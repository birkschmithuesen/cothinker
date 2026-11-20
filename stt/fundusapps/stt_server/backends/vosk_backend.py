import json
import time
import logging
from typing import Iterator, Dict, Optional
import numpy as np
from vosk import Model, KaldiRecognizer

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.backends.base import RecognizerBackend

VOSK_RATE = 16000


class VoskBackend(RecognizerBackend):
    """Wraps one KaldiRecognizer per recognizer_id. Streaming-style: every
    16 kHz int16 frame goes in via process_chunk(), partials/finals come out.
    """

    def __init__(self, event_bus: EventBus, model_path: str):
        super().__init__(event_bus)
        self._model = Model(model_path)
        self._recs: Dict[str, KaldiRecognizer] = {}
        self._last_partial: Dict[str, str] = {}
        logging.info(f"VoskBackend loaded model from {model_path}")

    @classmethod
    def from_args(cls, event_bus: EventBus, args) -> "VoskBackend":
        return cls(event_bus=event_bus, model_path=args.model)

    def wants_streaming(self) -> bool:
        return True

    def _rec_for(self, recognizer_id: str) -> KaldiRecognizer:
        if recognizer_id not in self._recs:
            self._recs[recognizer_id] = KaldiRecognizer(self._model, VOSK_RATE)
            self._last_partial[recognizer_id] = ""
        return self._recs[recognizer_id]

    def process_chunk(
        self, audio: np.ndarray, recognizer_id: str, turn_id: Optional[str] = None
    ) -> Iterator[TranscriptionEvent]:
        rec = self._rec_for(recognizer_id)
        if audio.dtype != np.int16:
            audio = audio.astype(np.int16)
        data = audio.tobytes()
        now = time.time()

        if rec.AcceptWaveform(data):
            text = (json.loads(rec.Result()).get("text") or "").strip()
            self._last_partial[recognizer_id] = ""
            if text:
                ev = TranscriptionEvent(
                    recognizer_id=recognizer_id,
                    type="final",
                    text=text,
                    timestamp=now,
                    backend="vosk",
                    turn_id=turn_id,
                )
                self._bus.publish(ev)
                yield ev
        else:
            partial = (json.loads(rec.PartialResult()).get("partial") or "").strip()
            if partial and partial != self._last_partial.get(recognizer_id):
                self._last_partial[recognizer_id] = partial
                ev = TranscriptionEvent(
                    recognizer_id=recognizer_id,
                    type="partial",
                    text=partial,
                    timestamp=now,
                    backend="vosk",
                )
                self._bus.publish(ev)
                yield ev

    def close(self) -> None:
        self._recs.clear()
        self._model = None
