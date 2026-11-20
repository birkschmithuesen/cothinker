import datetime
import os
import sys
import time
import logging
import threading
import queue
import wave
from pathlib import Path
from typing import Iterator, Optional
import numpy as np


def _register_cudnn_dll_dir() -> None:
    """On Windows, ctranslate2 (used by faster-whisper) bundles cudnn64_9.dll
    but not its sub-DLLs (cudnn_ops, cudnn_cnn, ...). When pip-installed via
    `nvidia-cudnn-cu12`, those sub-DLLs sit under
    venv/Lib/site-packages/nvidia/cudnn/bin/, but ctranslate2 loads them
    transitively via LoadLibrary which does NOT honor os.add_dll_directory().
    Prepend that one bin dir to PATH so the transitive load resolves it.

    We deliberately avoid touching nvidia/cublas/bin or any other nvidia/*/bin —
    those would shadow whatever system-wide cuBLAS the user already has and
    cause CUBLAS_STATUS_INVALID_VALUE when versions mismatch.
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia  # noqa: F401
    except ImportError:
        return
    cudnn_bin = Path(nvidia.__path__[0]) / "cudnn" / "bin"
    if not cudnn_bin.is_dir():
        return
    try:
        os.add_dll_directory(str(cudnn_bin))
    except (OSError, AttributeError):
        pass
    os.environ["PATH"] = str(cudnn_bin) + os.pathsep + os.environ.get("PATH", "")


_register_cudnn_dll_dir()

from faster_whisper import WhisperModel

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.backends.base import RecognizerBackend


WHISPER_RATE = 16000


class WhisperBackend(RecognizerBackend):
    """Single shared WhisperModel + single worker thread.

    Recognizers submit VAD-segmented int16 mono chunks via process_chunk().
    The worker pulls from the queue, calls model.transcribe(), and publishes
    one `final` event per yielded segment.
    """

    def __init__(
        self,
        event_bus: EventBus,
        model_name: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "de",
        debug_audio_folder: Optional[Path] = None,
    ):
        super().__init__(event_bus)
        self._debug_folder = Path(debug_audio_folder) if debug_audio_folder else None
        if self._debug_folder is not None:
            self._debug_folder.mkdir(parents=True, exist_ok=True)
        self._chunk_idx = 0
        self._chunk_idx_lock = threading.Lock()

        logging.info(
            f"Loading WhisperModel '{model_name}' (device={device}, "
            f"compute_type={compute_type}) — this may take a moment on first run."
        )
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self._language = language
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()
        logging.info("WhisperBackend ready.")

    def wants_streaming(self) -> bool:
        return False

    @classmethod
    def from_args(cls, event_bus: EventBus, args) -> "WhisperBackend":
        debug_folder = Path("audio/stt_rec") if getattr(args, "debug_audio", False) else None
        return cls(
            event_bus=event_bus,
            model_name=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
            debug_audio_folder=debug_folder,
        )

    def process_chunk(
        self, audio: np.ndarray, recognizer_id: str, turn_id: Optional[str] = None
    ) -> Iterator[TranscriptionEvent]:
        """Submit an int16 mono chunk @ 16 kHz for transcription. Returns
        an empty iterator — events are published asynchronously by the worker.
        """
        if audio.dtype != np.int16:
            audio = audio.astype(np.int16)
        # Convert to float32 in [-1, 1] once, here, so the worker doesn't
        # repeat the work on every dequeue.
        audio_f32 = audio.astype(np.float32) / 32768.0
        self._queue.put((recognizer_id, audio_f32, time.time(), turn_id))
        return iter(())

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                recognizer_id, audio_f32, t_submitted, turn_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            chunk_path = None
            if self._debug_folder is not None:
                with self._chunk_idx_lock:
                    idx = self._chunk_idx
                    self._chunk_idx += 1
                ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
                chunk_path = self._debug_folder / f"{recognizer_id}_{ts}_{idx:05d}.wav"
                audio_i16 = (audio_f32 * 32768.0).astype(np.int16)
                with wave.open(str(chunk_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(WHISPER_RATE)
                    wf.writeframes(audio_i16.tobytes())
            try:
                segments, _info = self._model.transcribe(
                    audio_f32,
                    language=self._language,
                    vad_filter=True,
                    beam_size=1,
                )
                segments_list = list(segments)
                texts = [s.text.strip() for s in segments_list if (s.text or "").strip()]
                for s in segments_list:
                    text = (s.text or "").strip()
                    if not text:
                        continue
                    ev = TranscriptionEvent(
                        recognizer_id=recognizer_id,
                        type="final",
                        text=text,
                        timestamp=time.time(),
                        backend="whisper",
                        confidence=getattr(s, "avg_logprob", None),
                        turn_id=turn_id,
                    )
                    self._bus.publish(ev)
                if chunk_path is not None:
                    duration = len(audio_f32) / WHISPER_RATE
                    rms = float(np.sqrt(np.mean(audio_f32 * audio_f32))) if len(audio_f32) else 0.0
                    logging.info(
                        f"whisper chunk {chunk_path} duration={duration:.2f}s rms={rms:.4f} "
                        f"text='{' | '.join(texts) or '<no text>'}'"
                    )
                elif not segments_list:
                    logging.debug(
                        f"[whisper] {recognizer_id}: no segments "
                        f"({time.time() - t_submitted:.2f}s)"
                    )
            except Exception as e:
                logging.error(f"WhisperBackend transcribe error: {e}")

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=5.0)
        self._model = None
