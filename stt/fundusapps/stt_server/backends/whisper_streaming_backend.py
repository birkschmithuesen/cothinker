"""Streaming Whisper backend using LocalAgreement-2 confirmation.

Wraps whisper_online.OnlineASRProcessor (ufal/whisper_streaming). Emits
`partial` TranscriptionEvents as the confirmed prefix grows and one `final`
on end-of-utterance. All events from a single utterance share a turn_id;
partials additionally carry a monotonic partial_seq (per recognizer).

Threading: one shared worker thread per backend instance — same pattern as
WhisperBackend — fed by a per-recognizer chunk queue. This keeps the audio
thread free; inference happens off the audio callback.
"""
import logging
import queue
import threading
import time
from typing import Callable, Iterator, Optional

import numpy as np

from fundusapps.stt_server.backends.base import RecognizerBackend
from fundusapps.stt_server.backends.whisper_backend import _register_cudnn_dll_dir
from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.common.turnid import ulid_now


_register_cudnn_dll_dir()

WHISPER_RATE = 16000
SILENCE_TIMEOUT_S = 0.7  # gap of silence that closes an utterance


def _default_asr_factory(model: str, device: str, compute_type: str, language: str):
    """Build the real OnlineASRProcessor. Imported lazily so tests with stubs
    don't need faster_whisper/whisper_online installed.

    Tries the system-installed ``whisper_online`` (from the pip
    ``whisper-streaming`` package) first; if that's unavailable — typical on
    Windows, where the package's transitive deps (pyalsaaudio,
    opus-fast-mosestokenizer) don't build — falls back to the vendored copy
    under ``_vendored_whisper_streaming/``. Raises ImportError with both
    attempts described if neither resolves.
    """
    try:
        from whisper_online import FasterWhisperASR, OnlineASRProcessor  # type: ignore
    except ImportError:
        try:
            from fundusapps.stt_server.backends._vendored_whisper_streaming.whisper_online import (  # type: ignore
                FasterWhisperASR,
                OnlineASRProcessor,
            )
        except ImportError as e:
            raise ImportError(
                "whisper-streaming backend requires whisper_online. Tried both the "
                "system 'whisper-streaming' package and the vendored copy at "
                "fundusapps/stt_server/backends/_vendored_whisper_streaming/. "
                "Install with: pip install whisper-streaming, or check the vendored "
                "copy isn't missing transitive deps (faster-whisper, librosa, etc.)."
            ) from e

    # ufal's FasterWhisperASR.__init__ only accepts (lan, modelsize, cache_dir,
    # model_dir, logfile) — `device` and `compute_type` aren't constructor args;
    # the upstream load_model() hardcodes device="cuda" and compute_type="float16".
    # Subclass and override load_model so the operator's --device and
    # --compute_type flags actually reach faster_whisper.WhisperModel.
    class _ConfigurableFasterWhisperASR(FasterWhisperASR):
        def load_model(self, modelsize=None, cache_dir=None, model_dir=None):
            from faster_whisper import WhisperModel
            if model_dir is not None:
                model_size_or_path = model_dir
            elif modelsize is not None:
                model_size_or_path = modelsize
            else:
                raise ValueError("modelsize or model_dir parameter must be set")
            return WhisperModel(
                model_size_or_path,
                device=device,
                compute_type=compute_type,
                download_root=cache_dir,
            )

    asr = _ConfigurableFasterWhisperASR(language, model)
    return OnlineASRProcessor(asr, buffer_trimming=("segment", 8))


class _UtteranceState:
    """Per-recognizer rolling utterance state."""

    def __init__(self, asr_factory: Callable):
        self.asr = asr_factory()
        self.asr.init()
        self.turn_id: Optional[str] = None
        self.partial_seq: int = 0
        self.last_audio_ts: float = 0.0
        self.silence_audio_s: float = 0.0  # accumulated silence audio-time since last speech
        self.has_audio: bool = False

    def reset(self):
        self.asr.init()
        self.turn_id = None
        self.partial_seq = 0
        self.silence_audio_s = 0.0
        self.has_audio = False


class WhisperStreamingBackend(RecognizerBackend):
    def __init__(
        self,
        event_bus: EventBus,
        asr_factory: Optional[Callable] = None,
        model: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "de",
        min_chunk_size: float = 1.0,
    ):
        super().__init__(event_bus)
        self._min_chunk_size = float(min_chunk_size)
        self._asr_factory = asr_factory or (
            lambda: _default_asr_factory(model, device, compute_type, language)
        )
        # One state per recognizer_id, lazily created.
        self._states: dict[str, _UtteranceState] = {}
        self._states_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    @classmethod
    def from_args(cls, event_bus: "EventBus", args) -> "WhisperStreamingBackend":
        return cls(
            event_bus=event_bus,
            model=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
            min_chunk_size=args.min_chunk_size,
        )

    def wants_streaming(self) -> bool:
        return True

    def process_chunk(
        self, audio: np.ndarray, recognizer_id: str, turn_id: Optional[str] = None
    ) -> Iterator[TranscriptionEvent]:
        # turn_id from caller is ignored — this backend mints its own per utterance.
        if audio.dtype != np.int16:
            audio = audio.astype(np.int16)
        audio_f32 = audio.astype(np.float32) / 32768.0
        self._queue.put((recognizer_id, audio_f32, time.time()))
        return iter(())

    def _get_state(self, recognizer_id: str) -> _UtteranceState:
        with self._states_lock:
            st = self._states.get(recognizer_id)
            if st is None:
                st = _UtteranceState(self._asr_factory)
                self._states[recognizer_id] = st
            return st

    def _is_speech(self, audio_f32: np.ndarray) -> bool:
        """Coarse VAD: RMS over the chunk. Good enough to gate utterance boundaries."""
        if len(audio_f32) == 0:
            return False
        rms = float(np.sqrt(np.mean(audio_f32 * audio_f32)))
        return rms > 0.005

    def _run_worker(self) -> None:
        last_infer_ts: dict[str, float] = {}
        while not self._stop.is_set():
            try:
                recognizer_id, audio_f32, t_recv = self._queue.get(timeout=0.1)
            except queue.Empty:
                # Check all states for silence-timeout → finalize utterance.
                self._maybe_finalize_idle()
                continue

            st = self._get_state(recognizer_id)
            is_speech = self._is_speech(audio_f32)

            chunk_audio_s = len(audio_f32) / float(WHISPER_RATE)

            if is_speech:
                if not st.has_audio:
                    st.turn_id = ulid_now()
                    st.partial_seq = 0
                    st.has_audio = True
                st.last_audio_ts = t_recv
                st.silence_audio_s = 0.0
                try:
                    st.asr.insert_audio_chunk(audio_f32)
                except Exception as e:
                    logging.error(f"[whisper-streaming] insert_audio_chunk failed: {e}")
                    st.reset()
                    continue

                # Re-infer only every min_chunk_size seconds.
                last = last_infer_ts.get(recognizer_id, 0.0)
                if (t_recv - last) >= self._min_chunk_size:
                    last_infer_ts[recognizer_id] = t_recv
                    self._emit_partial(st, recognizer_id)
            else:
                # Silence: still pass to ASR so its own end-of-utterance logic ticks,
                # but if we've already collected speech and silence-gap exceeded
                # (measured in audio-time OR wall-clock), finalize.
                if st.has_audio:
                    try:
                        st.asr.insert_audio_chunk(audio_f32)
                    except Exception as e:
                        logging.error(f"[whisper-streaming] insert_audio_chunk failed: {e}")
                    st.silence_audio_s += chunk_audio_s
                    wall_gap = t_recv - st.last_audio_ts
                    if (
                        st.silence_audio_s >= SILENCE_TIMEOUT_S
                        or wall_gap >= SILENCE_TIMEOUT_S
                    ):
                        self._emit_final(st, recognizer_id)

    def _maybe_finalize_idle(self) -> None:
        now = time.time()
        with self._states_lock:
            ids = list(self._states.keys())
        for rid in ids:
            st = self._get_state(rid)
            if st.has_audio and (now - st.last_audio_ts) >= SILENCE_TIMEOUT_S:
                self._emit_final(st, rid)

    @staticmethod
    def _committed_prefix_text(asr) -> str:
        """All confirmed (LocalAgreement-committed) words so far, joined with
        the backend's sep. asr.commited grows; process_iter only ever returns
        the delta committed in that call. asr.finish() returns only the
        not-yet-committed remainder. So the operator-visible cumulative text
        has to be built from asr.commited explicitly."""
        try:
            commited = getattr(asr, "commited", None) or []
            if not commited:
                return ""
            _, _, text = asr.to_flush(commited)
            return (text or "").strip()
        except Exception as e:
            logging.warning(f"[whisper-streaming] _committed_prefix_text failed: {e}")
            return ""

    def _emit_partial(self, st: _UtteranceState, recognizer_id: str) -> None:
        try:
            _, _, delta_text = st.asr.process_iter()
        except Exception as e:
            logging.error(f"[whisper-streaming] process_iter failed: {e}")
            st.reset()
            return
        if not delta_text or not delta_text.strip():
            return  # no new commit this iter — nothing to update the UI with
        text = self._committed_prefix_text(st.asr)
        if not text:
            return
        ev = TranscriptionEvent(
            recognizer_id=recognizer_id,
            type="partial",
            text=text,
            timestamp=time.time(),
            backend="whisper-streaming",
            turn_id=st.turn_id,
            partial_seq=st.partial_seq,
        )
        st.partial_seq += 1
        self._bus.publish(ev)

    def _emit_final(self, st: _UtteranceState, recognizer_id: str) -> None:
        # Snapshot the committed prefix BEFORE finish() — finish() advances
        # buffer_time_offset and may otherwise mutate state.
        prefix = self._committed_prefix_text(st.asr)
        try:
            _, _, rest = st.asr.finish()
        except Exception as e:
            logging.error(f"[whisper-streaming] finish failed: {e}")
            rest = ""
        rest = (rest or "").strip()
        if prefix and rest:
            inner_asr = getattr(st.asr, "asr", None)
            sep = getattr(inner_asr, "sep", " ") if inner_asr is not None else " "
            if sep == "":
                sep = " "  # don't smash two words together if backend convention is space-already-included
            text = (prefix + sep + rest).strip()
        else:
            text = prefix or rest
        turn_id = st.turn_id
        st.reset()
        if not text:
            return
        ev = TranscriptionEvent(
            recognizer_id=recognizer_id,
            type="final",
            text=text,
            timestamp=time.time(),
            backend="whisper-streaming",
            turn_id=turn_id,
        )
        self._bus.publish(ev)

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=5.0)
