import logging
import time
from typing import Callable, Optional
import numpy as np

from fundusapps.common.turnid import format_latency_log, ulid_now
from fundusapps.stt_server.vad_detectors import RmsDetector, rms as _rms_of


class VadChunker:
    """Energy-threshold VAD that buffers speech between silence boundaries.

    Call .feed(frame) with each new int16 mono frame at `sample_rate`.
    It yields zero or one numpy int16 chunks per call: a chunk is emitted
    when speech has ended (silence longer than min_silence_ms) and the
    captured speech is at least min_speech_ms long.

    On every successful finalize the chunker generates a fresh ULID
    (`turn_id`), logs `[LATENCY phase=vad_speech_end ...]`, and passes
    (chunk, turn_id) to on_chunk_submitted so downstream layers can
    propagate the same id.

    With a `span_registry` it also records, under that same turn_id, the
    first and the last sample of SPEECH in the finalized chunk -- refined
    to 10 ms sub-windows by the detector, because the frames it is fed are
    ~93 ms long and a frame-granular timestamp cannot carry a 50 ms
    promise. The speech/silence DECISION is untouched by that refinement.
    """

    def __init__(
        self,
        sample_rate: int,
        min_silence_ms: int,
        min_speech_ms: int,
        energy_threshold: float,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_chunk_submitted: Optional[Callable[[np.ndarray, str], None]] = None,
        detector=None,
        span_registry=None,
    ):
        self.sample_rate = sample_rate
        self.min_silence_samples = int(sample_rate * min_silence_ms / 1000)
        self.min_speech_samples = int(sample_rate * min_speech_ms / 1000)
        # Kept as a mirror of the detector's threshold: the operator page
        # and /levels read it, and tests pin it. The DECISION reads the
        # detector, never this. Two single-float stores, neither torn --
        # a reader can see the old detector value beside the new mirror
        # for one frame, which is a threshold nudge arriving 93 ms late.
        self.energy_threshold = energy_threshold
        self.on_speech_start = on_speech_start
        self.on_chunk_submitted = on_chunk_submitted
        self.detector = detector if detector is not None else RmsDetector(
            energy_threshold)
        #: Optional audio_clock.SpanRegistry. None -> no spans are kept,
        #: which is exactly what every existing caller and test does.
        self.span_registry = span_registry

        self._in_speech = False
        self._buffer = []          # list of int16 numpy arrays
        self._buffered_samples = 0
        self._silence_run = 0      # consecutive silent samples while in speech
        self._current_turn_id: Optional[str] = None
        self._samples_fed = 0      # audio-clock position of the next frame
        self._speech_start_sample: Optional[int] = None
        self._speech_end_sample: Optional[int] = None

    def set_energy_threshold(self, value: float) -> None:
        """Retune the speech/silence decision while feed() is running.

        This makes two stores: `self.energy_threshold` below (the UI
        mirror -- see the note on it in `__init__`) and
        `self.detector.set_threshold()` (what the decision actually
        reads). No lock guards the pair, and none is needed: each store
        is a single float, atomic under the GIL, so the recognizer thread
        inside feed() reads either the old or the new value for each,
        never a torn one -- and since the speech/silence DECISION only
        ever reads `self.detector`, a reader catching the old UI mirror
        beside the new detector value for one frame is a threshold nudge
        arriving 93 ms late, not a torn read of the value the decision is
        made from. A future change that needs two fields to move
        together atomically (say a threshold plus a hold time) would need
        a lock; the cost of taking one per audio frame is what this
        avoids.
        """
        self.energy_threshold = float(value)
        self.detector.set_threshold(float(value))

    def _rms(self, frame: np.ndarray) -> float:
        """Kept as a method: tests/test_vad_threshold.py compares it
        against AudioSource.level_rms, and that equality is the reason the
        operator page can draw the threshold line where it does."""
        return _rms_of(frame)

    def feed(self, frame: np.ndarray, start_sample: Optional[int] = None) -> list:
        """One frame in, zero or one chunks out -- unchanged.

        `start_sample` is the frame's position on the recognizer's audio
        clock. Optional, because every existing caller passes one
        argument: without it the chunker counts its own samples, which is
        the same number as long as it is fed contiguously.
        """
        if frame.dtype != np.int16:
            frame = frame.astype(np.int16)
        if start_sample is None:
            start_sample = self._samples_fed
        self._samples_fed = start_sample + len(frame)

        detection = self.detector.analyse(frame, start_sample)
        is_speech = detection.is_speech

        out = []
        if self._in_speech:
            self._buffer.append(frame)
            self._buffered_samples += len(frame)
            if is_speech:
                self._silence_run = 0
                if detection.last is not None:
                    self._speech_end_sample = detection.last
            else:
                self._silence_run += len(frame)
                if self._silence_run >= self.min_silence_samples:
                    chunk = self._finalize()
                    if chunk is not None:
                        out.append(chunk)
        else:
            if is_speech:
                self._in_speech = True
                self._silence_run = 0
                self._buffer = [frame]
                self._buffered_samples = len(frame)
                self._speech_start_sample = (
                    detection.first if detection.first is not None
                    else start_sample)
                self._speech_end_sample = (
                    detection.last if detection.last is not None
                    else start_sample + len(frame))
                if self.on_speech_start:
                    self.on_speech_start()

        return out

    def _finalize(self) -> Optional[np.ndarray]:
        chunk = np.concatenate(self._buffer) if self._buffer else None
        silence_run = self._silence_run
        span = (self._speech_start_sample, self._speech_end_sample)
        self._buffer = []
        self._buffered_samples = 0
        self._silence_run = 0
        self._in_speech = False
        self._speech_start_sample = None
        self._speech_end_sample = None
        if chunk is None:
            return None
        speech_samples = len(chunk) - silence_run
        if speech_samples < self.min_speech_samples:
            return None
        turn_id = ulid_now()
        self._current_turn_id = turn_id
        logging.info(format_latency_log(
            turn_id,
            "vad_speech_end",
            time.time() * 1000,
        ))
        # BEFORE on_chunk_submitted: that callback publishes a status
        # event, and a segmented backend may finish and publish its final
        # from another thread at any moment afterwards. The span has to be
        # findable by then or the enricher sees an empty registry.
        if self.span_registry is not None and span[0] is not None:
            self.span_registry.record(turn_id, span[0], span[1])
        if self.on_chunk_submitted:
            self.on_chunk_submitted(chunk, turn_id)
        return chunk

    def flush(self) -> list:
        out = []
        if self._in_speech:
            chunk = self._finalize()
            if chunk is not None:
                out.append(chunk)
        return out

    def current_turn_id(self) -> Optional[str]:
        """Most recently generated turn_id (used by sr.py to label backend events)."""
        return self._current_turn_id


class MicGate:
    """Is the physical switch on the microphone on or off?

    A second threshold, deliberately NOT the VAD's. The VAD separates speech
    from pauses inside a running conversation; this separates "microphone
    connected and switched on" from "microphone switched off". Someone who
    says nothing for ten seconds is not a switched-off microphone, and a
    single threshold cannot mean both things at once.

    Fed with the RMS level of the input (`AudioSource.level_rms`), on the same
    0..1 scale the VAD threshold uses. Two guards, both mandatory:

    * **Hysteresis.** ON above `threshold`, OFF only below
      `threshold * release_ratio` (default half). A level parked near the
      threshold — which is exactly where a quiet room sits — would otherwise
      flip on every block.
    * **Debounce.** A candidate state has to hold for `debounce_ms` before it
      counts. Without it every cough and every pause between two sentences is
      an interview boundary.

    `feed()` returns None while nothing is confirmed, and the new state (True
    or False) on the block where a change becomes official — so a caller can
    act on it exactly once.

    The gate starts in the OFF state and reports transitions only, never its
    initial state: a server that boots while the microphone is already on
    emits one ON as soon as the debounce is satisfied, and a server that boots
    into a silent room emits nothing at all and leaves recognition exactly as
    the operator set it.
    """

    def __init__(
        self,
        threshold: float = 0.002,
        debounce_ms: int = 1500,
        release_ratio: float = 0.5,
    ):
        self.threshold = float(threshold)
        self.release_ratio = float(release_ratio)
        self.debounce_ms = int(debounce_ms)
        self.mic_on = False
        self._candidate: Optional[bool] = None
        self._candidate_since: Optional[float] = None

    @property
    def release_threshold(self) -> float:
        return self.threshold * self.release_ratio

    @property
    def debounce_s(self) -> float:
        return self.debounce_ms / 1000.0

    def set_threshold(self, value: float) -> None:
        self.threshold = float(value)
        self._candidate = None
        self._candidate_since = None

    def set_release_ratio(self, value: float) -> None:
        self.release_ratio = float(value)
        self._candidate = None
        self._candidate_since = None

    def set_debounce_ms(self, value: int) -> None:
        self.debounce_ms = int(value)
        self._candidate = None
        self._candidate_since = None

    def feed(self, level_rms: float, now: float) -> Optional[bool]:
        """One level sample. Returns the new state on a confirmed change."""
        if level_rms > self.threshold:
            target = True
        elif level_rms < self.release_threshold:
            target = False
        else:
            # The hysteresis band. Neither switching on nor off — whatever the
            # gate currently holds stays, and any pending change is dropped.
            target = self.mic_on

        if target == self.mic_on:
            self._candidate = None
            self._candidate_since = None
            return None

        if self._candidate is not target:
            self._candidate = target
            self._candidate_since = now
            return None

        if now - self._candidate_since >= self.debounce_s:
            self.mic_on = target
            self._candidate = None
            self._candidate_since = None
            return target
        return None

    def describe(self) -> dict:
        return {
            "enabled": True,
            "mic_on": self.mic_on,
            "threshold": self.threshold,
            "release_ratio": self.release_ratio,
            "release_threshold": self.release_threshold,
            "debounce_ms": self.debounce_ms,
            "pending": self._candidate,
        }
