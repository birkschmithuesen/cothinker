from typing import Dict, Type

from fundusapps.stt_server.backends.base import RecognizerBackend
from fundusapps.stt_server.backends.vosk_backend import VoskBackend


def _lazy_whisper():
    # Imported lazily so vosk-only environments don't pay the import cost
    # nor fail when faster-whisper / CUDA is not installed.
    from fundusapps.stt_server.backends.whisper_backend import WhisperBackend
    return WhisperBackend


def _lazy_whisper_streaming():
    from fundusapps.stt_server.backends.whisper_streaming_backend import (
        WhisperStreamingBackend,
    )
    return WhisperStreamingBackend


def _lazy_elevenlabs_scribe():
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    return ElevenLabsScribeBackend


def _lazy_infomaniak_whisper():
    from fundusapps.stt_server.backends.infomaniak_whisper_backend import (
        InfomaniakWhisperBackend,
    )
    return InfomaniakWhisperBackend


def _lazy_speechmatics_rt():
    from fundusapps.stt_server.backends.speechmatics_rt_backend import (
        SpeechmaticsRtBackend,
    )
    return SpeechmaticsRtBackend


BACKENDS: Dict[str, Type[RecognizerBackend]] = {
    "vosk": VoskBackend,
}


def get_backend_class(name: str) -> Type[RecognizerBackend]:
    if name == "whisper":
        return _lazy_whisper()
    if name == "whisper-streaming":
        return _lazy_whisper_streaming()
    if name == "elevenlabs-scribe":
        return _lazy_elevenlabs_scribe()
    if name == "infomaniak-whisper":
        return _lazy_infomaniak_whisper()
    if name == "speechmatics-rt":
        return _lazy_speechmatics_rt()
    return BACKENDS[name]
