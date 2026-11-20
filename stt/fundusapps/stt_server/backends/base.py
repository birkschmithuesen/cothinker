from abc import ABC, abstractmethod
from typing import Iterator, Optional
import numpy as np

from fundusapps.stt_server.events import TranscriptionEvent, EventBus


class RecognizerBackend(ABC):
    """Pluggable speech-recognition backend.

    Two interaction modes:
      * Streaming (wants_streaming() is True): the recognizer pushes
        every resampled audio frame via process_chunk(), expecting the
        backend to yield 0..N events synchronously per call.
      * Segmented (wants_streaming() is False): the recognizer accumulates
        audio between VAD trigger events and submits one buffered chunk
        per phrase. The backend may transcribe asynchronously and publish
        events to the bus on its own schedule.

    All backends additionally publish events to the shared EventBus for
    the operator display.
    """

    #: Does this backend want the audio-clock offset of each frame?
    #: False for everything by default, and that is load-bearing: vosk,
    #: whisper-streaming and the four test fakes all implement
    #: `process_chunk(audio, recognizer_id, turn_id=None)`, and passing a
    #: new keyword to all of them would break every one of those
    #: signatures. A backend that sets this True is called as
    #: `process_chunk(audio, recognizer_id, audio_offset=<int sample>)`.
    accepts_audio_offset = False

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus

    @classmethod
    @abstractmethod
    def from_args(cls, event_bus: "EventBus", args) -> "RecognizerBackend":
        """Construct a backend from parsed argparse `args`. Subclasses
        override to consume their subparser-specific options.
        """
        ...

    @abstractmethod
    def wants_streaming(self) -> bool:
        ...

    @abstractmethod
    def process_chunk(
        self, audio: np.ndarray, recognizer_id: str, turn_id: Optional[str] = None
    ) -> Iterator[TranscriptionEvent]:
        ...

    @abstractmethod
    def close(self) -> None:
        ...
