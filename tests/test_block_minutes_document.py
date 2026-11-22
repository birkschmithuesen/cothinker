# CoThinker — tests for blocks/minutes_document: the minutes document block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_minutes_document.py"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

from blocks.minutes_document import ARTEFACT_NAME, MinutesDocumentBlock  # noqa: E402
from blocks.minutes_document import minutes as m  # noqa: E402
from gateway import consumer as k  # noqa: E402
from gateway.raw_transcript import MinutesFile  # noqa: E402
from shared.blocks import BLOCK_API_VERSION, BlockContext  # noqa: E402
from shared.minutes_format import split_raw  # noqa: E402
from shared.modules import CoreEvent, StateStore  # noqa: E402

HEAD = '''---
title: "Testrunde"
entity: testprojekt
protokoll_format: 2
modus: sitzung
---

# Testrunde

## Überblick

<!-- Abgeleitete Sicht. -->

### Beschlüsse

*noch keine*

### Aufgaben

*noch keine*

### Offene Fragen

*noch keine*

## Themen

'''
RAW = "## Rohtranskript\n- **09:00** *Max:* hallo\n"
V1 = '---\ntitle: "Alt"\n---\n\n# Alt\n\n## Verlauf\n\n## Rohtranskript\n- **09:00** x\n'


def _doc(tmp_path, text=HEAD + RAW):
    p = tmp_path / "protokoll.md"
    p.write_text(text, encoding="utf-8")
    return p


def _started(tmp_path):
    """Block + the heads it handed to ctx.artefact."""
    heads: list[str] = []
    ctx = BlockContext(
        instance=None, session_dir=tmp_path, state=StateStore(tmp_path / "st"),
        agent=None, llm=None, language="de", clock=lambda: 0.0,
        emit=lambda i: None,
        artefact=lambda name, text: heads.append(text) if name == ARTEFACT_NAME else None)
    b = MinutesDocumentBlock()
    b.start(ctx, {})
    return b, heads


def _ev(**payload):
    return CoreEvent("block_output", payload)


def _open(b, p, mode="sitzung"):
    return b.on_event(_ev(minutes_open={"path": str(p), "mode": mode}))


def test_contract():
    b = MinutesDocumentBlock()
    assert b.id == "minutes-document"
    assert b.api_version == BLOCK_API_VERSION


def test_entry_renders_a_head_and_announces_the_change(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    out = b.on_event(_ev(entry={"tag": "beschluss", "text": "Wir bauen es",
                                "stamp": "10:00", "names": None}))
    assert [(e.kind, e.payload) for e in out] == [
        ("block_output", {"minutes_changed": {"name": "protokoll.md"}})]
    assert len(heads) == 1
    assert "Wir bauen es" in heads[0]
    assert "## Rohtranskript" not in heads[0]
    assert b.last_result().reason == ""
    # splicing it in front of the raw section keeps that section byte-identical
    MinutesFile(p, threading.RLock()).write_sections(heads[0])
    new = p.read_text(encoding="utf-8")
    assert split_raw(new)[1] == RAW
    assert "10:00" in split_raw(new)[0]


def test_changed_event_only_on_a_landed_write(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.on_event(_ev(entry={"tag": "unbekannt", "text": "x"})) == []
    assert b.last_result().reason == "unknown tag"
    assert b.on_event(_ev(entry={"tag": "notiz", "text": "   "})) == []
    assert b.last_result().reason == "no text"
    assert heads == []


def test_duplicate_entry_yields_no_event(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    first = b.on_event(_ev(entry={"tag": "notiz", "text": "a", "stamp": "10:00"}))
    assert first
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    again = b.on_event(_ev(entry={"tag": "notiz", "text": "a", "stamp": "10:01"}))
    assert again == []
    assert b.last_result().reason == "duplicate"
    assert len(heads) == 1


def test_not_v2_file_yields_no_event(tmp_path):
    p = _doc(tmp_path, V1)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.is_v2 is False
    assert b.on_event(_ev(entry={"tag": "notiz", "text": "a"})) == []
    assert b.last_result().reason == "not v2"
    assert heads == []


def test_bullet_and_condensed_become_notes(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.on_event(_ev(bullet={"text": "ein Punkt", "stamp": "10:00"}))
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    assert b.on_event(_ev(condensed={"text": "kurz gefasst", "stamp": "10:01"}))
    head = heads[-1]
    assert "[notiz] ein Punkt" in head
    assert "[notiz] kurz gefasst" in head


def test_topic_opens_a_block(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.on_event(_ev(topic={"title": "Budget", "stamp": "10:05"}))
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    assert "### 10:05 Budget" in p.read_text(encoding="utf-8")
    assert b.current_topic()[1] == "Budget"
    assert b.current_topic_title() == "Budget"


def test_no_file_io_before_minutes_open(tmp_path):
    p = tmp_path / "missing.md"
    b, heads = _started(tmp_path)
    assert b.on_event(_ev(entry={"tag": "notiz", "text": "a"})) == []
    assert b.last_result().reason == "not written"
    assert b.current_topic() == ("", "")
    assert b.is_v2 is False
    assert b.mode == m.DEFAULT_MODE
    assert heads == []
    assert not p.exists()
    assert not (tmp_path / "artefacts").exists()


def test_last_result_pops(tmp_path):
    p = _doc(tmp_path)
    b, _ = _started(tmp_path)
    _open(b, p)
    assert b.last_result() is None
    b.on_event(_ev(entry={"tag": "notiz", "text": "a"}))
    assert b.last_result() is not None
    assert b.last_result() is None


def test_other_events_are_ignored(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.on_event(CoreEvent("utterance", {"text": "hi"})) == []
    assert b.on_event(_ev(minutes_changed={"name": "protokoll.md"})) == []
    assert heads == []


def test_existing_v2_file_keeps_its_recorded_mode(tmp_path):
    p = _doc(tmp_path)
    logs = []
    b, _ = _started(tmp_path)
    old = m.log
    m.log = lambda *a: logs.append(" ".join(map(str, a)))
    try:
        _open(b, p, mode="arbeit")
    finally:
        m.log = old
    assert b.mode == "sitzung"
    assert any("ignored" in x for x in logs)


def test_the_block_package_never_names_the_raw_section():
    r = subprocess.run(["git", "grep", "-n", "Rohtranskript", "--", "blocks"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.stdout == ""
    for f in (REPO / "blocks" / "minutes_document").glob("*.py"):
        assert "Rohtranskript" not in f.read_text(encoding="utf-8")


# ---- host side: MinutesPort over a real session block host ----------------

def test_port_splices_and_removes_the_artefact(tmp_path):
    p = _doc(tmp_path)
    reports = []
    port = k.open_minutes(p, report=reports.append, session_dir=tmp_path / "sess")
    assert port.is_v2 is True
    res = port.add_entry("beschluss", "Wir bauen es", stamp="10:00")
    assert res.reason == ""
    new = p.read_text(encoding="utf-8")
    assert "Wir bauen es" in split_raw(new)[0]
    assert split_raw(new)[1] == RAW
    assert not (tmp_path / "sess" / "artefacts" / "minutes-document" / "protokoll.md").exists()
    assert reports == [True]
    assert port.current_topic()[1] == "Beginn"
    assert port.open_topic("Budget", "10:05") is True
    assert port.add_entry("notiz", "Wir bauen es", stamp="10:00").reason == ""
    assert port.add_entry("beschluss", "wir  BAUEN es").reason == "duplicate"
    assert port.add_entry("beschluss", "wir  BAUEN es").reason == "duplicate"


def test_port_raw_append_comes_first_and_survives(tmp_path):
    p = _doc(tmp_path)
    port = k.open_minutes(p, session_dir=tmp_path / "sess")
    assert port.append_raw("zweite Zeile", "Eva") is True
    port.add_entry("notiz", "a", stamp="10:00")
    new = p.read_text(encoding="utf-8")
    assert "*Eva:* zweite Zeile" in split_raw(new)[1]
    assert split_raw(new)[1].startswith(RAW)


def test_port_reports_a_failed_write_as_not_written(tmp_path):
    p = _doc(tmp_path)
    reports = []
    port = k.open_minutes(p, report=reports.append, session_dir=tmp_path / "sess")

    def boom(text):
        raise OSError("disk full")
    port.file.write_sections = boom
    assert port.add_entry("notiz", "a", stamp="10:00").reason == "not written"
    assert port.open_topic("T", "10:00") is False
    assert reports == [False, False]


def test_port_on_a_v1_file(tmp_path):
    p = _doc(tmp_path, V1)
    port = k.open_minutes(p, session_dir=tmp_path / "sess")
    assert port.is_v2 is False
    assert port.add_entry("notiz", "a").reason == "not v2"
    assert port.current_topic() == ("", "")
    assert p.read_text(encoding="utf-8") == V1
