"""A bare `pytest` run from the repo root must discover stt/'s tests via
testpaths, while still honoring stt/pytest.ini's hardware/model marker
exclusions so CI and card runs don't try to load faster_whisper/CUDA/vosk."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _collect_only():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_bare_collection_discovers_stt_tests():
    out = _collect_only()
    assert "stt/fundusapps/common/tests/test_turnid.py::test_ulid_now_is_26_char_crockford" in out


def test_bare_collection_deselects_tests_needing_a_vosk_model():
    out = _collect_only()
    assert "test_vosk_backend.py::test_wants_streaming_is_true" not in out
    assert "test_vosk_backend.py::test_silence_emits_no_final" not in out
