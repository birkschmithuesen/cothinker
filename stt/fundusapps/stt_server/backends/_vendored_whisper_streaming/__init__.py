"""Vendored copy of ufal/whisper_streaming's ``whisper_online`` module.

Re-exports the two symbols the fundusbot ``whisper-streaming`` backend
actually uses so callers can do::

    from fundusapps.stt_server.backends._vendored_whisper_streaming import (
        FasterWhisperASR, OnlineASRProcessor,
    )

See ``UPSTREAM.md`` for source URL, commit hash, license, and any patches.
"""
from .whisper_online import FasterWhisperASR, OnlineASRProcessor

__all__ = ["FasterWhisperASR", "OnlineASRProcessor"]
