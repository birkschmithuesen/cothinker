"""The consumer must show the attributed speaker, not the raw channel id."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT))

import consumer  # noqa: E402
from shared.stt import TranscriptEvent  # noqa: E402


def test_attributed_speaker_wins_over_recognizer_id():
    ev = TranscriptEvent.from_wire({"type": "final", "speaker": "Alex", "recognizer_id": "S1"})
    assert consumer.final_speaker(ev) == "Alex"


def test_falls_back_to_recognizer_id():
    ev = TranscriptEvent.from_wire({"recognizer_id": "S2"})
    assert consumer.final_speaker(ev) == "S2"


def test_default_and_mic_gate_fold_to_none():
    assert consumer.final_speaker(TranscriptEvent.from_wire({"recognizer_id": "default"})) is None
    assert consumer.final_speaker(TranscriptEvent.from_wire({"speaker": "mic_gate"})) is None
    assert consumer.final_speaker(TranscriptEvent.from_wire({})) is None
