# CoThinker — tests of the level-2 policy runner
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""blocks/decisions_tasks_questions/policy_runner.py against the REAL StageState and StageWriter.

In-process: `decide_fn` is a scripted function (no backend, no network),
both clocks are injected. Every rule is a function returning bool that
builds its own fresh stage, so the mutants at the end can swap a broken
runner method in and prove that the named rule goes red. All fixture
sentences are invented.

    python3 -m pytest -q tests/test_policy_runner.py
"""
from __future__ import annotations

import atexit
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
import consumer  # noqa: E402
import blocks.decisions_tasks_questions.decide as d  # noqa: E402
import blocks.decisions_tasks_questions.policy as pol  # noqa: E402
import blocks.decisions_tasks_questions.policy_runner as pr  # noqa: E402
import stage_state as ss  # noqa: E402
from shared.events import EventLog  # noqa: E402
from blocks.decisions_tasks_questions.questions import CONFIRM_ID, MODE_ID  # noqa: E402

START = 1000.0
WALL = datetime(2026, 9, 26, 14, 7, 0)
SATZ = "Wir sollten die Tabelle mit den Raumkosten bis Freitag fertig haben"


def ans(**ps):
    """Level-1 answer entries in decide.py's shape."""
    return [{"question_id": k, "p": v, "confidence": 0.9, "ms": 1.0}
            for k, v in ps.items()]


class Env:
    """One fresh stage, writer, trace and runner in a temp dir."""

    def __init__(self, mode="sitzung", allowed_roots=None, log=None,
                 real_protocol=False):
        self.tmp = Path(tempfile.mkdtemp())
        atexit.register(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = self.tmp / "sessions" / "2026-09-26-probe"
        self.run_dir.mkdir(parents=True)
        self.t = [START]
        self.state = ss.StageState(now=lambda: self.t[0], clock=lambda: WALL)
        self.trace_path = self.run_dir / "stage-events.jsonl"
        self.writer = consumer.StageWriter(
            self.run_dir, self.state, log=lambda *a: None,
            proposals=self.run_dir / "vorschlag.jsonl",
            events=EventLog(self.trace_path))
        # Level 2 acts only while the writer files into a v2 protocol; the
        # runner never touches it beyond "is there one", so a stand-in does
        # -- except where the page's backlink entry is checked.
        self.writer.protocol = object()
        if real_protocol:
            self.protocol_path = self.run_dir / "protokoll.md"
            self.protocol_path.write_text(consumer.protocol_header(
                title="Synthetischer Termin", entity="probe",
                day="2026-09-26", pslug="probe", participants="A, B",
                start="10:00", mode=mode), encoding="utf-8")
            self.writer.protocol = consumer.Protocol(self.protocol_path,
                                                     self.tmp, mode=mode)
        self.script: dict = {}       # question id -> raw backend answer
        self.raise_on = None         # (question id, exception)
        self.calls: list = []        # question ids per decide_fn call
        self.logs: list = []
        kw = {} if log is None else {"log": log}
        if log is None:
            kw["log"] = lambda *a: self.logs.append(a)
        self.runner = pr.PolicyRunner(
            self.writer, mode=mode, session_dir=self.run_dir, backend=None,
            decide_fn=self.decide, now=lambda: self.t[0],
            clock=lambda: WALL, allowed_roots=allowed_roots,
            project_dir=self.tmp, **kw)

    def decide(self, snap, questions, *, backend=None):
        ids = [q.id for q in questions]
        self.calls.append(ids)
        if self.raise_on and self.raise_on[0] in ids:
            raise self.raise_on[1]
        return d.Result(answers=d.interpret(questions, self.script, 1.0),
                        backend="fake")

    def hear(self, block=SATZ, reason="pause", **ps):
        self.runner.on_result(d.Result(answers=ans(**ps), backend="fake"),
                              d.State(block=block), reason)

    def events(self, name=None, **match):
        if not self.trace_path.exists():
            return []
        rows = [json.loads(l) for l in
                self.trace_path.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        return [r for r in rows if (name is None or r["event"] == name)
                and all(r.get(k) == v for k, v in match.items())]

    def kinds(self):
        return [p.kind for p in self.state.pending]

    def docs(self):
        """The topic pages (MM-G D4: `<session>/entwuerfe/*.md`)."""
        return sorted(q for q in self.tmp.rglob("*.md")
                      if q.parent.name == "entwuerfe")


# ------------------------------------------------------------ the rules
def rule_autonomous_entry() -> bool:
    e = Env()
    e.hear(aufgabe=0.8)
    entry = [p for p in e.state.pending if p.kind == "entry"]
    # MM-L: the label names the WHAT -- prefix by tag, <= 3 content words.
    if len(entry) != 1 or entry[0].label != "Aufgabe: Tabelle Raumkosten Freitag":
        return False
    note = e.runner.take_turn().note
    if "AUFTRAG A1" not in note or "Tag `aufgabe`" not in note:
        return False
    if e.runner.marker_for("A1") != "*(autonom)*":
        return False
    e.runner.delivered("A1")
    return ("entry" not in e.kinds()
            and bool(e.events("pending_end", id=entry[0].id,
                              outcome="arrived")))


def rule_end_turn_settles() -> bool:
    e = Env()
    e.hear(aufgabe=0.8)
    e.runner.take_turn()
    e.runner.delivered("A1")
    e.runner.end_turn(True)
    e.hear(aufgabe=0.8)                               # A2
    p2 = e.state.pending[-1].id
    if "AUFTRAG A2" not in e.runner.take_turn().note:
        return False
    e.runner.end_turn(True)
    e.hear(aufgabe=0.8)                               # A3
    p3 = e.state.pending[-1].id
    e.runner.take_turn()
    e.runner.end_turn(False)
    return (not e.state.pending
            and bool(e.events("pending_end", id=p2, outcome="unanswered"))
            and bool(e.events("pending_end", id=p3, outcome="failed")))


def rule_topic_braked() -> bool:
    e = Env()
    if e.state.apply({"action": "topic", "title": "Raumplanung",
                      "markdown": "- erster Punkt"}):
        return False
    e.hear(neues_thema=0.95)
    if "topic" in e.kinds() or e.runner.take_turn().note:
        return False
    e.t[0] += ss.TOPIC_WINDOW_S + 1          # past hold time AND window
    e.hear(neues_thema=0.95)
    return ("topic" in e.kinds()
            and "AUFTRAG A1" in e.runner.take_turn().note)


def _open_doc_ask(e: Env) -> bool:
    e.hear(dokument_kandidat=0.9)
    q = e.state.question
    return q is not None and q.state == "open"


def rule_ask_confirm() -> bool:
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    if q.text != "Eigenes Dokument dazu anlegen?":
        return False
    if not e.events("ask_opened", id=q.id):
        return False
    if not e.events("pending_end", kind="question", outcome="arrived"):
        return False
    if "question" in e.kinds():
        return False
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(zuruf_robo=0.8)
    if q.state != "resolved":
        return False
    docs = e.docs()
    if len(docs) != 1 or docs[0].parent != e.run_dir / "entwuerfe":
        return False
    if f"*(bestätigt {WALL:%H:%M})*" not in docs[0].read_text("utf-8"):
        return False
    if "document" not in e.kinds():
        return False
    note = e.runner.take_turn().note
    return (str(docs[0]) in note and docs[0].stem in note
            and bool(e.events("ask_confirmed", id=q.id)))


def rule_ask_no_answer() -> bool:
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.hear(zuruf_robo=0.8)             # confirm is a gap
    if q.state != "open":
        return False
    e.hear(zuruf_robo=0.8)
    return (q.state == "resolved" and not e.docs()
            and not e.runner.take_turn().note
            and bool(e.events("ask_discarded", id=q.id, reason="no_answer")))


def rule_ask_rejected() -> bool:
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.script = {CONFIRM_ID: {"noul": 0.1}}
    e.hear(zuruf_robo=0.8)
    return (q.state == "resolved" and not e.docs()
            and bool(e.events("ask_discarded", id=q.id, reason="rejected")))


def rule_one_ask() -> bool:
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    parked = e.state.parked
    e.hear(dokument_kandidat=0.9)
    return (e.state.question is q and q.state == "open"
            and e.state.parked == parked
            and len(e.events("ask_opened")) == 1)


def rule_write_outside_asks() -> bool:
    anderswo_dir = Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, anderswo_dir, ignore_errors=True)
    anderswo = anderswo_dir / "elsewhere"
    e = Env(mode="arbeit", allowed_roots=(anderswo,))
    e.hear(dokument_kandidat=0.95)
    q = e.state.question
    return (not e.docs() and not list(anderswo.parent.rglob("*.md"))
            and q is not None and q.state == "open"
            and q.text == "Außerhalb der Sitzung speichern?"
            and not e.runner.take_turn().note)


def rule_wake() -> bool:
    e = Env()
    e.hear(block="Robo zeig uns bitte die Liste der offenen Punkte",
           reason="pause", zuruf_robo=0.95)
    t = e.runner.take_turn()
    if not ("main" in e.kinds() and t.wake
            and "Liste der offenen Punkte" in t.spoken):
        return False
    w = Env()
    w.hear(block="Robo zeig uns bitte die Liste der offenen Punkte",
           reason="wake", zuruf_robo=0.95)
    t2 = w.runner.take_turn()
    return "main" not in w.kinds() and not t2.wake and not t2.note


def rule_mode_check() -> bool:
    e = Env(mode="sitzung")
    e.script = {MODE_ID: {"choice": "arbeit", "confidence": 0.8}}
    for i in range(3):
        e.t[0] = START + 10 * (i + 1)
        e.hear()
    if e.state.mirror != "":
        return False
    e.t[0] = START + pol.MODE_CHECK_S + 1
    e.hear()
    if e.state.mirror != "Robo hört ein Arbeitsmeeting — umschalten?":
        return False
    if not e.events("mode_mismatch", mode="arbeit"):
        return False
    asked = sum(MODE_ID in ids for ids in e.calls)
    for _ in range(3):
        e.t[0] += 10
        e.hear()
    return asked == 4 and sum(MODE_ID in ids for ids in e.calls) == 4


def rule_decide_error() -> bool:
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.raise_on = (CONFIRM_ID, d.DecideError("x", "timeout"))
    try:
        e.hear(zuruf_robo=0.8)
    except Exception:                       # noqa: BLE001
        return False
    return (q.state == "open" and e.runner.policy.ask is not None
            and bool(e.events("policy_error", reason="timeout")))


def rule_canary() -> bool:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        e = Env(log=print)
        block = "KANARIENVOGEL sagt wir brauchen noch einen Termin im Mai"
        e.hear(block=block, aufgabe=0.8, dokument_kandidat=0.9)
        # A failing extra call whose message carries the canary.
        e.raise_on = (CONFIRM_ID, RuntimeError("KANARIENVOGEL " + block))
        e.hear(block=block, zuruf_robo=0.8)
        # The confirm path too: a document skeleton from a canary block.
        e.raise_on = None
        e.script = {CONFIRM_ID: {"noul": 0.9}}
        e.hear(block=block, zuruf_robo=0.8)
        t = e.runner.take_turn()
        e.runner.end_turn(True)
    stage = (e.run_dir / "buehne.json").read_text(encoding="utf-8")
    docs = "".join(p.read_text("utf-8") for p in e.docs())
    return ("KANARIENVOGEL" not in out.getvalue()
            and "KANARIENVOGEL" not in e.trace_path.read_text("utf-8")
            and "KANARIENVOGEL" not in stage
            and "KANARIENVOGEL" not in docs
            and "KANARIENVOGEL" not in t.spoken
            and "KANARIENVOGEL" in t.note
            and bool(e.events("policy_error", reason="RuntimeError")))


def rule_no_reask() -> bool:
    """I1: the review's probe. A no in the block that still carries the
    document signal: no second ask in that block, none for the next
    ASK_COOLDOWN_BLOCKS blocks -- then the signal may ask again."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.script = {CONFIRM_ID: {"noul": 0.1}}
    e.hear(zuruf_robo=0.8, dokument_kandidat=0.9)
    if q.state != "resolved" or e.state.question is not q:
        return False
    e.script = {}
    for _ in range(pol.ASK_COOLDOWN_BLOCKS):
        e.hear(dokument_kandidat=0.9)
        if e.state.question is not q or len(e.events("ask_opened")) != 1:
            return False
    e.hear(dokument_kandidat=0.9)
    return (len(e.events("ask_opened")) == 2
            and e.state.question is not q
            and e.state.question.state == "open")


def rule_other_signal_asks() -> bool:
    """I1: the settling block asks nothing at all; a DIFFERENT signal may
    ask from the next block on."""
    e = Env(mode="arbeit")
    e.hear(dokument_kandidat=0.6)
    q = e.state.question
    if q is None or q.state != "open":
        return False
    e.script = {CONFIRM_ID: {"noul": 0.1}}
    e.hear(zuruf_robo=0.8, dokument_kandidat=0.6, recherche_kandidat=0.6)
    if e.state.question is not q or len(e.events("ask_opened")) != 1:
        return False
    e.script = {}
    e.hear(recherche_kandidat=0.6)
    neu = e.state.question
    return (neu is not q and neu.state == "open"
            and neu.text == "Soll Robo das nachschlagen?")


FRAGE_BLOCK = "Die Hausordnung für den Probenraum braucht ein eigenes Papier"
JA_BLOCK = "Ja Robo mach das bitte gleich so wie besprochen"


def rule_confirm_anchor() -> bool:
    """I2: the confirmed order names the block that raised the question,
    not the "Ja" that answered it."""
    e = Env()
    e.hear(block=FRAGE_BLOCK, dokument_kandidat=0.9)
    q = e.state.question
    if q is None or q.state != "open":
        return False
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(block=JA_BLOCK, zuruf_robo=0.8)
    note = e.runner.take_turn().note
    return (f'ab "{pol.anchor(FRAGE_BLOCK)}"' in note
            and "Ja Robo mach" not in note)


AGENT_FRAGE = "Wer übernimmt die Moderation am Donnerstag?"


def rule_atomic_ask() -> bool:
    """I3: an agent question lands between the runner's refresh and its
    apply. Robo's own question must not park it (or be parked), and no
    pending may be left behind."""
    e = Env()
    r = e.runner
    original = r._refresh

    def refresh_then_agent():
        original()
        if sys._getframe(1).f_code.co_name == "_ask":
            e.state.apply({"action": "question", "text": AGENT_FRAGE})

    r._refresh = refresh_then_agent
    parked = e.state.parked
    e.hear(dokument_kandidat=0.9)
    q = e.state.question
    return (e.state.parked == parked and not e.state.parked_entries
            and q is not None and q.text == AGENT_FRAGE
            and q.state == "open" and r.policy.ask is None
            and "question" not in e.kinds()
            and not e.events("ask_opened")
            and bool(e.events("policy_refused", kind="ask",
                              reason="question_open")))


def rule_settle_resolves_on_error() -> bool:
    """M-a: whatever raises after answer(), the stage question is
    resolved -- and with the right text: "Ja" / "verworfen"."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question

    original = e.runner._trace

    def kaputt(event, **k):
        if event.startswith("ask_"):
            raise RuntimeError("trace down")
        original(event, **k)

    e.runner._trace = kaputt
    e.script = {CONFIRM_ID: {"noul": 0.1}}
    e.hear(zuruf_robo=0.8)
    return q.state == "resolved" and q.resolution == "verworfen"


def rule_resolution_texts() -> bool:
    """M-f: a confirmed ask resolves "Ja", a discarded one "verworfen"."""
    ja = Env()
    if not _open_doc_ask(ja):
        return False
    qa = ja.state.question
    ja.script = {CONFIRM_ID: {"noul": 0.9}}
    ja.hear(zuruf_robo=0.8)
    nein = Env()
    if not _open_doc_ask(nein):
        return False
    qn = nein.state.question
    nein.hear()
    nein.hear()                                      # expires
    return (qa.state == "resolved" and qa.resolution == "Ja"
            and qn.state == "resolved" and qn.resolution == "verworfen")


def rule_doc_name_taken() -> bool:
    """M-b: a taken page name (a restart, the same title twice) moves on
    to `<slug>-2`, and the old file stays untouched. No topic yet: the
    slug falls back to `seite-HHMM`."""
    e = Env(mode="arbeit")
    alt = e.run_dir / "entwuerfe" / f"seite-{WALL:%H%M}.md"
    alt.parent.mkdir(parents=True)
    alt.write_text("alter Inhalt\n", encoding="utf-8")
    e.hear(dokument_kandidat=0.95)
    neu = e.run_dir / "entwuerfe" / f"seite-{WALL:%H%M}-2.md"
    return (alt.read_text("utf-8") == "alter Inhalt\n" and neu.exists()
            and str(neu) in e.runner.take_turn().note)


def rule_autonomous_document() -> bool:
    """M-f: arbeit, p >= 0.8: a skeleton marked *(autonom)*, a pending,
    and an order naming the file."""
    e = Env(mode="arbeit")
    e.hear(dokument_kandidat=0.85)
    docs = e.docs()
    if len(docs) != 1 or "*(autonom)*" not in docs[0].read_text("utf-8"):
        return False
    if e.state.question is not None or "document" not in e.kinds():
        return False
    note = e.runner.take_turn().note
    return ("AUFTRAG A1" in note and str(docs[0]) in note
            and f"[[{docs[0].stem}]]" in note)


def rule_confirmed_topic_braked() -> bool:
    """M-c: a confirmed STAGE_MOVING action passes the brake too."""
    e = Env()
    if e.state.apply({"action": "topic", "title": "Raumplanung",
                      "markdown": "- erster Punkt"}):
        return False
    if e.state.apply({"action": "question", "text": "Neues Thema anlegen?"}):
        return False
    q = e.state.question
    ask = pol.OpenAsk(
        action=pol.Action("topic_block", "neues_thema", 0.6, False),
        stage_id=q.id)
    ask.block = SATZ
    e.runner.policy.ask = ask
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(zuruf_robo=0.8)
    return (q.state == "resolved" and "topic" not in e.kinds()
            and not e.runner.take_turn().note
            and bool(e.events("policy_refused", kind="topic_block",
                              reason="too_soon")))


def rule_close_stops() -> bool:
    """Fix 1: after close() on_result is a no-op -- no pending, no stage
    question, no skeleton, no order -- and the orders no agent will take
    any more are settled, so nothing freezes into the final stage file."""
    e = Env()
    e.hear(aufgabe=0.8)                              # A1, queued, not taken
    p1 = e.state.pending[-1].id
    e.runner.close()
    if e.state.pending or not e.events("pending_end", id=p1,
                                       outcome="shutdown"):
        return False
    q_vorher = e.state.question
    calls = len(e.calls)
    e.hear(aufgabe=0.8, neues_thema=0.95, zuruf_robo=0.95)
    e.hear(dokument_kandidat=0.9)                    # would ask (rule 4)
    return (not e.state.pending and e.state.question is q_vorher
            and not e.docs() and len(e.calls) == calls
            and e.runner.take_turn() == pr.TurnOrders()
            and not e.events("policy_action", id="A2"))


def rule_close_in_flight() -> bool:
    """Fix 1: close() lands while an action is between its announce and
    its order: the pending is settled at once and no order is queued."""
    e = Env()
    original = e.writer.announce

    def announce_then_close(*a, **k):
        pid = original(*a, **k)
        e.runner.close()
        return pid

    e.writer.announce = announce_then_close
    e.hear(aufgabe=0.8)
    return (not e.state.pending
            and bool(e.events("pending_end", outcome="shutdown"))
            and e.runner.take_turn() == pr.TurnOrders())


def rule_turn_scoped_ids() -> bool:
    """Fix 3: only ids sent in the RUNNING turn are honoured. An id from
    turn 1 reused in turn 2 gets no marker and delivers nothing; a queued
    but not yet sent id neither."""
    e = Env()
    e.hear(aufgabe=0.8)                              # A1
    if e.runner.marker_for("A1") != "":              # queued, not sent yet
        return False
    e.runner.take_turn()
    if e.runner.marker_for("A1") != "*(autonom)*":
        return False
    e.runner.end_turn(True)
    if e.runner.marker_for("A1") != "":              # turn is over
        return False
    e.hear(aufgabe=0.8)                              # A2
    p2 = e.state.pending[-1].id
    e.runner.take_turn()
    if e.runner.marker_for("A1") != "":              # reused old id
        return False
    e.runner.delivered("A1")
    if p2 not in [p.id for p in e.state.pending]:
        return False
    e.runner.end_turn(True)
    return (len(e.events("pending_end", outcome="arrived")) == 0
            and bool(e.events("pending_end", id=p2, outcome="unanswered")))


WAKE_BLOCK = "Robo zeig uns bitte die Liste der offenen Punkte"


def rule_wake_answered() -> bool:
    """Minor 3: a wake order has no proposal of its own -- a turn that
    answered the call-out settles its pending `arrived`; one that did not
    leaves it to the ordinary `unanswered`."""
    ja = Env()
    ja.hear(block=WAKE_BLOCK, reason="pause", zuruf_robo=0.95)
    pj = [p.id for p in ja.state.pending if p.kind == "main"]
    ja.runner.take_turn()
    ja.runner.end_turn(True, answered=True)
    nein = Env()
    nein.hear(block=WAKE_BLOCK, reason="pause", zuruf_robo=0.95)
    pn = [p.id for p in nein.state.pending if p.kind == "main"]
    nein.runner.take_turn()
    nein.runner.end_turn(True, answered=False)
    # An entry order of an answered turn stays `unanswered`: answered is
    # about the main surface only.
    en = Env()
    en.hear(aufgabe=0.8)
    pe = en.state.pending[-1].id
    en.runner.take_turn()
    en.runner.end_turn(True, answered=True)
    return (len(pj) == 1 and len(pn) == 1
            and bool(ja.events("pending_end", id=pj[0], outcome="arrived"))
            and bool(nein.events("pending_end", id=pn[0],
                                 outcome="unanswered"))
            and bool(en.events("pending_end", id=pe, outcome="unanswered")))


def rule_v1_fallback_stops() -> bool:
    """Minor 5: once the writer let go of the protocol (a mid-run switch
    back to v1), level 2 does nothing at all."""
    e = Env()
    e.writer.protocol = None
    e.hear(aufgabe=0.8, zuruf_robo=0.95)
    e.hear(dokument_kandidat=0.9)
    return (not e.state.pending and e.state.question is None
            and not e.docs() and e.calls == []
            and e.runner.take_turn() == pr.TurnOrders())


def _asks_again_only_after_cooldown(e: Env, q) -> bool:
    """The settling block (the next one heard) and the ASK_COOLDOWN_BLOCKS
    blocks after it ask nothing for the document signal; then it asks."""
    e.script = {}
    for _ in range(1 + pol.ASK_COOLDOWN_BLOCKS):
        e.hear(dokument_kandidat=0.9)
        if e.state.question is not q or len(e.events("ask_opened")) != 1:
            return False
    e.hear(dokument_kandidat=0.9)
    return (len(e.events("ask_opened")) == 2
            and e.state.question is not q
            and e.state.question.state == "open")


def rule_yes_cooldown() -> bool:
    """Final review 2: a yes cools the signal down too -- the round just
    answered, the question must not come straight back."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(zuruf_robo=0.8, dokument_kandidat=0.9)
    if q.state != "resolved" or q.resolution != "Ja":
        return False
    e.script = {}
    for _ in range(pol.ASK_COOLDOWN_BLOCKS):
        e.hear(dokument_kandidat=0.9)
        if e.state.question is not q or len(e.events("ask_opened")) != 1:
            return False
    e.hear(dokument_kandidat=0.9)
    return (len(e.events("ask_opened")) == 2
            and e.state.question is not q)


def rule_stage_closed_cooldown() -> bool:
    """Final review 2: the agent (or a timeout) closes level 2's question.
    That ends the ask too: nothing new in the block that notices it, and
    the signal cools down like after an answer."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.state.resolve(q.id, "erledigt")               # someone else closed it
    if not _asks_again_only_after_cooldown(e, q):
        return False
    return bool(e.events("ask_discarded", id=q.id, reason="stage_closed"))


def rule_open_ask_id() -> bool:
    """Final review 1: the runner names its own open question for the
    prompt, and nothing once it is settled."""
    e = Env()
    if e.runner.open_ask_id() != "":
        return False
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    if e.runner.open_ask_id() != q.id:
        return False
    e.script = {CONFIRM_ID: {"noul": 0.1}}
    e.hear(zuruf_robo=0.8)
    return e.runner.open_ask_id() == ""


def rule_close_clears_ask() -> bool:
    """Final review 3: close() with level 2's question open resolves it
    "verworfen" -- nobody will evaluate the answer any more -- instead of
    leaving it frozen on the stage."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.runner.close()
    stage = json.loads((e.run_dir / "buehne.json").read_text("utf-8"))
    return (q.state == "resolved" and q.resolution == "verworfen"
            and e.runner.policy.ask is None
            and e.runner.open_ask_id() == ""
            and bool(e.events("ask_discarded", id=q.id, reason="shutdown"))
            and "verworfen" in json.dumps(stage, ensure_ascii=False))


def rule_close_leaves_foreign_question() -> bool:
    """Final review 3: an ask whose stage question someone else already
    replaced is dropped without touching the stage's question."""
    e = Env()
    if not _open_doc_ask(e):
        return False
    q = e.state.question
    e.state.resolve(q.id, "erledigt")
    e.runner.close()
    return (q.resolution == "erledigt" and e.runner.policy.ask is None
            and not e.events("ask_discarded", reason="shutdown")
            and bool(e.events("ask_discarded", id=q.id,
                              reason="stage_closed")))


def _wiki_env(mode: str, titel: str) -> Env:
    """A real v2 protocol, a wiki layer over a synthetic project (the temp
    dir), a topic block and the same topic on the stage."""
    from blocks.knowledge_link import KnowledgeLinkBlock
    e = Env(mode=mode, real_protocol=True)
    (e.tmp / "kosten.md").write_text("---\ntags: [finanzen]\n---\nZahlen.\n",
                                     encoding="utf-8")
    # The project's index note: stem == project folder name.
    (e.tmp / f"{e.tmp.name}.md").write_text("Start.\n", encoding="utf-8")
    e.writer.knowledge_link = KnowledgeLinkBlock()
    e.writer.knowledge_link.use_project(e.tmp)
    e.writer.protocol.open_topic(titel)
    if e.state.apply({"action": "topic", "title": titel,
                      "markdown": "- erster Punkt"}):
        raise AssertionError("topic refused")
    return e


def _current_block(e: Env) -> str:
    """The protocol's last topic block (up to the raw transcript)."""
    text = e.protocol_path.read_text("utf-8")
    text = text.split("## Rohtranskript")[0]
    return text.rsplit("\n### ", 1)[-1]


def rule_page_backlink() -> bool:
    """MM-G D4-D6: the document action creates the topic page in
    `entwuerfe/`, links it (neighbour + index note top-up), and the
    consumer -- not the agent -- files the backlink into the current topic
    block. The order asks the agent to fill the page, not to file."""
    e = _wiki_env("arbeit", "Kosten der Obstwiese")
    e.hear(dokument_kandidat=0.85)
    docs = e.docs()
    if [d.name for d in docs] != ["kosten-der-obstwiese.md"]:
        return False
    seite = docs[0].read_text("utf-8")
    kopf = ("---\ntype: topic\ncreated: 2026-09-26 14:07\n"
            "sources: [sessions/2026-09-26-probe/protokoll.md]\n"
            "contributors: [robo]\n---\n")
    if not (seite.startswith(kopf)
            and "\n# Kosten der Obstwiese\n\n*(autonom)*\n" in seite
            and f"Verwandt: [[kosten]], [[{e.tmp.name}]]" in seite):
        return False
    block = _current_block(e)
    if "Kosten der Obstwiese" not in block.splitlines()[0]:
        return False
    if re.search(r"\[notiz\] Themenseite \[\[kosten-der-obstwiese\]\] "
                 r"\*\(autonom\)\*", block) is None:
        return False
    created = e.events("wiki_create")
    if len(created) != 1 or created[0].get("links") != 2 \
            or set(created[0]) != {"at", "event", "links"}:
        return False
    note = e.runner.take_turn().note
    zeile = note.split("AUFTRAG A1")[1].split("\n")[0] if "AUFTRAG A1" in note else ""
    if not (zeile and str(docs[0]) in zeile
            and "KEINEN Protokolleintrag" in zeile):
        return False
    pid = [q.id for q in e.state.pending if q.kind == "document"]
    e.runner.end_turn(True)
    return (len(pid) == 1
            and bool(e.events("pending_end", id=pid[0], outcome="arrived")))


def rule_page_name_of_a_note() -> bool:
    """MM-G D5: a title whose slug is an existing project note's stem
    would make the backlink ambiguous -- the page moves on to `-2`."""
    e = _wiki_env("arbeit", "Kosten")
    e.hear(dokument_kandidat=0.85)
    return ([d.name for d in e.docs()] == ["kosten-2.md"]
            and "[[kosten-2]]" in _current_block(e))


def rule_page_order() -> bool:
    """MM-G D7: the Zuruf runs the same page path in every mode -- in
    `sitzung` too, without a question, marked *(bestätigt HH:MM)*; the
    fill order rides along with the next turn. Closed: nothing."""
    e = _wiki_env("sitzung", "Kosten der Obstwiese")
    e.runner.page_order("Robo, leg dafuer eine Seite an")
    docs = e.docs()
    mark = f"*(bestätigt {WALL:%H:%M})*"
    if len(docs) != 1 or mark not in docs[0].read_text("utf-8"):
        return False
    if e.state.question is not None or "document" not in e.kinds():
        return False
    if f"Themenseite [[{docs[0].stem}]] {mark}" not in _current_block(e):
        return False
    acts = e.events("policy_action", kind="document")
    if not (len(acts) == 1 and acts[0].get("signal") == "zuruf_seite"
            and acts[0].get("autonomous") is False):
        return False
    if "AUFTRAG A1" not in e.runner.take_turn().note:
        return False
    zu = _wiki_env("sitzung", "Kosten der Obstwiese")
    zu.runner.close()
    zu.runner.page_order("Robo, leg dafuer eine Seite an")
    return not zu.docs() and not zu.state.pending


def rule_page_failure_costs_page_only() -> bool:
    """Never raise: a page that cannot be created (the entwuerfe name is a
    FILE) is a `policy_refused` trace, no pending, no order."""
    e = Env(mode="arbeit")
    (e.run_dir / "entwuerfe").write_text("im Weg\n", encoding="utf-8")
    e.hear(dokument_kandidat=0.95)
    return (not e.docs() and "document" not in e.kinds()
            and not e.runner.take_turn().note
            and bool(e.events("policy_refused", kind="document")))



def rule_page_order_supersedes_ask() -> bool:
    """Review 7: level 2's own question "Eigenes Dokument dazu anlegen?"
    is answered by the Zuruf -- closed "Ja" with the usual settle (the
    signal cools down), so a later "Ja" cannot create a second page."""
    e = _wiki_env("sitzung", "Kosten der Obstwiese")
    e.hear(dokument_kandidat=0.9)
    q = e.state.question
    if q is None or q.state != "open" or e.runner.policy.ask is None:
        return False
    e.runner.page_order("Robo, leg dafuer eine Seite an")
    if not (q.state == "resolved" and q.resolution == "Ja"
            and e.runner.policy.ask is None and len(e.docs()) == 1
            and e.events("ask_discarded", id=q.id, reason="superseded")):
        return False
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(zuruf_robo=0.8)                   # a late "Ja": nothing to confirm
    if len(e.docs()) != 1 or e.events("ask_confirmed"):
        return False
    # Settled between blocks: the "Ja" block above was the first of the
    # ASK_COOLDOWN_BLOCKS cooled-down blocks; after them it may ask again.
    e.script = {}
    for _ in range(pol.ASK_COOLDOWN_BLOCKS - 1):
        e.hear(dokument_kandidat=0.9)
        if len(e.events("ask_opened")) != 1:
            return False
    e.hear(dokument_kandidat=0.9)
    return len(e.events("ask_opened")) == 2


def rule_page_title_from_protocol() -> bool:
    """Review 2: the page is about the protocol's CURRENT topic block
    (link suffix and marker stripped), not the stage's title."""
    e = _wiki_env("arbeit", "Kosten der Obstwiese")
    e.writer.protocol.open_topic("Pflege der Wiese -> [[pflege]] *(autonom)*")
    e.hear(dokument_kandidat=0.85)
    docs = e.docs()
    return ([d.name for d in docs] == ["pflege-der-wiese.md"]
            and "\n# Pflege der Wiese\n" in docs[0].read_text("utf-8")
            and e.state.main.title == "Kosten der Obstwiese"
            and "Themenseite [[pflege-der-wiese]]" in _current_block(e))


def _one_page(e: Env) -> bool:
    """Exactly one page, one backlink, one `page_exists` refusal."""
    return (len(e.docs()) == 1
            and e.protocol_path.read_text("utf-8").count("Themenseite [[") == 1
            and len(e.events("policy_refused", kind="document",
                             reason="page_exists")) == 1)


def rule_page_confirm_then_zuruf() -> bool:
    """Fix pass 2, A: the round says "Ja" to level 2's document question
    (page 1), and the same block's Zuruf reaches the agent thread only
    afterwards -- no second page, no second fill order."""
    e = _wiki_env("sitzung", "Kosten der Obstwiese")
    e.hear(dokument_kandidat=0.9)
    if e.runner.policy.ask is None:
        return False
    e.script = {CONFIRM_ID: {"noul": 0.9}}
    e.hear(zuruf_robo=0.8)
    if len(e.docs()) != 1 or not e.events("ask_confirmed"):
        return False
    seite = e.docs()[0]
    return (e.runner.page_order("Robo, leg dafuer eine Seite an") is None
            and _one_page(e) and e.writer.topic_page() == seite
            and len(e.events("policy_action", kind="document")) == 1)


def rule_page_zuruf_then_confirm() -> bool:
    """Fix pass 2, A, the other order: the decider has already taken the
    ask out (a "Ja" in flight) when the Zuruf creates the page; the
    confirmation then finds the block's page and creates nothing."""
    e = _wiki_env("sitzung", "Kosten der Obstwiese")
    e.hear(dokument_kandidat=0.9)
    ask = e.runner.policy.ask
    if ask is None:
        return False
    e.runner.policy.ask = None               # pol.answer() took it
    if e.runner.page_order("Robo, leg dafuer eine Seite an") is None:
        return False
    e.runner._settle_ask("confirmed", ask)   # noqa: SLF001 -- the race
    q = e.state.question
    return (_one_page(e) and q is not None and q.state == "resolved"
            and len(e.events("policy_action", kind="document")) == 1)


def rule_supersede_under_lock() -> bool:
    """Fix pass 2, B: the Zuruf's check-and-swap of level 2's ask runs
    under the runner's lock -- while the decider holds it (in pol.answer
    / _refresh), the agent thread waits; the ask is settled exactly once."""
    import threading
    e = _wiki_env("sitzung", "Kosten der Obstwiese")
    e.hear(dokument_kandidat=0.9)
    ask = e.runner.policy.ask
    if ask is None:
        return False
    e.runner._lock.acquire()                 # noqa: SLF001 -- the decider
    t = threading.Thread(target=e.runner.page_order,
                         args=("Robo, leg dafuer eine Seite an",))
    t.start()
    t.join(0.3)
    unberuehrt = e.runner.policy.ask is ask and not e.docs()
    e.runner._lock.release()                 # noqa: SLF001
    t.join(5)
    return (unberuehrt and not t.is_alive() and e.runner.policy.ask is None
            and len(e.events("ask_discarded", reason="superseded")) == 1
            and len(e.docs()) == 1)


def rule_pool_ask_access_locked() -> bool:
    """Fix pass 2, B: every read-modify-write of `policy.ask` in the
    runner sits under `self._lock` (pol.answer, _refresh, the Zuruf's
    supersede, close) -- pinned on the source, a race is not testable."""
    import inspect
    import blocks.decisions_tasks_questions.policy_runner as prm
    zeilen = inspect.getsource(prm.PolicyRunner).splitlines()

    def tiefe(z: str) -> int:
        return len(z) - len(z.lstrip())

    def unter_lock(i: int) -> bool:
        """Walk the enclosing blocks up to the `def`."""
        grenze = tiefe(zeilen[i])
        for z in reversed(zeilen[:i]):
            if not z.strip() or z.strip().startswith("#"):
                continue
            if tiefe(z) < grenze:
                if z.strip().startswith("with self._lock"):
                    return True
                if z.strip().startswith("def "):
                    return False
                grenze = tiefe(z)
        return False

    swaps = [i for i, z in enumerate(zeilen)
             if "self.policy.ask = " in z or "self.policy.ask, None" in z
             or "pol.answer(self.policy" in z]
    return len(swaps) >= 5 and all(unter_lock(i) for i in swaps)


# --------------------------------------- MM-L: the pending label's WHAT
def _record_labels(e: Env) -> list:
    """Every (kind, label) the runner announces, in order -- a question's
    pending is settled at once, so the state alone would not show it."""
    seen: list = []
    announce = e.writer.announce

    def spy(kind, label):
        seen.append((kind, label))
        return announce(kind, label)
    e.writer.announce = spy
    return seen


def _label_is(seen: list, kind: str, want: str, block: str) -> bool:
    """Exactly one `kind` pending, labelled `want`: a prefix plus at most
    three content words, never the whole block."""
    labels = [lab for k, lab in seen if k == kind]
    if labels != [want]:
        return False
    prefix, _, words = want.partition(" ")
    return (prefix.endswith(":") and 1 <= len(words.split()) <= 3
            and want != block and block not in want)


def rule_label_entry() -> bool:
    e = Env()
    seen = _record_labels(e)
    block = "Der Gartenverein braucht neue Giesskannen fuer das Sommerfest"
    e.hear(block=block, aufgabe=0.8)
    return _label_is(seen, "entry", "Aufgabe: Gartenverein braucht neue",
                     block)


def rule_label_decision() -> bool:
    e = Env(mode="sitzung")
    seen = _record_labels(e)
    block = "Der Kuchenbasar findet am Samstag im Gemeindesaal statt"
    e.hear(block=block, beschluss=0.8)
    return _label_is(seen, "decision", "Beschluss: Kuchenbasar findet Samstag",
                     block)


def rule_label_research() -> bool:
    e = Env(mode="arbeit")
    seen = _record_labels(e)
    block = "Wie viele Beete hat die Obstwiese am Stadtrand eigentlich"
    e.hear(block=block, recherche_kandidat=0.85)
    return _label_is(seen, "research", "Recherche: viele Beete Obstwiese",
                     block)


def rule_label_topic() -> bool:
    e = Env()
    seen = _record_labels(e)
    block = "Gießplan fuer die Hochbeete im Sommer und wer den Schluessel hat"
    e.hear(block=block, neues_thema=0.95)
    return _label_is(seen, "topic", "Thema: Gießplan fuer Hochbeete", block)


def rule_label_document() -> bool:
    """The page's title (the protocol's topic block) wins over the block."""
    e = _wiki_env("arbeit", "Kosten der Obstwiese")
    seen = _record_labels(e)
    block = "Dazu brauchen wir eine eigene Seite mit allen Rechnungen"
    e.hear(block=block, dokument_kandidat=0.85)
    return _label_is(seen, "document", "Dokument: Kosten Obstwiese", block)


def rule_label_question() -> bool:
    """The ask path: our own question, reduced, the `?` kept."""
    e = Env(mode="sitzung")
    seen = _record_labels(e)
    block = "Fuer den Basar koennten wir eine eigene Liste fuehren"
    e.hear(block=block, dokument_kandidat=0.9)
    return (e.state.question is not None
            and _label_is(seen, "question",
                          "Rückfrage: Eigenes Dokument dazu", block))


def rule_label_wake() -> bool:
    """The call-out level 2 recognises without the wake reason: `main`,
    the wake word itself is not content."""
    e = Env()
    seen = _record_labels(e)
    block = "Robo zeig uns die Liste der Beetpaten fuer den Sommer"
    e.hear(block=block, reason="pause", zuruf_robo=0.95)
    return _label_is(seen, "main", "Zuruf: zeig Liste Beetpaten", block)


def rule_label_canary() -> bool:
    """The marker, first content word of the block, reaches the pending
    label in buehne.json -- and nowhere in the event trace."""
    e = Env()
    mark = "Zwiebelkuchenkanarienvogel"
    e.hear(block=f"{mark} fuer den Kuchenbasar am Samstag besorgen",
           aufgabe=0.8)
    stage = json.loads((e.run_dir / "buehne.json").read_text("utf-8"))
    labels = [p.get("label", "") for p in stage.get("pending", [])]
    trace = e.trace_path.read_text("utf-8")
    return (labels == [f"Aufgabe: {mark} fuer Kuchenbasar"]
            and bool(e.events("pending_start", kind="entry"))
            and mark not in trace)


RULES = [
    ("1 autonomous tag entry: pending, order, marker, arrived",
     rule_autonomous_entry),
    ("2 end_turn settles leftovers unanswered / failed",
     rule_end_turn_settles),
    ("3 topic braked while fresh, ordered once free", rule_topic_braked),
    ("4 ask -> confirm: document with bestätigt marker", rule_ask_confirm),
    ("5 ask -> no answer in 2 blocks: discarded", rule_ask_no_answer),
    ("6 ask -> rejected: discarded", rule_ask_rejected),
    ("7 one ask at a time", rule_one_ask),
    ("8 write outside is refused into a question", rule_write_outside_asks),
    ("9 wake without wake word; not twice with it", rule_wake),
    ("10 mode counter-check after 300 s, then never asked", rule_mode_check),
    ("11 decide_fn raising: no exception, ask stays open",
     rule_decide_error),
    ("12 canary reaches the prompt note only", rule_canary),
    ("13 I1 no re-ask in the settling block, cooldown for the signal",
     rule_no_reask),
    ("14 I1 a different signal asks after the settling block",
     rule_other_signal_asks),
    ("15 I2 confirmed order anchored to the asking block",
     rule_confirm_anchor),
    ("16 I3 agent question between check and apply: nothing parked",
     rule_atomic_ask),
    ("17 M-a stage question resolved even when settling raises",
     rule_settle_resolves_on_error),
    ("18 M-f resolution texts Ja / verworfen", rule_resolution_texts),
    ("19 M-b taken document name moves on to the next n",
     rule_doc_name_taken),
    ("20 M-f autonomous document: *(autonom)* skeleton + order",
     rule_autonomous_document),
    ("21 M-c confirmed topic block passes the brake", rule_confirmed_topic_braked),
    ("22 fix1 close(): no action any more, leftovers settled",
     rule_close_stops),
    ("23 fix1 close() during an action in flight: pending settled",
     rule_close_in_flight),
    ("24 fix3 order ids are honoured only in the turn they were sent",
     rule_turn_scoped_ids),
    ("25 minor3 wake order arrives with an answered call-out",
     rule_wake_answered),
    ("26 minor5 v1 fallback (writer.protocol None): no action",
     rule_v1_fallback_stops),
    ("27 final2 a yes cools the signal down too", rule_yes_cooldown),
    ("28 final2 stage closed the ask: settled block + cooldown",
     rule_stage_closed_cooldown),
    ("29 final1 open_ask_id names level 2's own open question",
     rule_open_ask_id),
    ("30 final3 close() resolves the open ask verworfen",
     rule_close_clears_ask),
    ("31 final3 close() leaves a question it no longer owns alone",
     rule_close_leaves_foreign_question),
    ("32 MM-G page in entwuerfe/, links, consumer backlink, fill order",
     rule_page_backlink),
    ("33 MM-G a page named like a project note moves on to -2",
     rule_page_name_of_a_note),
    ("34 MM-G Zuruf page: every mode, bestätigt, same path; closed: none",
     rule_page_order),
    ("35 MM-G page not creatable: refused trace, no pending, no order",
     rule_page_failure_costs_page_only),
    ("36 MM-G Zuruf closes level 2's document ask (cooldown, no 2nd page)",
     rule_page_order_supersedes_ask),
    ("37 MM-G page title = the protocol's current topic block",
     rule_page_title_from_protocol),
    ("38 MM-G confirm, then Zuruf: one page, one backlink, page_exists",
     rule_page_confirm_then_zuruf),
    ("39 MM-G Zuruf, then confirm: one page, one backlink, page_exists",
     rule_page_zuruf_then_confirm),
    ("40 MM-G Zuruf's supersede waits for the runner lock, settles once",
     rule_supersede_under_lock),
    ("41 MM-G every policy.ask swap sits under the runner lock",
     rule_pool_ask_access_locked),
    ("42 MM-L entry label: tag prefix + <= 3 content words",
     rule_label_entry),
    ("43 MM-L decision label", rule_label_decision),
    ("44 MM-L research label", rule_label_research),
    ("45 MM-L topic label from the block", rule_label_topic),
    ("46 MM-L document label: the page title wins over the block",
     rule_label_document),
    ("47 MM-L question label from the ask text, ? kept",
     rule_label_question),
    ("48 MM-L wake label: main, wake word dropped", rule_label_wake),
    ("49 MM-L canary: marker in buehne.json label, not in the trace",
     rule_label_canary),
]

print("level-2 policy runner")
for name, rule in RULES:
    try:
        ok = rule()
    except Exception as ex:                  # noqa: BLE001
        ok = False
        name += f"  ({type(ex).__name__}: {ex})"
    check(name, ok)

print("\nother")
e = Env()
check("marker_for: unknown / non-token id is empty",
      e.runner.marker_for("A99") == "" and e.runner.marker_for("A 1") == ""
      and e.runner.marker_for(None) == "")
e.runner.delivered("A99")
e.runner.delivered(["A1"])
e.runner.end_turn(True)
check("delivered/end_turn with nothing queued do not raise", True)
check("take_turn with nothing queued is empty",
      e.runner.take_turn() == pr.TurnOrders())
e.hear(offene_frage=0.85)
check("offene_frage -> tag frage order",
      "Tag `frage`" in e.runner.take_turn().note)
e2 = Env(mode="kaputt")
check("unknown mode falls back to the default", e2.runner.mode == pol.DEFAULT_MODE)


# ------------------------------------------------------------ mutants
print("\nmutants: each broken runner must turn its named check red")


def _run_quiet(rule) -> bool:
    try:
        return bool(rule())
    except Exception:                        # noqa: BLE001
        return False


def _mutant(name, owner, attr, broken, rule):
    saved = getattr(owner, attr)
    setattr(owner, attr, broken)
    try:
        red = not _run_quiet(rule)
    finally:
        setattr(owner, attr, saved)
    check(f"mutant {name} is caught by {rule.__name__}", red)


_on_result = pr.PolicyRunner.on_result
_mutant("M1 delivered does nothing", pr.PolicyRunner, "delivered",
        lambda self, oid: None, rule_autonomous_entry)
_mutant("M2 end_turn settles nothing", pr.PolicyRunner, "end_turn",
        lambda self, ok: None, rule_end_turn_settles)
_mutant("M3 runner skips brake_check", ss.StageState, "brake_check",
        lambda self: "", rule_topic_braked)
_mutant("M4 runner ignores reason == wake", pr.PolicyRunner, "on_result",
        lambda self, r, s, reason="": _on_result(self, r, s, ""), rule_wake)


def _settle_no_finally(self, outcome, ask):
    if outcome in ("none", "waiting") or ask is None:
        return
    self._trace("ask_discarded", id=ask.stage_id, reason="rejected")
    self._resolve(ask.stage_id, "Ja" if outcome == "confirmed"
                  else "verworfen")


_mutant("M5 settle without finally", pr.PolicyRunner, "_settle_ask",
        _settle_no_finally, rule_settle_resolves_on_error)
_mutant("M6 confirm skips the brake", pr.PolicyRunner, "_confirmed",
        lambda self, ask: self._execute(ask.action, "*(bestätigt 14:07)*",
                                        ask.block),
        rule_confirmed_topic_braked)

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
