# CoThinker — tests of the shared STT API client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of `shared/stt.py`: the SSE line parser (moved unchanged from
`gateway/consumer.py`), the `TranscriptEvent` wire mapping, and the SSE
transport (`SseSource`, `open_stt`).
"""
from __future__ import annotations

import pytest

from shared import stt


# ============================================================== parse_sse
# Moved unchanged from tests/test_consumer.py's "SSE parsing" scenario.

def test_parse_sse_simple_event():
    roh = [b'data: {"type": "final", "text": "Hallo"}\n', b"\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "Hallo"}]


def test_parse_sse_multiline_data_field():
    roh = [b"data: {\n", b'data:   "type": "final",\n', b'data:   "text": "Zwei"\n',
          b"data: }\n", b"\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "Zwei"}]


def test_parse_sse_crlf_line_endings():
    roh = [b'data: {"type": "final", "text": "CRLF"}\r\n', b"\r\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "CRLF"}]


def test_parse_sse_comment_event_id_retry_skipped():
    roh = [b": keep-alive\n", b"event: message\n", b"id: 17\n",
          b"retry: 3000\n", b'data: {"type": "final", "text": "X"}\n', b"\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "X"}]


def test_parse_sse_broken_json_skipped():
    roh = [b"data: {kaputt\n", b"\n",
          b'data: {"type": "final", "text": "danach"}\n', b"\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "danach"}]


def test_parse_sse_non_objects_discarded():
    roh = [b"data: [1,2,3]\n", b"\n", b"data: null\n", b"\n", b"data: 42\n", b"\n"]
    assert list(stt.parse_sse(roh)) == []


def test_parse_sse_event_at_end_of_stream():
    roh = [b'data: {"type": "final", "text": "letztes"}\n']
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "letztes"}]


def test_parse_sse_data_without_space():
    roh = [b'data:{"type":"final","text":"eng"}\n', b"\n"]
    assert list(stt.parse_sse(roh)) == [{"type": "final", "text": "eng"}]


def test_parse_sse_invalid_bytes_replaced():
    roh = [b'data: {"type": "final", "text": "kaputt \xff\xfe"}\n', b"\n"]
    ev = list(stt.parse_sse(roh))
    assert len(ev) == 1 and ev[0]["type"] == "final"


def test_parse_sse_two_events_separated():
    roh = [b'data: {"n": 1}\n', b"\n", b'data: {"n": 2}\n', b"\n"]
    assert list(stt.parse_sse(roh)) == [{"n": 1}, {"n": 2}]


# =========================================================== TranscriptEvent
_TARGET_FIELDS = ("type", "recognizer_id", "timestamp", "text", "turn_id",
                  "t_start", "t_end", "audio_epoch", "speaker", "channel",
                  "backend")

# Four recorded synthetic wire lines (invented placeholders, not real
# meeting content): a final with audio clock, speaker, channel and words;
# a partial; a mic-gate event; a thought-end event.
_WIRE_FINAL = {
    "recognizer_id": "S1", "type": "final", "text": "Hallo zusammen",
    "timestamp": 1732000000.123, "backend": "speechmatics-rt", "turn_id": "t1",
    "t_start": 12.5, "t_end": 14.0, "audio_epoch": 1731999987.5,
    "speaker": "S1", "channel": "0",
    "words": [{"w": "Hallo", "label": "S1", "volume": 0.4,
              "start_s": 12.5, "end_s": 12.8}],
}
_WIRE_PARTIAL = {
    "recognizer_id": "0", "type": "partial", "text": "Hallo",
    "timestamp": 1732000000.0, "backend": "vosk",
}
_WIRE_MIC_GATE = {
    "type": "mic_gate", "recognizer_id": "mic_gate", "mic_on": False,
    "level_rms": 0.001, "threshold": 0.02, "timestamp": 1732000001.0,
}
_WIRE_THOUGHT_END = {
    "type": "thought_end", "recognizer_id": "S1", "turn_id": "t1",
    "t_end": 14.0, "p": 0.87, "ms": 12.3, "error": None, "audio_s": 1.5,
    "timestamp": 1732000002.0, "candidate": "smart_turn",
}


def test_from_wire_reads_every_target_field():
    ev = stt.TranscriptEvent.from_wire(_WIRE_FINAL)
    for name in _TARGET_FIELDS:
        assert getattr(ev, name) == _WIRE_FINAL[name], name


def test_unknown_keys_kept_in_extra():
    ev = stt.TranscriptEvent.from_wire(_WIRE_FINAL)
    assert ev.extra == {"words": _WIRE_FINAL["words"]}


@pytest.mark.parametrize("wire", [_WIRE_FINAL, _WIRE_PARTIAL, _WIRE_MIC_GATE,
                                  _WIRE_THOUGHT_END],
                        ids=["final", "partial", "mic_gate", "thought_end"])
def test_wire_round_trip_is_byte_identical(wire):
    assert stt.TranscriptEvent.from_wire(wire).to_wire() == wire


def test_missing_or_non_numeric_fields_become_defaults():
    ev = stt.TranscriptEvent.from_wire({
        "type": 123, "recognizer_id": None, "text": "ok",
        "t_start": "not-a-number", "t_end": None, "timestamp": [1, 2],
    })
    assert ev.type is None
    assert ev.recognizer_id is None
    assert ev.text == "ok"
    assert ev.t_start is None
    assert ev.t_end is None
    assert ev.timestamp is None
    assert ev.turn_id is None
    assert ev.audio_epoch is None
    assert ev.speaker is None
    assert ev.channel is None
    assert ev.backend is None


def test_replace_drops_the_wire_copy():
    ev = stt.TranscriptEvent.from_wire(_WIRE_FINAL)
    changed = ev.replace(text="geaendert")
    assert changed.text == "geaendert"
    assert changed.to_wire() != _WIRE_FINAL
    assert changed.to_wire()["text"] == "geaendert"


# =================================================================== SSE
def test_sse_source_reads_events_from_server(monkeypatch):
    seen = {}
    lines = [b'data: {"type": "final", "text": "Hallo"}\n', b"\n"]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter(lines)

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(stt.urllib.request, "urlopen", _fake_urlopen)
    cfg = stt.SttConfig(transport="sse", url="http://host:5051", read_timeout_s=42.0)
    source = stt.open_stt(cfg)
    events = list(source.events())
    assert [e.to_wire() for e in events] == [{"type": "final", "text": "Hallo"}]
    assert seen["timeout"] == 42.0


def test_sse_source_appends_events_path(monkeypatch):
    seen = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter([])

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResponse()

    monkeypatch.setattr(stt.urllib.request, "urlopen", _fake_urlopen)
    cfg = stt.SttConfig(transport="sse", url="http://host:5051/", read_timeout_s=1.0)
    list(stt.open_stt(cfg).events())
    assert seen["url"] == "http://host:5051/events"


def test_open_stt_rejects_unknown_transport():
    cfg = stt.SttConfig(transport="websocket", url="http://host:5051")
    with pytest.raises(ValueError):
        stt.open_stt(cfg)


def test_sse_read_timeout_propagates(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            raise TimeoutError("timed out")

    def _fake_urlopen(req, timeout=None):
        return _FakeResponse()

    monkeypatch.setattr(stt.urllib.request, "urlopen", _fake_urlopen)
    cfg = stt.SttConfig(transport="sse", url="http://host:5051", read_timeout_s=0.01)
    source = stt.open_stt(cfg)
    with pytest.raises(TimeoutError):
        list(source.events())
