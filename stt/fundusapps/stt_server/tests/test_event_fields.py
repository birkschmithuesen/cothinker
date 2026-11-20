"""The three additive fields on a final, and the copy that has to follow.

Additive means additive (AGENTS.md): the fields default to None, they sit
at the END of the dataclass so no positional construction moves, and a
consumer that knows nothing about them keeps working. Task 10's gateway
tests are the other half of that promise.
"""
import dataclasses
import json
from pathlib import Path

from fundusapps.stt_server.events import TranscriptionEvent
from fundusapps.stt_server.backends import infomaniak_whisper_backend

STT_SERVER = Path(infomaniak_whisper_backend.__file__).resolve().parents[1]


def _final(**kw):
    base = dict(recognizer_id="0", type="final", text="Platzhaltersatz",
                timestamp=1.0, backend="whisper")
    base.update(kw)
    return TranscriptionEvent(**base)


def test_the_three_new_fields_default_to_none():
    d = _final().to_dict()
    assert d["t_start"] is None
    assert d["t_end"] is None
    assert d["audio_epoch"] is None


def test_the_fields_survive_to_dict():
    d = _final(t_start=1.013, t_end=2.487, audio_epoch=1758700000.25).to_dict()
    assert d["t_start"] == 1.013
    assert d["t_end"] == 2.487
    assert d["audio_epoch"] == 1758700000.25


def test_the_sse_serialisation_carries_them():
    """app.py's /events generator is literally `json.dumps(ev.to_dict())`
    -- so the three fields have to survive that round trip as JSON
    numbers, and that line has to still be the one doing it."""
    back = json.loads(json.dumps(
        _final(t_start=1.013, t_end=2.487, audio_epoch=1758700000.25).to_dict()))
    assert back["t_start"] == 1.013
    assert back["t_end"] == 2.487
    assert back["audio_epoch"] == 1758700000.25
    app_src = (STT_SERVER / "app.py").read_text(encoding="utf-8")
    assert "json.dumps(ev.to_dict())" in app_src


def test_the_infomaniak_fallback_copy_lists_the_same_fields():
    """That copy is only reachable outside this checkout, so no import can
    test it -- but it is a field-identical copy by contract, and a copy
    that quietly falls behind is worse than no copy at all."""
    src = Path(infomaniak_whisper_backend.__file__).read_text(encoding="utf-8")
    block = src.split("class TranscriptionEvent:", 1)[1] \
               .split("log = logging.getLogger", 1)[0]
    for field in dataclasses.fields(TranscriptionEvent):
        assert f"{field.name}:" in block, field.name
    assert "dreizehn Felder" in block
