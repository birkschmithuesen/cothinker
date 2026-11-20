import os
import logging
from queue import Queue, Empty
import wave
import datetime
import pathlib
from pathlib import Path
import time
from threading import Thread, Event, Lock
try:
    import sounddevice as sd
except OSError:
    # No PortAudio on this machine (the headless vServer). The server still
    # runs with a tcp: input (pcm_source.py); asking for a real device then
    # fails with a message that says why, instead of at import time.
    sd = None
import samplerate
import numpy as np

VOSK_RATE = 16000
_CHANNEL_IDX = {'left': 0, 'right': 1, 'regie': 0, 'audience': 1}

from fundusapps.stt_server.args import args
from fundusapps.stt_server.vad import VadChunker, MicGate
from fundusapps.stt_server.events import TranscriptionEvent, MicGateEvent
from fundusapps.stt_server import settings as runtime_settings
from fundusapps.common.turnid import format_latency_log
from fundusapps.stt_server.audio_clock import AudioClock, SpanRegistry, TimestampEnricher
from fundusapps.stt_server.vad_detectors import make_detector
from fundusapps.stt_server.thought_end import AudioRing, chain_enrich

# def list_audio_devices():
#     """List available audio devices."""
#     devices = sr.Microphone.list_microphone_names()
#     return devices


class AudioSource:
    """Owns a single sounddevice RawInputStream and fans captured frames
    out to multiple subscriber Queues. Required for ASIO devices which
    are exclusive per process — multiple recognizers must share one stream.
    """

    # A source counts as live only while frames keep arriving. Anything older is
    # reported as zero: a meter that freezes on its last value would lie in
    # exactly the situation it exists for (stream died, device unplugged).
    SIGNAL_TIMEOUT = 1.0

    def __init__(self, device, sample_rate, num_channels,
                 asio_channels=None, blocksize=4096):
        self.device = device
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        # Zero-based ASIO channel selectors (caller subtracts 1 from 1-indexed CLI input)
        self.asio_channels = list(asio_channels) if asio_channels is not None else None
        self.blocksize = blocksize
        self._subscribers = []
        self._stream = None
        self.level = 0.0            # peak of the last block, 0..1
        # RMS of the SAME block, 0..1. Kept separately from `level` on purpose:
        # the meter is an headroom display and wants peak, but the VAD decides
        # on RMS (vad.VadChunker._rms). For speech the two differ by the crest
        # factor — typically 3–10x — so a VAD threshold drawn against the peak
        # bar sits far too low and gets misread. The operator page draws its
        # threshold line against THIS value.
        self.level_rms = 0.0
        self.level_timestamp = None  # time.monotonic() of the last block
        # Bumped by a source whose stream can restart on its own (a new tcp:
        # client after the tap reconnected). The recognizer compares it per
        # frame and re-anchors its AudioClock epoch on a change, exactly as
        # after a resume or a device switch: the gap between two clients is
        # wall time the sample count never saw. A PortAudio stream never
        # bumps it.
        self.epoch_generation = 0

    def subscribe(self):
        q = Queue()
        self._subscribers.append(q)
        return q

    def _callback(self, indata, frames, time_, status):
        if status:
            logging.warning(f"AudioSource status: {status}")
        frame = bytes(indata)
        for q in list(self._subscribers):
            q.put((time_, frame))
        # Metering happens here because the frames pass through anyway. This runs
        # in the PortAudio thread, so it stays two numpy reductions over a view of
        # the existing buffer — no copy, no queue, no extra thread.
        samples = np.frombuffer(frame, dtype=np.int16)
        if samples.size:
            peak = max(int(samples.max()), -int(samples.min()))
            self.level = min(peak / 32768.0, 1.0)
            # Same block, second reduction. einsum accumulates the sum of
            # squares directly in int64 instead of materialising a float copy
            # of the block first — this is still the PortAudio thread, and the
            # rule from the comment above (no copy, no queue, no extra thread)
            # applies to the RMS exactly as it does to the peak.
            sum_sq = float(np.einsum("i,i->", samples, samples, dtype=np.int64))
            self.level_rms = min(
                (sum_sq / samples.size) ** 0.5 / 32768.0, 1.0
            )
            self.level_timestamp = time.monotonic()

    def level_snapshot(self, now=None):
        """Current input level plus whether frames are actually still arriving.

        Two numbers, and they mean different things: `level` is the peak of the
        last block (headroom), `level_rms` its RMS (what the VAD and the
        microphone gate compare against). Both are reported so the operator
        page can label them apart instead of drawing a threshold against
        whichever one happens to be handy.

        Deliberately independent of STTServer.pause(): pausing only makes the
        recognizer threads drop their frames, this stream keeps running. The
        operator wants to see whether the microphone delivers signal BEFORE
        switching recognition (and its per-utterance cost) back on.
        """
        now = time.monotonic() if now is None else now
        ts = self.level_timestamp
        age = None if ts is None else now - ts
        live = age is not None and age <= self.SIGNAL_TIMEOUT
        return {
            "level": round(self.level, 4) if live else 0.0,
            "level_rms": round(self.level_rms, 5) if live else 0.0,
            "signal": live,
            "age": None if age is None else round(age, 3),
        }

    def start(self):
        extra = None
        if self.asio_channels is not None:
            extra = sd.AsioSettings(channel_selectors=self.asio_channels)
        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            device=self.device,
            dtype="int16",
            channels=self.num_channels,
            callback=self._callback,
            extra_settings=extra,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class STTRecognizer(Thread):
    """Captures audio from one device/channel, resamples to 16 kHz, and
    delegates recognition to an injected RecognizerBackend.
    """

    def __init__(
        self,
        backend,                        # RecognizerBackend
        recognizer_id: str,             # "left" | "right" | "regie" | "audience" | "0" | ...
        audio_source,                   # AudioSource — required
        channel=None,
        blocksize=8000,
        debug_audio_folder=None,
        vad_min_silence_ms=500,
        vad_min_speech_ms=250,
        vad_energy_threshold=0.01,
        enricher=None,
        vad_kind="rms",
        silero_threshold=0.5,
        thought_end_tap=None,
    ):
        super().__init__(daemon=True)
        self.backend = backend
        self.recognizer_id = recognizer_id
        self.audio_source = audio_source
        self.device = audio_source.device
        self._stop_event = Event()
        self._mute = Event()

        self.channel_idx = _CHANNEL_IDX.get(channel) if channel is not None else None

        self.samplerate = audio_source.sample_rate
        self.blocksize = blocksize

        self.debug_audio_folder = (
            Path(debug_audio_folder) if debug_audio_folder is not None else None
        )

        self.vad_min_silence_ms = vad_min_silence_ms
        self.vad_min_speech_ms = vad_min_speech_ms
        self.vad_energy_threshold = vad_energy_threshold
        # Published by run() once built, so the operator can retune the VAD
        # threshold on the LIVE chunker. Stays None for streaming backends,
        # which have no chunker at all.
        self.chunker = None

        # Guards the audio_source/samplerate/audio_queue triple against a device
        # switch happening between two reads in run().
        self._source_lock = Lock()
        self._source_changed = Event()

        # Subscribe immediately so frames captured before run() starts are buffered.
        self.audio_queue = audio_source.subscribe()

        self.vad_kind = vad_kind
        self.silero_threshold = silero_threshold
        # Built HERE, on the main thread, not in run(): with --vad silero
        # this imports onnxruntime and loads the vendored model, and a
        # missing package has to fail at startup where somebody sees it --
        # not silently inside a daemon thread that then transcribes
        # nothing and looks like a quiet room.
        self.detector = make_detector(vad_kind, vad_energy_threshold,
                                      silero_threshold)
        #: Counts only the samples that really pass through run(), so a
        #: position on it is a sample position in the debug WAV.
        self.clock = AudioClock(VOSK_RATE)
        self.spans = SpanRegistry()
        self._was_muted = False
        if enricher is not None:
            enricher.register(recognizer_id, self.clock, self.spans)

        # Only with --thought-end: the last seconds of THIS recognizer's
        # audio, by clock position, so the tap can cut a turn's window
        # without a file. Stays None otherwise -- no memory, no copy.
        self.ring = None
        if thought_end_tap is not None:
            self.ring = AudioRing()
            thought_end_tap.register(recognizer_id, self.clock, self.ring)

    def stop(self):
        self._stop_event.set()

    def set_vad_energy_threshold(self, value):
        """Retune the VAD while this thread keeps running.

        Both assignments are single stores of a float and therefore atomic
        under the GIL — the audio thread reads either the old or the new value,
        never a half-written one, so no lock is needed here. Do NOT turn this
        into a multi-step mutation (read-modify-write, or two related values
        that must change together): that would need a lock, and taking one in
        the recognizer's hot path is exactly what this avoids.

        `self.vad_energy_threshold` is set as well as the chunker's own copy so
        a chunker built later — run() has not reached its constructor yet —
        starts from the current value rather than the startup one.
        """
        self.vad_energy_threshold = value
        chunker = self.chunker
        if chunker is not None:
            # Also moves the chunker's detector, which is this one.
            chunker.set_energy_threshold(value)
        else:
            # run() has not built the chunker yet; move the detector
            # directly so the value is already in force when it does.
            self.detector.set_threshold(value)

    def attach_source(self, source):
        """Re-point this recognizer at another AudioSource (runtime device switch).

        Subscribing yields a *fresh* queue, so frames still buffered from the old
        device are dropped rather than resampled with the new device's ratio.
        """
        q = source.subscribe()
        with self._source_lock:
            self.audio_source = source
            self.device = source.device
            self.samplerate = source.sample_rate
            self.audio_queue = q
            self._source_changed.set()

    def _make_resampler(self):
        if self.samplerate == VOSK_RATE:
            return None, 1.0
        return samplerate.Resampler("sinc_best", channels=1), VOSK_RATE / self.samplerate

    def run(self):
        wav_file = None
        chunker = None
        if self.debug_audio_folder is not None:
            self.debug_audio_folder.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            wav_path = (
                self.debug_audio_folder
                / f"{ts}_dev{self.device}_{self.recognizer_id}.wav"
            )
            wav_file = wave.open(str(wav_path), "wb")
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(VOSK_RATE)
            logging.info(f"[debug_audio] Recording post-resampled audio to: {wav_path}")

        try:
            resampler, ratio = self._make_resampler()
            streaming = self.backend.wants_streaming()
            last_turn_id_box = [None]
            if not streaming:
                def _on_speech_start():
                    self.backend._bus.publish(TranscriptionEvent(
                        recognizer_id=self.recognizer_id, type="partial", text="",
                        timestamp=time.time(), backend="whisper", status="speech_detected",
                    ))
                def _on_chunk_submitted(_audio, _turn_id):
                    last_turn_id_box[0] = _turn_id
                    self.backend._bus.publish(TranscriptionEvent(
                        recognizer_id=self.recognizer_id, type="partial", text="",
                        timestamp=time.time(), backend="whisper", status="transcribing",
                    ))
                chunker = VadChunker(
                    sample_rate=VOSK_RATE,
                    min_silence_ms=self.vad_min_silence_ms,
                    min_speech_ms=self.vad_min_speech_ms,
                    energy_threshold=self.vad_energy_threshold,
                    on_speech_start=_on_speech_start,
                    on_chunk_submitted=_on_chunk_submitted,
                    detector=self.detector,
                    span_registry=self.spans,
                )
                self.chunker = chunker
                # Re-apply: a /vad_threshold call that landed between the
                # constructor above and the publish just now would otherwise
                # have updated self.vad_energy_threshold and found no chunker.
                chunker.set_energy_threshold(self.vad_energy_threshold)
            seen_generation = getattr(self.audio_source, "epoch_generation", 0)
            while not self._stop_event.is_set():
                if self._source_changed.is_set():
                    # The new device may run at a different rate. Rebuilding the
                    # resampler is not optional: keeping the old one would leave
                    # recognition running at the wrong speed — it does not crash,
                    # it just recognises nonsense.
                    with self._source_lock:
                        self._source_changed.clear()
                        resampler, ratio = self._make_resampler()
                    # A switch drops whatever was queued from the old
                    # device, so there is a hole in wall-clock time that
                    # the sample count does not have. Re-anchor.
                    self.clock.reset_epoch()
                    logging.info(
                        f"[{self.recognizer_id}] switched to device {self.device} "
                        f"@ {self.samplerate} Hz (resampler rebuilt)"
                    )
                audio_queue = self.audio_queue
                if self._mute.is_set():
                    audio_queue.queue.clear()  # drop frames buffered while muted
                    self._was_muted = True
                    time.sleep(0.05)
                    continue
                if self._was_muted:
                    # Muted frames never reached the clock, so it stood
                    # still while the wall clock ran on. Without this,
                    # every utterance after the pause would report itself
                    # as having happened the length of the pause too
                    # early. The SAMPLE COUNT deliberately keeps running.
                    self._was_muted = False
                    self.clock.reset_epoch()
                try:
                    _time, data = audio_queue.get(timeout=0.2)
                except Empty:
                    continue
                generation = getattr(self.audio_source, "epoch_generation", 0)
                if generation != seen_generation:
                    # The source restarted its stream (a new tcp: client).
                    # Same reasoning as the resume branch above: re-anchor,
                    # keep the sample count.
                    seen_generation = generation
                    self.clock.reset_epoch()
                if self.channel_idx is not None:
                    data = np.ascontiguousarray(
                        np.frombuffer(data, dtype=np.int16)[self.channel_idx::2]
                    ).tobytes()
                if resampler is not None:
                    chunk = (
                        np.frombuffer(data, dtype=np.int16).astype(np.float32)
                        / 32768.0
                    )
                    data = (
                        (resampler.process(chunk, ratio) * 32768.0)
                        .astype(np.int16)
                        .tobytes()
                    )
                if wav_file is not None:
                    wav_file.writeframes(data)

                audio_np = np.frombuffer(data, dtype=np.int16)
                # The clock counts EXACTLY these samples: the ones written
                # to the debug WAV just above, and the ones handed on just
                # below. Nothing else ever gets here -- muted frames were
                # dropped, and un-resampled frames do not exist.
                offset = self.clock.advance(len(audio_np))
                if self.ring is not None:
                    # The same samples, the same offset: a ring position is
                    # a clock position, and t_start/t_end index into it.
                    self.ring.append(offset, data)
                if streaming:
                    if getattr(self.backend, "accepts_audio_offset", False):
                        ereignisse = self.backend.process_chunk(
                            audio_np, self.recognizer_id, audio_offset=offset)
                    else:
                        # vosk, whisper-streaming and the test fakes keep
                        # their three-argument signature untouched.
                        ereignisse = self.backend.process_chunk(
                            audio_np, self.recognizer_id)
                    for _ev in ereignisse:
                        pass
                else:
                    for chunk in chunker.feed(audio_np, start_sample=offset):
                        for _ev in self.backend.process_chunk(chunk, self.recognizer_id, last_turn_id_box[0]):
                            pass
            if chunker is not None:
                for chunk in chunker.flush():
                    for _ev in self.backend.process_chunk(chunk, self.recognizer_id, last_turn_id_box[0]):
                        pass
        finally:
            self.chunker = None
            if wav_file is not None:
                wav_file.close()


class STTServer:

    @staticmethod
    def get_audio_device_index(audio_device: str):
        from fundusapps.stt_server.pcm_source import is_tcp_device
        if is_tcp_device(audio_device):
            return audio_device.strip()
        if sd is None:
            raise RuntimeError(
                f"Audio device '{audio_device}' requested, but PortAudio is not "
                f"installed on this machine. Use --audio_devices tcp:127.0.0.1:5053 "
                f"with fundusapps.bbb_tap, or install libportaudio2."
            )
        devices = sd.query_devices()
        for idx, device in enumerate(devices):
            if audio_device in device["name"]:
                logging.info(f"Found audio device '{device['name']}' at index {idx}")
                return idx
        raise ValueError(
            f"Audio device '{audio_device}' not found. "
            f"Available devices: {[d['name'] for d in devices]}"
        )

    def __init__(
        self,
        audio_device_indexes,
        backend,                        # RecognizerBackend, already constructed
        event_bus,                      # EventBus
        enable_audio_logging=True,
        sample_rates=[44100],
        channels=None,
        asio_channels=None,             # NEW: 1-indexed list, e.g. [13, 14]
        chunk_size=4096,
        external_record_callback=None,
        external_stop_callback=None,
        external_partial_callback=None,
        debug_audio_folder=None,
        vad_min_silence_ms=500,
        vad_min_speech_ms=250,
        vad_energy_threshold=0.01,
        vad_kind="rms",
        silero_threshold=0.5,
        mic_gate_enabled=False,
        mic_gate_threshold=0.002,
        mic_gate_debounce_ms=1500,
        mic_gate_release_ratio=0.5,
        interview_signal_callback=None,
        thought_end_tap=None,
        **kwargs,
    ):
        self.audio_device_indexes = audio_device_indexes
        self.sample_rates = sample_rates
        self.channels = channels or []
        self.asio_channels = asio_channels  # None or list of 1-indexed ints
        self.backend = backend
        self.event_bus = event_bus
        self.debug_audio_folder = (
            Path(debug_audio_folder) if debug_audio_folder is not None else None
        )
        self.vad_min_silence_ms = vad_min_silence_ms
        self.vad_min_speech_ms = vad_min_speech_ms
        self.vad_energy_threshold = vad_energy_threshold
        self.vad_kind = vad_kind
        self.silero_threshold = silero_threshold
        # One enricher for the whole server, dispatching on
        # recognizer_id: two recognizers on one bus have two clocks and
        # two registries, and crossing them would put one speaker's
        # timestamps on the other's words.
        self.enricher = TimestampEnricher()
        # With --thought-end the tap rides on the same hook, AFTER the
        # enricher: it reads the span the enricher has just filled in.
        self.thought_end_tap = thought_end_tap
        event_bus.set_enrich(self.enricher if thought_end_tap is None
                             else chain_enrich(self.enricher, thought_end_tap))

        # Off unless asked for: with the gate on, the input level decides
        # whether recognition runs. That is a large change in behaviour for
        # every existing production (chairs, finale), and none of them has a
        # microphone with a switch — so it must not arrive by default.
        self.mic_gate = (
            MicGate(
                threshold=mic_gate_threshold,
                debounce_ms=mic_gate_debounce_ms,
                release_ratio=mic_gate_release_ratio,
            )
            if mic_gate_enabled
            else None
        )
        # Called with (mic_on: bool) on every confirmed change, always from a
        # throwaway thread. None = no receiver configured; the gate then still
        # switches recognition and shows the state, it just sends nothing.
        self.interview_signal_callback = interview_signal_callback
        self._gate_thread = None
        self._gate_stop = Event()

        self.set_external_record_callback(external_record_callback)
        self.set_external_stop_callback(external_stop_callback)
        self.set_external_partial_callback(external_partial_callback)

        self.chunk_size = chunk_size
        self.status = "initialized"

        self.enable_audio_logging = enable_audio_logging
        self.audio_log_folder = Path(args.log_folder, "audio")
        if self.enable_audio_logging:
            pathlib.Path(self.audio_log_folder).mkdir(parents=True, exist_ok=True)
            logging.info(f"Audio logging enabled. Saving to: {self.audio_log_folder}")

        self.threads = []
        self._sources = {}  # (device_idx, sample_rate) -> AudioSource
        # Serializes runtime device switches against each other and against
        # readers of _sources (the /levels and /audio_devices endpoints).
        self._device_lock = Lock()
        self._dispatch_thread = None
        self._dispatch_stop = Event()
        self._bus_subscriber = None

    def __enter__(self):
        # Group recognizers by (device, sample_rate). One AudioSource per group.
        groups = {}  # key=(device_idx, sample_rate) -> list of (j, channel)
        for j, _ in (enumerate(self.channels) if self.channels else enumerate(self.audio_device_indexes)):
            dev = (self.audio_device_indexes[j] if j < len(self.audio_device_indexes)
                   else self.audio_device_indexes[0])
            sr = (self.sample_rates[j] if j < len(self.sample_rates)
                  else self.sample_rates[0])
            channel = self.channels[j] if (self.channels and j < len(self.channels)) else None
            groups.setdefault((dev, sr), []).append((j, channel))

        for (dev, sr), members in groups.items():
            if self.asio_channels is not None:
                num_channels = len(self.asio_channels)
                asio = [c - 1 for c in self.asio_channels]
            else:
                num_channels = 2 if any(ch in _CHANNEL_IDX for _, ch in members) else 1
                asio = None
            source = self._create_source(dev, sr, num_channels, asio)
            self._sources[(dev, sr)] = source
            source.start()

            for j, channel in members:
                recognizer_id = channel if channel else str(j)
                self.threads.append(
                    self._make_recognizer(recognizer_id, source, channel)
                )
                self.threads[-1].start()

        self._bus_subscriber = self.event_bus.subscribe()
        self._dispatch_stop.clear()
        self._dispatch_thread = Thread(target=self._dispatch_events_to_callbacks, daemon=True)
        self._dispatch_thread.start()

        if self.mic_gate is not None:
            self._gate_stop.clear()
            self._gate_thread = Thread(target=self._run_mic_gate, daemon=True)
            self._gate_thread.start()
            logging.info(
                f"Microphone gate active: threshold={self.mic_gate.threshold} (RMS), "
                f"release={self.mic_gate.release_threshold}, "
                f"debounce={self.mic_gate.debounce_ms} ms."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        self._gate_stop.set()
        if self._gate_thread is not None:
            self._gate_thread.join(timeout=1.0)
        self._dispatch_stop.set()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=1.0)
        if self._bus_subscriber is not None:
            self.event_bus.unsubscribe(self._bus_subscriber)
        for source in self._sources.values():
            source.stop()
        self._sources.clear()
        if self.backend is not None:
            self.backend.close()
        return None

    def start(self):
        logging.info("Starting STT server recording.")
        for thread in self.threads:
            if not thread.is_alive():
                thread.start()
        self.status = "running"

    def stop(self):
        logging.info("Stopping STT server recording.")
        for thread in self.threads:
            thread.stop()
        for thread in self.threads:
            thread.join(timeout=5)
        self.status = "stopped"

    def pause(self):
        logging.info("Pausing STT server.")
        for thread in self.threads:
            thread._mute.set()
        self.status = "paused"

    def resume(self):
        logging.info("Resuming STT server.")
        for thread in self.threads:
            thread._mute.clear()
        self.status = "running"

    # --- audio source introspection / runtime device switching ---------------

    def _make_recognizer(self, recognizer_id, source, channel):
        """Single place where an STTRecognizer is constructed.

        Every VAD knob is read from `self` here, not from the startup
        arguments, so a recognizer built after the operator moved the
        threshold starts with the value that is currently in force.
        """
        return STTRecognizer(
            backend=self.backend,
            recognizer_id=recognizer_id,
            audio_source=source,
            channel=channel,
            blocksize=self.chunk_size,
            debug_audio_folder=self.debug_audio_folder,
            vad_min_silence_ms=self.vad_min_silence_ms,
            vad_min_speech_ms=self.vad_min_speech_ms,
            vad_energy_threshold=self.vad_energy_threshold,
            enricher=self.enricher,
            vad_kind=self.vad_kind,
            silero_threshold=self.silero_threshold,
            thought_end_tap=self.thought_end_tap,
        )

    # --- runtime VAD tuning --------------------------------------------------

    # The three validators live in settings.py, because the settings FILE has to
    # be checked with exactly the same rules as an HTTP body: a hand-edited file
    # is no more trustworthy than a POST. Kept as static methods here so callers
    # (and the tests) keep their existing entry point.
    validate_threshold = staticmethod(runtime_settings.validate_threshold)
    validate_release_ratio = staticmethod(runtime_settings.validate_release_ratio)
    validate_debounce_ms = staticmethod(runtime_settings.validate_debounce_ms)

    def set_vad_energy_threshold(self, value):
        """Move the VAD's speech/silence threshold at runtime.

        Written in two places on purpose: onto every running chunker (takes
        effect on the next audio frame) AND onto the server itself. Only the
        latter is inherited by a recognizer created later — leave it out and
        the threshold silently jumps back to the startup value the next time a
        recognizer is rebuilt.
        """
        v = self.validate_threshold(value, "vad_energy_threshold")
        self.vad_energy_threshold = v
        for thread in list(self.threads):
            thread.set_vad_energy_threshold(v)
        logging.info(f"VAD energy threshold set to {v} (RMS, 0..1).")
        return v

    # --- microphone switch ---------------------------------------------------

    # 50 ms is a twentieth of the default debounce, so the confirmed instant is
    # accurate to well under the tolerance the debounce already imposes. It also
    # keeps the gate off the PortAudio callback: the level is read from the
    # source instead of computed in it, so a slow moment here can delay the
    # signal but can never drop an audio block.
    MIC_GATE_POLL_S = 0.05

    def current_input_rms(self, now=None):
        """Loudest RMS across the capture sources, 0.0 when nothing arrives.

        Loudest, not the sum: two capture sources must not pull each other's
        gate shut. A source whose frames have stopped reports 0.0 through
        level_snapshot(), which is what turns an unplugged interface into a
        switched-off microphone — the honest reading, and the debounce keeps a
        device switch from tripping it.

        Known limit, worth knowing before wiring this to a second microphone:
        `AudioSource.level_rms` is measured over the whole interleaved block,
        so on a stereo source (the Fireface setup with regie+audience on one
        ASIO stream) the gate sees the two channels BLENDED and cannot tell
        which of them went quiet. The installation this was built for has one
        microphone on one source, where blend and channel are the same thing.
        Per-recognizer gating would need per-channel metering in AudioSource —
        cheap to add (strided views, no copy), deliberately not added here
        because nothing needs it yet.
        """
        with self._device_lock:
            sources = list(self._sources.values())
        if not sources:
            return 0.0
        return max(s.level_snapshot(now=now)["level_rms"] for s in sources)

    def _run_mic_gate(self):
        while not self._gate_stop.wait(self.MIC_GATE_POLL_S):
            try:
                self.poll_mic_gate()
            except Exception as e:
                logging.error(f"Microphone gate poll failed: {e}")

    def poll_mic_gate(self, now=None):
        """One gate step. Public so tests can drive it with their own clock."""
        if self.mic_gate is None:
            return None
        now = time.monotonic() if now is None else now
        level = self.current_input_rms(now=now)
        changed = self.mic_gate.feed(level, now)
        if changed is not None:
            self._apply_mic_gate(changed, level)
        return changed

    def _apply_mic_gate(self, mic_on, level):
        logging.info(
            f"Microphone switch went {'ON' if mic_on else 'OFF'} "
            f"(rms={level:.5f}, threshold={self.mic_gate.threshold})."
        )
        # 1. Recognition follows the switch — via pause()/resume(), NOT by
        #    closing the stream. Pausing only mutes the recognizer threads, so
        #    /levels keeps reporting and the operator can see the microphone
        #    deliver signal again before recognition comes back.
        #    Note this acts on transitions only: an operator who presses
        #    "Erkennung an" while the microphone is off keeps recognition
        #    running until the switch is next moved. That is deliberate — the
        #    buttons must not become unusable.
        if mic_on:
            self.resume()
        else:
            self.pause()

        # 2. Tell the operator page.
        self.event_bus.publish(MicGateEvent(
            mic_on=mic_on,
            level_rms=round(level, 5),
            threshold=self.mic_gate.threshold,
            timestamp=time.time(),
        ))

        # 3. Tell whoever collects the interviews. In its own thread, like
        #    every other external callback in this file: this is an HTTP call
        #    over the exhibition LAN, and a dead peer must not stall the gate.
        callback = self.interview_signal_callback
        if callback is not None:
            Thread(
                target=self._send_interview_signal,
                args=(callback, mic_on),
                daemon=True,
            ).start()

    @staticmethod
    def _send_interview_signal(callback, mic_on):
        try:
            callback(mic_on)
        except Exception as e:
            logging.error(f"Interview signal callback failed: {e}")

    def describe_mic_gate(self):
        if self.mic_gate is None:
            return {"enabled": False}
        return self.mic_gate.describe()

    def configure_mic_gate(self, value=None, debounce_ms=None, release_ratio=None):
        """Retune the gate at runtime. Validates everything before it changes
        anything, so a rejected field cannot leave the gate half-adjusted."""
        if self.mic_gate is None:
            raise ValueError(
                "microphone gate is not enabled; start the server with --mic-gate"
            )
        if value is None and debounce_ms is None and release_ratio is None:
            raise ValueError(
                "give at least one of value, debounce_ms, release_ratio"
            )
        threshold = (
            None if value is None
            else self.validate_threshold(value, "mic_gate_threshold")
        )
        if debounce_ms is not None:
            debounce_ms = self.validate_debounce_ms(debounce_ms, "debounce_ms")
        if release_ratio is not None:
            # Exactly 1.0 would remove the hysteresis and make the gate chatter
            # around the threshold; 0 would make it never release.
            release_ratio = self.validate_release_ratio(release_ratio, "release_ratio")

        if threshold is not None:
            self.mic_gate.set_threshold(threshold)
        if debounce_ms is not None:
            self.mic_gate.set_debounce_ms(debounce_ms)
        if release_ratio is not None:
            self.mic_gate.set_release_ratio(release_ratio)
        logging.info(f"Microphone gate reconfigured: {self.mic_gate.describe()}")
        return self.mic_gate.describe()

    def _create_source(self, device, sample_rate, num_channels, asio_channels):
        """Single place where an AudioSource is constructed.

        Used by __enter__ and by set_audio_device; tests override it to run the
        switching logic without any audio hardware.
        """
        from fundusapps.stt_server.pcm_source import PcmTcpSource, is_tcp_device
        if is_tcp_device(device):
            return PcmTcpSource(device=device, sample_rate=sample_rate,
                                num_channels=num_channels,
                                blocksize=self.chunk_size)
        return AudioSource(
            device=device,
            sample_rate=sample_rate,
            num_channels=num_channels,
            asio_channels=asio_channels,
            blocksize=self.chunk_size,
        )

    @staticmethod
    def _device_info(index):
        try:
            return sd.query_devices(index)
        except Exception as e:
            logging.debug(f"query_devices({index}) failed: {e}")
            return None

    @staticmethod
    def _hostapi_name(info):
        if not info:
            return None
        try:
            return sd.query_hostapis()[info["hostapi"]]["name"]
        except Exception:
            return None

    @classmethod
    def resolve_device(cls, device):
        """Accept an index (int or numeric string) or a device-name substring."""
        from fundusapps.stt_server.pcm_source import is_tcp_device
        if is_tcp_device(device):
            return device.strip()
        if isinstance(device, int):
            return device
        text = str(device).strip()
        if text.isdigit():
            return int(text)
        return cls.get_audio_device_index(text)

    def describe_source(self, source):
        info = self._device_info(source.device)
        return {
            "device": source.device,
            "device_name": info["name"] if info else str(source.device),
            "hostapi": self._hostapi_name(info),
            "sample_rate": source.sample_rate,
            "num_channels": source.num_channels,
            "asio_channels": source.asio_channels,
            "recognizers": [
                t.recognizer_id for t in self.threads if t.audio_source is source
            ],
            # Reported next to the levels because that is where it is read: the
            # operator page draws it as a line into this source's RMS bar.
            "vad_energy_threshold": self.vad_energy_threshold,
            **source.level_snapshot(),
        }

    def describe_sources(self):
        with self._device_lock:
            sources = list(self._sources.values())
        return [self.describe_source(s) for s in sources]

    def list_input_devices(self):
        """All devices with at least one input channel, active one marked."""
        devices = []
        if sd is None:
            # No PortAudio here; a tcp: source has no device list to offer.
            # The operator page then shows an empty picker instead of a 500.
            return devices
        with self._device_lock:
            active = {key[0] for key in self._sources}
        for idx, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) <= 0:
                continue
            devices.append({
                "index": idx,
                "name": d["name"],
                "hostapi": self._hostapi_name(d),
                "max_input_channels": d["max_input_channels"],
                "default_samplerate": int(d.get("default_samplerate") or 0),
                "active": idx in active,
            })
        return devices

    def set_audio_device(self, device, sample_rate=None):
        """Move the running capture to another input device.

        Only the AudioSource is replaced. The recognizer threads keep running and
        the backend — for elevenlabs-scribe an open websocket that is expensive to
        rebuild — is never touched. The new stream is opened BEFORE the old one is
        stopped, so a device that cannot be opened (busy, gone, wrong rate) raises
        and leaves recognition running exactly as it was.
        """
        with self._device_lock:
            if len(self._sources) != 1:
                raise RuntimeError(
                    f"Runtime device switching supports a single capture source, "
                    f"but {len(self._sources)} are active. Restart the server with "
                    f"the desired --audio_devices instead."
                )
            (old_key, old_source), = self._sources.items()
            new_index = self.resolve_device(device)
            new_rate = int(sample_rate) if sample_rate else old_source.sample_rate

            info = self._device_info(new_index)
            if info is None:
                raise ValueError(f"Audio device {new_index} does not exist.")
            if info.get("max_input_channels", 0) < old_source.num_channels:
                raise ValueError(
                    f"Device '{info['name']}' offers {info['max_input_channels']} input "
                    f"channel(s), but the running recognizers need {old_source.num_channels}."
                )
            if old_source.asio_channels is not None and self._hostapi_name(info) != "ASIO":
                # The ASIO setup reads specific physical inputs (13/14 on the
                # Fireface). Silently dropping those selectors would keep running
                # but listen to the wrong inputs, so refuse instead.
                raise ValueError(
                    f"Current capture uses ASIO channel selectors "
                    f"{old_source.asio_channels}; '{info['name']}' is not an ASIO "
                    f"device. Switch via the CLI flags instead."
                )
            if (new_index, new_rate) == old_key:
                return self.describe_source(old_source)

            new_source = self._create_source(
                new_index, new_rate, old_source.num_channels, old_source.asio_channels
            )
            new_source.start()  # raises before anything is torn down

            for thread in [t for t in self.threads if t.audio_source is old_source]:
                thread.attach_source(new_source)
            self._sources.pop(old_key, None)
            self._sources[(new_index, new_rate)] = new_source
            try:
                old_source.stop()
            except Exception as e:
                # Recognizers already read from the new source; a stubborn old
                # stream must not turn a successful switch into a failure.
                logging.error(f"Could not close previous audio stream: {e}")

            # Single source means every recognizer moved — keep the startup config
            # in sync so later readers don't report the device we left behind.
            self.audio_device_indexes = [new_index for _ in self.audio_device_indexes]
            self.sample_rates = [new_rate for _ in self.sample_rates]
            logging.info(
                f"Switched capture to device {new_index} "
                f"('{info['name']}') @ {new_rate} Hz."
            )
            return self.describe_source(new_source)

    def set_external_record_callback(self, callback):
        if callback is None:
            self.external_record_callback = None
            return
        if not callable(callback):
            raise ValueError("Callback must be a callable function.")
        self.external_record_callback = callback

    def set_external_stop_callback(self, callback):
        if callback is None:
            self.external_stop_callback = None
            return
        if not callable(callback):
            raise ValueError("Callback must be a callable function.")
        self.external_stop_callback = callback

    def set_external_partial_callback(self, callback):
        if callback is None:
            self.external_partial_callback = None
            return
        if not callable(callback):
            raise ValueError("Callback must be a callable function.")
        self.external_partial_callback = callback

    def _dispatch_events_to_callbacks(self):
        """Subscribe to the event bus, route final → external_record_callback,
        partial → external_partial_callback. Both callbacks receive
        recognizer_index (int) derived from STTRecognizer creation order.
        """
        id_to_idx = {t.recognizer_id: i for i, t in enumerate(self.threads)}
        while not self._dispatch_stop.is_set():
            try:
                ev = self._bus_subscriber.get(timeout=0.2)
            except Empty:
                continue
            if not isinstance(ev, TranscriptionEvent):
                # The bus also carries MicGateEvent, which has no text, no turn
                # and no recognizer. Without this guard the `ev.text` below
                # raises, the exception leaves the while loop, and this thread
                # dies — taking every later botland callback with it, silently.
                continue
            if not ev.text.strip():
                continue
            idx = id_to_idx.get(ev.recognizer_id, 0)
            turn_id = ev.turn_id

            if ev.type == "final":
                logging.info(
                    f"Dispatching final transcription from recognizer "
                    f"'{ev.recognizer_id}' (index {idx}): {ev.text}"
                )
                if turn_id:
                    logging.info(format_latency_log(
                        turn_id, "stt_final", time.time() * 1000,
                        recognizer=ev.recognizer_id, chars=len(ev.text),
                    ))
                if self.external_record_callback:
                    try:
                        Thread(
                            target=self.external_record_callback,
                            args=(idx, ev.text, turn_id),
                            daemon=True,
                        ).start()
                    except Exception as e:
                        logging.error(f"Error in external record callback: {e}")

            elif ev.type == "partial":
                logging.debug(
                    f"[partial] turn={turn_id} rec={idx} seq={ev.partial_seq} "
                    f"extending={ev.extending} text=\"{ev.text}\""
                )
                # Backends that revise partial text mid-utterance (elevenlabs-scribe)
                # set `extending=False` on revisions: the operator UI sees them via
                # the bus, but the botland callback skips them so its delta-suffix
                # calc stays correct. Legacy backends leave `extending=None` and
                # behave as before.
                if ev.extending is False:
                    continue
                if self.external_partial_callback:
                    try:
                        Thread(
                            target=self.external_partial_callback,
                            args=(idx, ev.text, turn_id, ev.partial_seq),
                            daemon=True,
                        ).start()
                    except Exception as e:
                        logging.error(f"Error in external partial callback: {e}")

    # def _save_audio_to_file(self, audio_data):
    #     """Save audio data to a WAV file."""
    #     if not self.enable_audio_logging:
    #         return None
    #
    #     timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    #     filename = os.path.join(self.audio_log_folder, f"audio_{timestamp}.wav")
    #
    #     try:
    #         # Get raw audio data
    #         raw_data = audio_data.get_raw_data()
    #
    #         # Write to WAV file
    #         with wave.open(filename, 'wb') as wf:
    #             wf.setnchannels(1)  # Mono
    #             wf.setsampwidth(2)  # 16-bit
    #             wf.setframerate(self.sample_rate)  # Use the same sample rate
    #             wf.writeframes(raw_data)
    #
    #         logging.info(f"Saved audio to: {filename}")
    #         return filename
    #     except Exception as e:
    #         logging.error(f"Error saving audio file: {e}")
    #         return None

    # def _record_callback(self, recognizer, audio):
    # #     audio_file = self._save_audio_to_file(audio)
    #
    #     # Save audio to file if logging is enabled
    #     logging.info("Received audio input, processing...")
    #     audio_file = self._save_audio_to_file(audio)
    #
    #     try:
    #         start_time = time.time()
    #
    #         if args.backend == "whisper":
    #             logging.info(f"Recognizing audio using Whisper model '{args.model}'...")
    #             text = recognizer.recognize_whisper(audio, model=args.model)
    #         elif args.backend == "vosk":
    #             logging.info(f"Recognizing audio using Vosk model '{args.model}'...")
    #             text = recognizer.recognize_vosk(audio, model=args.model)
    #         else:
    #             logging.warning(f"Unknown backend '{args.backend}'")
    #             exit(1)
    #
    #         recognition_time = time.time() - start_time
    #         logging.info(f"Recognition took {recognition_time:.2f} seconds. Model used: {args.model}")
    #
    #     except Exception as e:
    #         logging.error(f"Error recognizing audio: {e}")
    #         return
    #
    #     text = text.strip()
    #     if text not in ["", " ", "\n", None]:
    #         logging.info(f"Recognized text: {text}")
    #         if audio_file:
    #             logging.info(f"Audio file: {audio_file}")
    #         self._text_queue.put(text)

    #         if self.external_record_callback:
    #             try:
    #                 self.external_record_callback(self)
    #             except Exception as e:
    #                 logging.error(f"Error in external record callback: {e}")


    # def start_recording(self):
    #     """Start recording audio input."""
    #     if self.recording:
    #         logging.warning("Already recording. No action taken.")
    #         return

    #     self.recording = True
    #     logging.info("Started recording audio input.")
    #
    #     # Start listening in the background
    #     self.start_listening()

    # def stop_recording(self):
    #     """Stop recording audio input."""
    #     if not self.recording:
    #         logging.warning("Not currently recording. No action taken.")
    #     self.recording = False
    #     logging.info("Stopped recording audio input.")

    # def start_listening(self):
    #     if self.stop_listening_callback is not None:
    #         logging.warning("Already listening. No action taken.")
    #         return

    # def stop_listening(self):
    #     if self.stop_listening is None:
    #         logging.warning("Not currently listening. No action taken.")
    #         return
