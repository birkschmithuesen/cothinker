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


def _changes(out):
    """The `minutes_changed` events among a block's outputs."""
    return [e for e in out if "minutes_changed" in e.payload]


def _result(out):
    """The `entry_result` payload among a block's outputs."""
    got = [e.payload for e in out if e.payload.get("kind") == "entry_result"]
    assert len(got) == 1
    return got[0]


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
    assert [(e.kind, e.payload) for e in _changes(out)] == [
        ("block_output", {"minutes_changed": {"name": "protokoll.md"}})]
    assert len(heads) == 1
    assert "Wir bauen es" in heads[0]
    assert "## Rohtranskript" not in heads[0]
    assert _result(out)["reason"] == ""
    # splicing it in front of the raw section keeps that section byte-identical
    MinutesFile(p, threading.RLock()).write_sections(heads[0])
    new = p.read_text(encoding="utf-8")
    assert split_raw(new)[1] == RAW
    assert "10:00" in split_raw(new)[0]


def test_changed_event_only_on_a_landed_write(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    out = b.on_event(_ev(entry={"tag": "unbekannt", "text": "x"}))
    assert _changes(out) == [] and _result(out)["reason"] == "unknown tag"
    out = b.on_event(_ev(entry={"tag": "notiz", "text": "   "}))
    assert _changes(out) == [] and _result(out)["reason"] == "no text"
    assert heads == []


def test_duplicate_entry_yields_no_event(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    first = b.on_event(_ev(entry={"tag": "notiz", "text": "a", "stamp": "10:00"}))
    assert _changes(first)
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    again = b.on_event(_ev(entry={"tag": "notiz", "text": "a", "stamp": "10:01"}))
    assert _changes(again) == []
    assert _result(again)["reason"] == "duplicate"
    assert len(heads) == 1


def test_not_v2_file_yields_no_event(tmp_path):
    p = _doc(tmp_path, V1)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert b.is_v2 is False
    out = b.on_event(_ev(entry={"tag": "notiz", "text": "a"}))
    assert _changes(out) == [] and _result(out)["reason"] == "not v2"
    assert heads == []


def test_bullet_and_condensed_become_notes(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert _changes(b.on_event(_ev(bullet={"text": "ein Punkt", "stamp": "10:00"})))
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    assert _changes(b.on_event(_ev(condensed={"text": "kurz gefasst", "stamp": "10:01"})))
    head = heads[-1]
    assert "[notiz] ein Punkt" in head
    assert "[notiz] kurz gefasst" in head


def test_topic_opens_a_block(tmp_path):
    p = _doc(tmp_path)
    b, heads = _started(tmp_path)
    _open(b, p)
    assert _changes(b.on_event(_ev(topic={"title": "Budget", "stamp": "10:05"})))
    MinutesFile(p, threading.RLock()).write_sections(heads[-1])
    assert "### 10:05 Budget" in p.read_text(encoding="utf-8")
    state = b.on_event(_ev(minutes_query={}))[0].payload
    assert state["kind"] == "minutes_state"
    assert (state["topic_key"] != "" and state["topic_title"] == "Budget"
            and state["is_v2"] is True)


def test_no_file_io_before_minutes_open(tmp_path):
    p = tmp_path / "missing.md"
    b, heads = _started(tmp_path)
    out = b.on_event(_ev(entry={"tag": "notiz", "text": "a"}))
    assert _changes(out) == [] and _result(out)["reason"] == "not written"
    state = b.on_event(_ev(minutes_query={}))[0].payload
    assert (state["topic_key"], state["topic_title"]) == ("", "")
    assert state["is_v2"] is False and state["mode"] == m.DEFAULT_MODE
    assert heads == []
    assert not p.exists()
    assert not (tmp_path / "artefacts").exists()


def test_every_write_event_is_answered_with_one_result(tmp_path):
    p = _doc(tmp_path)
    b, _ = _started(tmp_path)
    _open(b, p)
    out = b.on_event(_ev(entry={"tag": "notiz", "text": "a"}))
    assert _result(out)["attempted"] is True
    assert b.on_event(_ev(minutes_query={}))[0].payload["kind"] == "minutes_state"


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
    r = subprocess.run(["git", "grep", "-n", "Rohtranskript", "--", "blocks/minutes_document"],
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


def test_port_entry_lands_above_a_stray_non_utf8_byte(tmp_path):
    # A stray byte in the head must not block every later section write
    # (final review: the artefact transport used to encode strictly).
    p = tmp_path / "protokoll.md"
    p.write_bytes((HEAD + "- 09:00 alt ").encode("utf-8") + b"\xff"
                  + ("\n\n" + RAW).encode("utf-8"))
    raw_before = p.read_bytes()[p.read_bytes().index(b"## Rohtranskript"):]
    port = k.open_minutes(p, session_dir=tmp_path / "sess")
    res = port.add_entry("notiz", "neu", stamp="10:00")
    assert res.reason == ""
    data = p.read_bytes()
    assert b"neu" in data
    assert b"\xff" in data
    assert data[data.index(b"## Rohtranskript"):] == raw_before


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


def test_port_does_not_report_a_failure_before_any_write(tmp_path):
    p = _doc(tmp_path)
    reports = []
    port = k.open_minutes(p, report=reports.append, session_dir=tmp_path / "sess")
    res = port.add_entry("notiz", "a", names=42)      # not iterable
    assert res.reason == "not written"
    assert reports == []
    assert port.add_entry("notiz", "b", stamp="10:00").reason == ""
    assert reports == [True]


def test_port_on_a_v1_file(tmp_path):
    p = _doc(tmp_path, V1)
    port = k.open_minutes(p, session_dir=tmp_path / "sess")
    assert port.is_v2 is False
    assert port.add_entry("notiz", "a").reason == "not v2"
    assert port.current_topic() == ("", "")
    assert p.read_text(encoding="utf-8") == V1


# ---------------------------------------------------------------------------
# Scenarios moved from tests/test_consumer.py (issue #44). Each scenario is a
# module-scoped fixture (`sec_NN`) that records the outcome of every check it
# makes; each check is one test case named after it. They run against
# open_minutes / MinutesPort and the block module.
# ---------------------------------------------------------------------------
import atexit  # noqa: E402
import json as _json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime  # noqa: E402

from shared import events as _events  # noqa: E402
from shared import minutes_format as _pd  # noqa: E402
from stage_state import StageState  # noqa: E402

consumer = k


class _Recorder:
    def __init__(self):
        self.sites = {}
        self.error = ""

    def add(self, site, ok, name, detail):
        self.sites.setdefault(site, []).append((bool(ok), name, detail))


_REC = _Recorder()
_SCENARIO_EXPECTS_FAILURES = False


def _chk(site, name, cond=True, detail=""):
    _REC.add(site, cond, name, detail)


def _chk_ne(site, name, fn, *args, **kwargs):
    try:
        res = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _REC.add(site, False, name, f"{type(exc).__name__}: {exc}")
        return None
    _REC.add(site, True, name, "")
    return res


def _assert_site(rec, site, occ):
    # a failed setup step must not let later negative checks pass against
    # stale state: a scenario without expected failures has no setup errors
    if not _SCENARIO_EXPECTS_FAILURES:
        assert not rec.error, "scenario setup error: " + rec.error[-1500:]
    results = rec.sites.get(site)
    assert results, "check never ran; scenario error: " + rec.error[-1500:]
    if occ is not None:
        assert occ < len(results), f"only {len(results)} runs of this check"
        results = [results[occ]]
    for ok, name, detail in results:
        assert ok, f"{name}: {detail}"


def _mkdtemp(*args, **kwargs):
    """tempfile.mkdtemp whose directory is removed again at exit."""
    d = tempfile.mkdtemp(*args, **kwargs)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


_ABSENT = pytest.mark.xfail(strict=True, reason="behaviour not present in this version")


@pytest.fixture(scope="module")
def minutes_base():
    """What the moved scenarios need from the v2 setup: a temp folder, the
    header fields, a v1 file, and the stage-writer helpers."""
    global _v2, _HEAD, p_alt, alt_log, v2_datei, v2_schreiber, vorschlagen, spur
    _v2 = Path(_mkdtemp(prefix="cothinker-v2-"))
    _HEAD = dict(title="Synthetischer Termin", entity="beispiel", day="2026-09-26",
                 pslug="beispiel", participants="A, B", start="10:00")
    alt_log = m.log
    p_alt = _v2 / "alt.md"
    p_alt.write_text(consumer.HEADER.format(**_HEAD), encoding="utf-8")

    def v2_datei(name: str, mode: str = "sitzung") -> Path:
        p = _v2 / name
        p.write_text(consumer.protocol_header(**_HEAD, mode=mode), encoding="utf-8")
        return p

    def spur(pfad: Path) -> list[dict]:
        if not pfad.exists():
            return []
        return [_json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]

    def v2_schreiber(name: str, protocol, **state_kw):
        d = _v2 / name
        d.mkdir()
        zst = StageState(**state_kw)
        w = consumer.StageWriter(d, zst, log=lambda *a: None,
                                 proposals=d / "vorschlag.jsonl",
                                 events=_events.EventLog(d / "events.jsonl"))
        w.protocol = protocol
        return w, zst, d

    def vorschlagen(w, d: Path, *aktionen: dict, spoken: str = "") -> None:
        with (d / "vorschlag.jsonl").open("a", encoding="utf-8") as fh:
            for a in aktionen:
                fh.write(_json.dumps(a, ensure_ascii=False) + "\n")
        w.ingest(spoken=spoken)

    yield


@pytest.fixture(scope="module")
def sec_39(minutes_base):
    """Scenario: v2: an existing file keeps the mode it was created with"""
    global _REC
    _REC = rec = _Recorder()
    global mode_log, prot_m, prot_m2, text_m

    try:
        print("\nv2: an existing file keeps the mode it was created with")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        mode_log = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = lambda *a: mode_log.append(" ".join(map(str, a)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_m = k.open_minutes(v2_datei("modus.md", mode="arbeit"), mode="sitzung")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(357, "the frontmatter's modus wins over the flag", prot_m.mode == "arbeit",
              prot_m.mode)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(358, "the difference is logged",
              any("sitzung" in z and "arbeit" in z for z in mode_log), str(mode_log))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_m.add_entry("idee", "Vorschlag", stamp="10:10")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_m = prot_m.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(359, "the re-rendered view keeps the arbeit sections",
              "### Ideen" in text_m and "### Beschlüsse" not in text_m, text_m[:700])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        mode_log.clear()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = lambda *a: mode_log.append(" ".join(map(str, a)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_m2 = k.open_minutes(v2_datei("modus2.md", mode="folge"), mode="folge")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(360, "a matching flag is taken silently",
              prot_m2.mode == "folge" and mode_log == [], str(mode_log))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(361, "a v1 file keeps the flag's mode (no v2 frontmatter to read)",
              k.open_minutes(p_alt, mode="arbeit").mode == "arbeit")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_41(sec_39):
    """Scenario: v2: tagged entries land in the current block"""
    global _REC
    _REC = rec = _Recorder()
    global _alt_stamp, d2, gruende, jetzt, prot_a, roh_teil, st2, text_a, vor2, w2, zeilen

    try:
        print("\nv2: tagged entries land in the current block")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_a = k.open_minutes(v2_datei("a.md"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_a.append_raw("Roh eins")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w2, st2, d2 = v2_schreiber("w2", prot_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vor2 = w2.applied
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "aufgabe",
                             "text": "Kosten klaeren", "time": "10:05"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(365, "an entry before any topic opens `Beginn`",
              "### 10:05 Beginn\n- 10:05 [aufgabe] Kosten klaeren\n" in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(366, "entry_added kind=aufgabe is traced",
              any(e["event"] == "entry_added" and e.get("kind") == "aufgabe"
                  for e in spur(d2 / "events.jsonl")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(367, "a tag_entry does not count as a surface change", w2.applied == vor2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(368, "the view lists it under Aufgaben",
              "- 10:05 Kosten klaeren (Thema: Beginn)" in
              text_a.split("## Themen")[0].split("### Aufgaben")[1], text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_a.append_raw("Roh zwei")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # Pin the clock for the topic change: the "10:30" decision below must
    # predate the block, which a real-clock stamp only does after 10:30.
    try:
        jetzt = "11:00"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _alt_stamp = m._stamp_or_now
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m._stamp_or_now = lambda stamp=None: (
            jetzt if not (isinstance(stamp, str) and m._HHMM_RE.match(stamp.strip()))
            else stamp.strip())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            vorschlagen(w2, d2, {"action": "topic", "title": "Budget 2027",
                                 "markdown": "## Budget"})
        finally:
            m._stamp_or_now = _alt_stamp
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(369, "an accepted topic opens `### HH:MM Titel`",
              re.search(r"^### \d\d:\d\d Budget 2027$", text_a, re.M) is not None
              and st2.main.title == "Budget 2027", text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(370, "stamped with the time of the change (the pinned clock)",
              f"### {jetzt} Budget 2027" in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(371, "an accepted topic stays a surface change",
              w2.applied == vor2 + 1, f"{vor2} -> {w2.applied}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "topic", "title": "Zu frueh",
                             "markdown": "## Nein"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(372, "a brake-rejected topic opens no block", "Zu frueh" not in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "idee",
                             "text": "Stiftung fragen"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "idee",
                             "text": "Stiftung fragen"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(373, "the entry lands in the current block with the current time",
              re.search(r"### \d\d:\d\d Budget 2027\n- \d\d:\d\d \[idee\] Stiftung fragen\n",
                        text_a) is not None, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(374, "an identical consecutive entry is not appended twice",
              text_a.count("[idee] Stiftung fragen") == 1, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        vorschlagen(w2, d2, {"action": "decision", "time": "10:30",
                             "text": "Antrag wird gestellt"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(375, "an accepted decision becomes a beschluss entry",
              "- 10:30 [beschluss] Antrag wird gestellt\n" in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(376, "the view is re-rendered: Beschlüsse lists it",
              # this version files an entry under the LAST open block, whatever
              # its stamp says
              "- 10:30 Antrag wird gestellt (Thema: Budget 2027)"
              in text_a.split("### Beschlüsse")[1].split("### Aufgaben")[0], text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(377, "the view no longer says *noch keine* for Beschlüsse",
              "*noch keine*" not in
              text_a.split("### Beschlüsse")[1].split("### Aufgaben")[0], text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(378, "a decision's protocol entry is traced as entry_added kind=beschluss",
              any(e["event"] == "entry_added" and e.get("kind") == "beschluss"
                  for e in spur(d2 / "events.jsonl")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "idee", "text": "Dazwischen"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "beschluss",
                             "text": "  antrag   WIRD gestellt "})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(379, "the same decision as a later tag_entry is not filed twice",
              text_a.lower().count("[beschluss] antrag wird gestellt") == 1, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(380, "add_entry says why: duplicate",
              prot_a.add_entry("beschluss", "ANTRAG wird  gestellt").reason
              == "duplicate")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(381, "another decision still lands",
              prot_a.add_entry("beschluss", "Antrag wird vertagt").reason == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(382, "the overview lists the decision once",
              text_a.split("### Beschlüsse")[1].split("### Aufgaben")[0]
              .lower().count("antrag wird gestellt") == 1, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "meinung", "text": "egal"},
                    {"action": "tag_entry", "tag": "frage", "text": "   "})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gruende = [e.get("reason") for e in spur(d2 / "events.jsonl")
                   if e["event"] == "proposal_rejected"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(383, "an unknown tag is refused", "unknown_tag" in gruende, str(gruende))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(384, "an empty text is refused", "no_text" in gruende, str(gruende))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(385, "neither reached the file", "meinung" not in text_a
              and "[frage]" not in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        prot_a.append_raw("Roh drei")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        roh_teil = text_a[text_a.index("## Rohtranskript"):]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        zeilen = [z for z in roh_teil.splitlines() if z.startswith("- **")]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(386, "raw lines before, between and after the rewrites all survive, in order",
              [z.split("** ", 1)[1] for z in zeilen] == ["Roh eins", "Roh zwei", "Roh drei"],
              roh_teil)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(387, "byte-identical to what append_raw wrote",
              roh_teil == "## Rohtranskript\n" + "".join(prot_a.file._raw_lines), roh_teil)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(388, "restore_missing() finds nothing missing after the rewrites",
              prot_a.file.restore_missing() == 0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(389, "no temp file is left behind",
              [p.name for p in _v2.iterdir() if p.name.startswith(".a.md")] == [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(390, "topic blocks appear in the order they were opened",
              text_a.index("\n### 10:05 Beginn") < text_a.index("Budget 2027\n- "),
              text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_42(sec_41):
    """Scenario: v2: wikilinks are checked when the entry is written"""
    global _REC
    _REC = rec = _Recorder()
    global ev, text_a

    try:
        print("\nv2: wikilinks are checked when the entry is written")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w2.link_names = {"kostenplan", "leitfaden"}
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "aufgabe",
                             "text": "Zahlen aus [[kostenplan]] und [[gibtsnicht|Altplan]]"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_a = prot_a.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(391, "the existing link stays, the dead one becomes its label",
              "[aufgabe] Zahlen aus [[kostenplan]] und Altplan\n" in text_a, text_a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = spur(d2 / "events.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(392, "wikilink_seen count=2", any(e["event"] == "wikilink_seen"
                                           and e.get("count") == 2 for e in ev), str(ev[-4:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(393, "wikilink_dead count=1", any(e["event"] == "wikilink_dead"
                                           and e.get("count") == 1 for e in ev), str(ev[-4:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w2, d2, {"action": "tag_entry", "tag": "notiz",
                             "text": "nur [[leitfaden]]"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = spur(d2 / "events.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(394, "no wikilink_dead when nothing was dead",
              sum(1 for e in ev if e["event"] == "wikilink_dead") == 1, str(ev[-4:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(395, "no text reaches the trace",
              "Stiftung" not in (d2 / "events.jsonl").read_text(encoding="utf-8")
              and "Budget" not in (d2 / "events.jsonl").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_44(sec_42):
    """Scenario: v2: a write failure is reported, never raised"""
    global _REC
    _REC = rec = _Recorder()
    global d_ro, e, erg, erg_weg, gemeldet_v2, oeffnen, p_ro, prot_ro2, vorher_ro

    try:
        print("\nv2: a write failure is reported, never raised")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if os.geteuid() != 0:
            d_ro = _v2 / "ro"
            d_ro.mkdir()
            p_ro = d_ro / "protokoll.md"
            p_ro.write_text(consumer.protocol_header(**_HEAD, mode="sitzung"),
                            encoding="utf-8")
            gemeldet_v2 = []
            prot_ro2 = k.open_minutes(p_ro, report=gemeldet_v2.append,
                                      session_dir=_v2 / "ro-blocks")
            vorher_ro = p_ro.read_bytes()
            os.chmod(d_ro, 0o555)
            try:
                erg = prot_ro2.add_entry("aufgabe", "geht nicht")
                oeffnen = prot_ro2.open_topic("Auch nicht")
            except Exception as e:  # noqa: BLE001
                erg, oeffnen = e, e
            finally:
                os.chmod(d_ro, 0o755)
            _chk(404, "add_entry returns 'not written' instead of raising",
                  getattr(erg, "reason", None) == "not written", repr(erg))
            _chk(405, "open_topic returns False instead of raising", oeffnen is False,
                  repr(oeffnen))
            _chk(406, "the failure is reported", gemeldet_v2 and not any(gemeldet_v2),
                  str(gemeldet_v2))
            _chk(407, "the file is untouched", p_ro.read_bytes() == vorher_ro)
            _chk(408, "no temp file is left behind",
                  sorted(p.name for p in d_ro.iterdir()) == ["protokoll.md"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        erg_weg = k.open_minutes(_v2 / "fehlt2" / "p.md", session_dir=_v2 / "fehlt2-blocks").add_entry("frage", "x")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(409, "a missing file is 'not v2' (never v2 at start) and does not raise",
              erg_weg.reason in ("not v2", "not written"), erg_weg.reason)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_51(sec_44):
    """Scenario: add_entry never raises on odd input"""
    global _REC
    _REC = rec = _Recorder()
    global args, e, kw, odd_results, prot_odd

    try:
        print("\nadd_entry never raises on odd input")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_odd = k.open_minutes(v2_datei("odd.md"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        odd_results = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for args, kw in ((("aufgabe", "x"), {"names": 5}),
                         (("aufgabe", "x [[a]]"), {"names": [None, 3, "a"]}),
                         ((["aufgabe"], "x"), {}),
                         (("aufgabe", None), {}),
                         (("aufgabe", "kaputt \ud800 text"), {}),
                         (("aufgabe", "y"), {"stamp": object()}),
                         (("aufgabe", 12), {})):
            try:
                odd_results.append(prot_odd.add_entry(*args, **kw))
            except Exception as e:  # noqa: BLE001
                odd_results.append(e)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        m.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(457, "every odd call returns an EntryResult",
              all(isinstance(r, m.EntryResult) for r in odd_results), repr(odd_results))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(458, "the file is still v2 and parseable afterwards",
              _pd.is_v2(prot_odd.path.read_text(encoding="utf-8", errors="replace")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_52(sec_51):
    """Scenario: _replace fsyncs the folder after the rename (best effort)"""
    global _REC
    _REC = rec = _Recorder()
    global _dir_fsync_kaputt, _zaehl_fsync, dir_syncs, echt_fsync, erg_fs, prot_fs

    try:
        print("\n_replace fsyncs the folder after the rename (best effort)")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_fs = k.open_minutes(v2_datei("fs.md"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        echt_fsync = os.fsync
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        dir_syncs = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _zaehl_fsync(fd):
        import stat as _stat
        if _stat.S_ISDIR(os.fstat(fd).st_mode):
            dir_syncs.append(fd)
        return echt_fsync(fd)


    try:
        os.fsync = _zaehl_fsync
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            prot_fs.add_entry("notiz", "mit Ordner-fsync")
        finally:
            os.fsync = echt_fsync
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(459, "the parent directory was fsynced", len(dir_syncs) >= 1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _dir_fsync_kaputt(fd):
        import stat as _stat
        if _stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic: no dir fsync here")
        return echt_fsync(fd)


    try:
        os.fsync = _dir_fsync_kaputt
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            erg_fs = prot_fs.add_entry("notiz", "trotzdem geschrieben")
        finally:
            os.fsync = echt_fsync
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(460, "a failing directory fsync does not fail the write",
              erg_fs.reason == "" and "trotzdem geschrieben"
              in prot_fs.path.read_text(encoding="utf-8"), erg_fs.reason)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_75(sec_52):
    """Scenario: lagging entry: filed under the topic block its stamp belongs to"""
    global _REC
    _REC = rec = _Recorder()
    global _lp, _lp_before, _lp_roh, _lp_t, _lp_top

    try:
        print("\nlagging entry: filed under the topic block its stamp belongs to")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp = k.open_minutes(v2_datei("lagging.md"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.open_topic("Thema A", stamp="12:20")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("notiz", "Erste in A", stamp="12:21")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.open_topic("Thema B", stamp="12:25")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("notiz", "Erste in B", stamp="12:26")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_before = _lp.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_roh = _lp_before[_lp_before.index("## Rohtranskript"):]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("aufgabe", "Nachzuegler A", stamp="12:23")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_t = _lp.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_top = _pd.parse_topics(_lp_t)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(567, "(a) stamp 12:23 lands in block A, after its entries",
              [e.text for e in _lp_top[0].entries] == ["Erste in A", "Nachzuegler A"],
              str([[e.text for e in t.entries] for t in _lp_top]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(568, "(a) block B untouched and heading order unchanged",
              [e.text for e in _lp_top[1].entries] == ["Erste in B"]
              and [t.title for t in _lp_top] == ["Thema A", "Thema B"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(569, "(a) the new line sits before B's heading",
              # looked up within the topics section: the derived overview above
              # it also names the line, which would make a plain index() pass
              _lp_t.index("Nachzuegler A", _lp_t.index("## Themen"))
              < _lp_t.index("### 12:25 Thema B"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1567, "(a) a lagging stamp 12:23 lands in the last block, after its entries",
              [e.text for e in _lp_top[1].entries] == ["Erste in B", "Nachzuegler A"],
              str([[e.text for e in t.entries] for t in _lp_top]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1568, "(a) block A untouched and heading order unchanged (lagging line goes to the last block)",
              [e.text for e in _lp_top[0].entries] == ["Erste in A"]
              and [t.title for t in _lp_top] == ["Thema A", "Thema B"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1569, "(a) the new line sits after B's heading",
              _lp_t.rindex("Nachzuegler A") > _lp_t.index("### 12:25 Thema B"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("aufgabe", "Nachzuegler B", stamp="12:26")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_top = _pd.parse_topics(_lp.path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(570, "(b) stamp 12:26 lands in block B",
              [e.text for e in _lp_top[1].entries]
              == ["Erste in B", "Nachzuegler A", "Nachzuegler B"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("notiz", "Ohne Stempel")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_top = _pd.parse_topics(_lp.path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(571, "(c) no stamp -> last block",
              _lp_top[-1].entries[-1].text == "Ohne Stempel")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp.add_entry("notiz", "Zu frueh", stamp="11:00")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_top = _pd.parse_topics(_lp.path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(572, "no block qualifies -> last block (fallback)",
              _lp_top[-1].entries[-1].text == "Zu frueh")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lp_t = _lp.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(573, "(d) Rohtranskript unchanged",
              _lp_t[_lp_t.index("## Rohtranskript"):] == _lp_roh)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.mark.parametrize("site, occ", [
    pytest.param(357, None, id="the frontmatter's modus wins over the flag"),
    pytest.param(358, None, id='the difference is logged'),
    pytest.param(359, None, id='the re-rendered view keeps the arbeit sections'),
    pytest.param(360, None, id='a matching flag is taken silently'),
    pytest.param(361, None, id="a v1 file keeps the flag's mode (no v2 frontmatter to read)"),
])
def test_39_v2_an_existing_file_keeps_the_mode_it_was_created_(sec_39, site, occ):
    _assert_site(sec_39, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(365, None, id='an entry before any topic opens `Beginn`'),
    pytest.param(366, None, id='entry_added kind=aufgabe is traced'),
    pytest.param(367, None, id='a tag_entry does not count as a surface change'),
    pytest.param(368, None, id='the view lists it under Aufgaben'),
    pytest.param(369, None, id='an accepted topic opens `### HH:MM Titel`'),
    pytest.param(370, None, id='stamped with the time of the change (the pinned clock)'),
    pytest.param(371, None, id='an accepted topic stays a surface change'),
    pytest.param(372, None, id='a brake-rejected topic opens no block'),
    pytest.param(373, None, id='the entry lands in the current block with the current time'),
    pytest.param(374, None, id='an identical consecutive entry is not appended twice'),
    pytest.param(375, None, id='an accepted decision becomes a beschluss entry'),
    pytest.param(376, None, id='the view is re-rendered: Beschlüsse lists it'),
    pytest.param(377, None, id='the view no longer says *noch keine* for Beschlüsse'),
    pytest.param(378, None, id="a decision's protocol entry is traced as entry_added kind=beschluss"),
    pytest.param(379, None, id='the same decision as a later tag_entry is not filed twice'),
    pytest.param(380, None, id='add_entry says why: duplicate'),
    pytest.param(381, None, id='another decision still lands'),
    pytest.param(382, None, id='the overview lists the decision once'),
    pytest.param(383, None, id='an unknown tag is refused'),
    pytest.param(384, None, id='an empty text is refused'),
    pytest.param(385, None, id='neither reached the file'),
    pytest.param(386, None, id='raw lines before, between and after the rewrites all survive, in order'),
    pytest.param(387, None, id='byte-identical to what append_raw wrote'),
    pytest.param(388, None, id='restore_missing() finds nothing missing after the rewrites'),
    pytest.param(389, None, id='no temp file is left behind'),
    pytest.param(390, None, id='topic blocks appear in the order they were opened'),
])
def test_41_v2_tagged_entries_land_in_the_current_block(sec_41, site, occ):
    _assert_site(sec_41, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(391, None, id='the existing link stays, the dead one becomes its label'),
    pytest.param(392, None, id='wikilink_seen count=2'),
    pytest.param(393, None, id='wikilink_dead count=1'),
    pytest.param(394, None, id='no wikilink_dead when nothing was dead'),
    pytest.param(395, None, id='no text reaches the trace'),
])
def test_42_v2_wikilinks_are_checked_when_the_entry_is_written(sec_42, site, occ):
    _assert_site(sec_42, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(404, None, id="add_entry returns 'not written' instead of raising"),
    pytest.param(405, None, id='open_topic returns False instead of raising'),
    pytest.param(406, None, id='the failure is reported'),
    pytest.param(407, None, id='the file is untouched'),
    pytest.param(408, None, id='no temp file is left behind'),
    pytest.param(409, None, id="a missing file is 'not v2' (never v2 at start) and does not raise"),
])
def test_44_v2_a_write_failure_is_reported_never_raised(sec_44, site, occ):
    _assert_site(sec_44, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(457, None, id='every odd call returns an EntryResult'),
    pytest.param(458, None, id='the file is still v2 and parseable afterwards'),
])
def test_51_add_entry_never_raises_on_odd_input(sec_51, site, occ):
    _assert_site(sec_51, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(459, None, id='the parent directory was fsynced'),
    pytest.param(460, None, id='a failing directory fsync does not fail the write'),
])
def test_52_replace_fsyncs_the_folder_after_the_rename_best_ef(sec_52, site, occ):
    _assert_site(sec_52, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(567, None, id='(a) stamp 12:23 lands in block A, after its entries', marks=_ABSENT),
    pytest.param(568, None, id='(a) block B untouched and heading order unchanged', marks=_ABSENT),
    pytest.param(569, None, id="(a) the new line sits before B's heading", marks=_ABSENT),
    pytest.param(1567, None, id='(a) a lagging stamp 12:23 lands in the last block, after its entries'),
    pytest.param(1568, None, id='(a) block A untouched and heading order unchanged (lagging line goes to the last block)'),
    pytest.param(1569, None, id="(a) the new line sits after B's heading"),
    pytest.param(570, None, id='(b) stamp 12:26 lands in block B'),
    pytest.param(571, None, id='(c) no stamp -> last block'),
    pytest.param(572, None, id='no block qualifies -> last block (fallback)'),
    pytest.param(573, None, id='(d) Rohtranskript unchanged'),
])
def test_75_lagging_entry_filed_under_the_topic_block_its_stam(sec_75, site, occ):
    _assert_site(sec_75, site, occ)


def _scenarios_with_expected_failures():
    """Names of the test functions that carry at least one xfail case."""
    names = set()
    for name, fn in list(globals().items()):
        if not name.startswith("test_"):
            continue
        for mark in getattr(fn, "pytestmark", []):
            if mark.name != "parametrize":
                continue
            for param in mark.args[1]:
                if any(mk.name == "xfail" for mk in getattr(param, "marks", ())):
                    names.add(name)
    return names


_XFAIL_SCENARIOS = _scenarios_with_expected_failures()


@pytest.fixture(autouse=True)
def _setup_error_guard(request):
    """Scenarios without expected failures must have recorded no setup error."""
    global _SCENARIO_EXPECTS_FAILURES
    _SCENARIO_EXPECTS_FAILURES = request.node.originalname in _XFAIL_SCENARIOS
    yield
