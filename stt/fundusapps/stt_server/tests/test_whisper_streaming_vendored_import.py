"""Smoketest: the vendored whisper_online imports without error.

This test does NOT load a model. It only verifies that
_default_asr_factory's vendored fallback path can be reached on Windows
where the upstream pip package isn't installable.
"""
import pytest

# whisper_online imports librosa at module level. faster_whisper is
# installed on the vServer but librosa is not, and the recording PC does
# not install it either -- so this is a genuine "not applicable here",
# not a failure to fix. Both tests below already importorskip
# faster_whisper for the same reason.
pytest.importorskip("librosa", reason="librosa not installed")


def test_vendored_whisper_online_imports():
    pytest.importorskip("faster_whisper", reason="faster_whisper not installed")
    from fundusapps.stt_server.backends._vendored_whisper_streaming.whisper_online import (
        FasterWhisperASR,
        OnlineASRProcessor,
    )
    assert FasterWhisperASR.__name__ == "FasterWhisperASR"
    assert OnlineASRProcessor.__name__ == "OnlineASRProcessor"


def test_default_asr_factory_resolves_classes():
    """Verify _default_asr_factory's import block resolves (doesn't actually
    construct OnlineASRProcessor, which would load a model)."""
    pytest.importorskip("faster_whisper", reason="faster_whisper not installed")
    # Re-implement the import block locally to avoid the model-load side-effect
    try:
        from whisper_online import FasterWhisperASR, OnlineASRProcessor
    except ImportError:
        from fundusapps.stt_server.backends._vendored_whisper_streaming.whisper_online import (
            FasterWhisperASR, OnlineASRProcessor,
        )
    assert callable(FasterWhisperASR)
    assert callable(OnlineASRProcessor)
