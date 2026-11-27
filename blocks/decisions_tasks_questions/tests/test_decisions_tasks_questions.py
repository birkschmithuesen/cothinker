# CoThinker — tests for blocks/decisions_tasks_questions (issue #40, Wave B.3)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `decisions-tasks-questions` block, driven through the session's real
block host with invented transcript lines.

    python3 -m pytest -q blocks/decisions_tasks_questions/tests/test_decisions_tasks_questions.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

import pytest  # noqa: E402

import consumer  # noqa: E402
import stage_state as ss  # noqa: E402
import blocks.decisions_tasks_questions.decide as d  # noqa: E402
import blocks.decisions_tasks_questions.policy_runner as pr  # noqa: E402
from blocks.decisions_tasks_questions import (BLOCK_ID, DecisionsBlock,  # noqa: E402
                                              IntentWriter)
from gateway import block_host_wiring as bhw  # noqa: E402
from shared.block_registry import load_block, load_from  # noqa: E402
from gateway.stage_surface import StageSurface  # noqa: E402
from shared.events import EventLog  # noqa: E402

WALL = datetime(2026, 9, 26, 14, 7, 0)
SATZ = "Wir sollten die Tabelle mit den Raumkosten bis Freitag fertig haben"
NOTE = "AUFTRAG A1: Trage eine Aufgabe ein."
FRUEHER = "Robo zeig uns die Liste der offenen Punkte"


def ans(**ps):
    """Level-1 answer entries in decide.py's shape."""
    return [{"question_id": k, "p": v, "confidence": 0.9, "ms": 1.0}
            for k, v in ps.items()]


def scripted(**ps):
    def decide_fn(snap, questions=None, *, backend=None):
        return d.Result(answers=ans(**ps), backend="fake")
    return decide_fn


def segment(text=SATZ, *, wake_word=False, reason="pause"):
    """A block as the segmenter delivers it to the turn handles."""
    return SimpleNamespace(text=text, wake_word=wake_word, reason=reason,
                           chars=len(text))


class Rig:
    """The real block host with the block in it, over a real stage."""

    def __init__(self, tmp_path, *, policy=False, v2=True, **decider_kw):
        self.tmp = Path(tmp_path)
        self.logs = []
        self.intents = []          # every intent the block emitted, in order
        self.outputs = []          # every block_output payload
        self.state = ss.StageState(now=time.monotonic, clock=lambda: WALL)
        self.writer = consumer.StageWriter(
            self.tmp, self.state, log=lambda *a: None,
            proposals=self.tmp / "vorschlag.jsonl",
            events=EventLog(self.tmp / "stage-events.jsonl"))
        # Level 2 acts only while the writer files into a v2 protocol; a
        # stand-in does where the page's backlink is not what is checked.
        self.writer.protocol = object() if v2 else None
        surface = StageSurface(self.writer, self.state, self.logs.append)

        def tee(intent):
            self.intents.append(intent)
            surface.render([intent])

        self.blocks = bhw.build_session_blocks(
            session_dir=self.tmp, sink=tee, log=self.logs.append,
            load=load_from({BLOCK_ID: DecisionsBlock}), clock=time.time)
        self.blocks.on_output(self.outputs.append)
        self.block = self.blocks.get(BLOCK_ID)
        self.block.attach(
            self.writer, decisions_path=self.tmp / "decisions.jsonl",
            session_dir=self.tmp, project_dir=self.tmp, log=self.logs.append,
            policy=policy, mode="sitzung",
            wants_page=lambda text: False,
            order_note_for=consumer.order_note_for,
            env={"COTHINKER_DECIDER": "fake"}, **decider_kw)

    def hear(self, text=SATZ, *, wake_word=False, reason="pause"):
        self.blocks.dispatch("segment", {
            "id": 1, "text": text, "speakers": [], "wake_word": wake_word,
            "reason": reason, "chars": len(text),
            "at": datetime.now().astimezone().timestamp()})

    def settle_decider(self):
        """Waits for the decider's backlog, then lets the block speak."""
        assert self.block.wait_for_results(1), "the decider never answered"
        self.blocks.dispatch("tick", {"at": time.time(), "components": {}})

    last_open_ask_id = ""

    def turn_orders(self, seg):
        """Opens a turn through the event pair, as the host does: returns
        (order note, spoken). No answer (no block) -> the plain text."""
        n = len(self.outputs)
        self.blocks.dispatch("block_output", {"turn_open": {
            "turn": 1, "text": seg.text, "wake_word": seg.wake_word}})
        got = [o for o in self.outputs[n:] if o.get("kind") == "turn_orders"]
        if not got:
            return "", seg.text
        self.last_open_ask_id = got[-1]["open_ask_id"]
        return got[-1]["note"], got[-1]["spoken"]

    def end_turn(self, ok, answered=False):
        self.blocks.dispatch("block_output", {"turn_closed": {
            "turn": 1, "ok": ok, "answered": answered}})

    def of_kind(self, kind):
        return [o for o in self.outputs if o.get("kind") == kind]


def decisions_lines(rig):
    path = rig.tmp / "decisions.jsonl"
    if not path.exists():
        return []
    return [z for z in path.read_text(encoding="utf-8").splitlines() if z.strip()]


# ------------------------------------------------------------ the contract
def test_block_implements_the_contract():
    block = DecisionsBlock()
    assert block.id == "decisions-tasks-questions"
    assert block.api_version == 1
    assert type(load_block("decisions-tasks-questions")) is DecisionsBlock


def test_consumer_constructs_none_of_the_decider_or_policy_objects():
    source = Path(consumer.__file__).read_text(encoding="utf-8")
    assert "PolicyRunner" not in source
    assert "DeciderWorker" not in source
    assert "decider.from_env" not in source
    assert "PolicyRunner" not in vars(consumer)


# ------------------------------------------------------------ the four new
def test_segment_reaches_decider(tmp_path):
    rig = Rig(tmp_path)
    rig.hear(SATZ, reason="pause")
    rig.settle_decider()
    assert len(decisions_lines(rig)) == 1
    assert '"reason": "pause"' in decisions_lines(rig)[0] \
        or '"reason":"pause"' in decisions_lines(rig)[0]


def test_decider_result_is_a_block_output(tmp_path):
    rig = Rig(tmp_path)
    rig.hear(SATZ)
    rig.settle_decider()
    results = rig.of_kind("decider_result")
    assert len(results) == 1
    out = results[0]
    assert out["from"] == BLOCK_ID
    assert out["block"] == 1 and out["reason"] == "pause"
    assert out["answers"], "the level-1 answers ride along"
    assert not rig.of_kind("entry")


def test_announce_becomes_pending_intent(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    announced = [i for i in rig.intents if i.payload["op"] == "announce"]
    assert len(announced) == 1
    intent = announced[0]
    assert intent.id.startswith("pending:") and intent.block == BLOCK_ID
    assert intent.payload["kind"] == "entry"
    # The legacy stage sink applied it: today's stage output is unchanged.
    assert [p.kind for p in rig.state.pending] == ["entry"]
    assert intent in rig.blocks.intents.current()


def test_settle_updates_same_intent_id(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    extra, _ = rig.turn_orders(segment("Nur das."))
    assert "A1" in extra
    rig.end_turn(True)
    ops = [(i.id, i.payload["op"]) for i in rig.intents]
    assert [op for _, op in ops] == ["announce", "settle"]
    assert ops[0][0] == ops[1][0], "a settle updates the SAME intent"
    assert rig.intents[1].payload["outcome"] == "unanswered"
    assert not rig.state.pending
    # A settled pending leaves the board.
    assert rig.intents[1] not in rig.blocks.intents.current()


# ------------------------------------------------------------ intent writer
def test_intent_writer_warn_is_an_intent_the_stage_applies(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted())
    iw = IntentWriter(rig.writer, emit=rig.block._ctx.emit, block_id=BLOCK_ID,
                      clock=time.time)
    iw.warn("etwas stimmt nicht")
    assert rig.intents[-1].payload == {"op": "warn", "text": "etwas stimmt nicht"}
    assert rig.writer.warning() == "etwas stimmt nicht"


def test_intent_writer_settle_of_an_unknown_id_is_false(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted())
    iw = IntentWriter(rig.writer, emit=rig.block._ctx.emit, block_id=BLOCK_ID,
                      clock=time.time)
    assert iw.settle("pending:99", "arrived") is False
    pid = iw.announce("entry", "Eintrag wird notiert")
    assert iw.settle(pid, "arrived") is True
    assert iw.settle(pid, "arrived") is False


def test_a_refused_announce_leaves_no_stage_pending(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted())
    iw = IntentWriter(rig.writer, emit=rig.block._ctx.emit, block_id=BLOCK_ID,
                      clock=time.time)
    pid = iw.announce("kein-gueltiger-art", "x")
    assert pid.startswith("pending:")
    assert not rig.state.pending
    assert iw.settle(pid, "arrived") is True      # the block's own books


# ------------------------------------------- the entry output of a document
def test_a_filed_entry_is_a_block_output(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted())
    # The runner reports the entries it files through the block's hook.
    rig.block._runner.on_entry("notiz", "Themenseite [[x]]", "T1")
    rig.blocks.dispatch("tick", {"at": 0.0, "components": {}})
    [out] = rig.of_kind("entry")
    assert out["from"] == BLOCK_ID
    assert (out["tag"], out["text"], out["topic_id"]) == (
        "notiz", "Themenseite [[x]]", "T1")


# ------------------------------------------- host handles (was test_consumer)
class TurnStub:
    """Stands in for PolicyRunner.take_turn(): hands out one TurnOrders."""

    def __init__(self, orders):
        self.orders = orders
        self.taken = 0

    def take_turn(self):
        self.taken += 1
        return self.orders


def rig_with_orders(tmp_path, orders, *, fmt_v2=True):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted())
    stub = TurnStub(orders)
    rig.block._runner.take_turn = stub.take_turn
    return rig, stub


def test_the_orders_note_becomes_the_turns_extra_order_note(tmp_path):
    rig, stub = rig_with_orders(tmp_path, pr.TurnOrders(note=NOTE))
    extra, gesagt = rig.turn_orders(segment("Wir reden über den Termin."))
    assert stub.taken == 1 and extra == NOTE
    assert (consumer.order_note_for(False) + extra).endswith(NOTE)
    assert NOTE in consumer.order_note_for(True) + extra
    assert gesagt == "Wir reden über den Termin."


def test_a_wake_order_on_a_block_without_the_wake_word(tmp_path):
    rig, _ = rig_with_orders(
        tmp_path, pr.TurnOrders(note=NOTE, spoken=FRUEHER, wake=True))
    extra, gesagt = rig.turn_orders(segment("Ja genau so."))
    assert extra == NOTE + consumer.ORDER_NOTE
    assert gesagt == "Ja genau so. " + FRUEHER


def test_a_block_with_the_wake_word_carries_its_note_once(tmp_path):
    rig, _ = rig_with_orders(
        tmp_path, pr.TurnOrders(note=NOTE, spoken=FRUEHER, wake=True))
    extra, _ = rig.turn_orders(segment("Robo, mach das.", wake_word=True))
    assert extra == NOTE


def test_without_a_runner_no_note_and_spoken_is_the_block_text(tmp_path):
    rig = Rig(tmp_path, policy=False)
    assert rig.turn_orders(segment("Nur das.")) == ("", "Nur das.")
    assert rig.last_open_ask_id == ""
    rig.end_turn(True)          # a no-op, never raises
    assert rig.block.page_order("x") is None


def test_policy_off_logs_why_and_leaves_the_stage_alone(tmp_path):
    rig = Rig(tmp_path, policy=True, v2=False)
    assert any("policy off: needs a v2 protocol" in z for z in rig.logs)
    assert rig.writer.policy is None


def test_the_stage_hooks_reach_the_runner(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    assert rig.writer.policy is rig.block
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    assert rig.block.marker_for("A1") == "*(autonom)*"
    rig.block.delivered("A1")
    assert not rig.state.pending


def test_stage_context_reads_the_stage(tmp_path):
    rig = Rig(tmp_path)
    ctx = rig.block.stage_context(rig.state)
    assert isinstance(ctx, dict)
    assert rig.block.stage_context(object()) == {}


def test_without_the_env_var_there_is_no_decider(tmp_path):
    rig = Rig(tmp_path)
    rig.block.attach(
        rig.writer, decisions_path=rig.tmp / "none.jsonl", session_dir=rig.tmp,
        project_dir=rig.tmp, log=rig.logs.append, policy=True, mode="sitzung",
        env={})
    assert not rig.block.decider_on
    assert any("policy off: needs COTHINKER_DECIDER" in z for z in rig.logs)
    rig.hear()
    assert not (rig.tmp / "none.jsonl").exists()


def test_a_result_hook_runs_after_the_result_is_written(tmp_path):
    rig = Rig(tmp_path)
    seen = []
    rig.block.add_result_hook(lambda result, snap, reason: seen.append(reason))
    rig.hear(reason="batch")
    rig.settle_decider()
    assert seen == ["batch"]


def test_a_broken_block_never_stops_the_session(tmp_path):
    rig = Rig(tmp_path)
    rig.block.finish()
    rig.hear()            # the decider is closed: dropped, never raised
    assert rig.blocks.dispatch("tick", {"at": 0.0, "components": {}}) >= 1


# ------------------------------------- orders and tasks (issues #178, #129)
TASK = {"action": "tag_entry", "tag": "aufgabe",
        "text": "Sam bestellt bis Donnerstag Material.",
        "person": "Sam", "due": "Donnerstag"}
SAMS_TASK = {"text": "Sam bestellt bis Donnerstag Material.",
             "person": "Sam", "due": "Donnerstag"}


def tasks_intents(rig):
    return [i for i in rig.intents if i.kind == "tasks"]


def test_orders_wait_until_a_turn_takes_them(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    assert rig.block.orders_waiting() is False
    rig.hear()
    rig.settle_decider()
    assert rig.block.orders_waiting() is True
    rig.turn_orders(segment("Nur das."))
    assert rig.block.orders_waiting() is False


def test_no_orders_wait_without_level_2(tmp_path):
    assert Rig(tmp_path, policy=False).block.orders_waiting() is False


def test_a_filed_aufgabe_answer_becomes_the_tasks_intent(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A1", {**TASK, "order": "A1"})
    [got] = tasks_intents(rig)
    assert (got.id, got.block, got.audience) == ("tasks:room", BLOCK_ID, "room")
    assert got.payload == {"tasks": [SAMS_TASK]}
    assert rig.state.content()["tasks"] == [SAMS_TASK]


def test_a_task_the_meeting_gave_no_person_or_due_keeps_them_empty(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A1", {"action": "tag_entry", "tag": "aufgabe",
                                "text": "Material bestellen."})
    assert rig.state.tasks == [{"text": "Material bestellen.",
                                "person": "", "due": ""}]


def test_the_list_grows_and_a_repeat_is_not_added_twice(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A1", TASK)
    rig.block.task_filed("A1", {**TASK, "text": "Raum buchen.", "person": "Alex"})
    rig.block.task_filed("A1", TASK)
    assert [t["text"] for t in rig.state.tasks] == [
        "Sam bestellt bis Donnerstag Material.", "Raum buchen."]
    assert len(tasks_intents(rig)) == 2


def test_only_the_running_turns_aufgabe_orders_make_tasks(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(aufgabe=0.8))
    rig.hear()
    rig.settle_decider()
    rig.block.task_filed("A1", TASK)               # no turn took A1 yet
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A9", TASK)               # not an order of this turn
    rig.block.task_filed("A1", {**TASK, "tag": "notiz"})
    rig.block.task_filed("A1", {**TASK, "text": "   "})
    assert not tasks_intents(rig) and "tasks" not in rig.state.content()


def test_an_aufgabe_answer_to_a_frage_order_makes_no_task(tmp_path):
    """The ORDER decides what was asked for: an `aufgabe`-tagged answer to
    an `offene_frage` order (tag `frage`) is no task."""
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(offene_frage=0.9))
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A1", {**TASK, "order": "A1"})
    assert not tasks_intents(rig) and "tasks" not in rig.state.content()


def test_a_decision_order_makes_no_task(tmp_path):
    rig = Rig(tmp_path, policy=True, decide_fn=scripted(beschluss=0.8))
    rig.hear()
    rig.settle_decider()
    rig.turn_orders(segment("Nur das."))
    rig.block.task_filed("A1", TASK)
    assert not tasks_intents(rig)


def test_without_level_2_a_filed_task_is_a_no_op(tmp_path):
    rig = Rig(tmp_path, policy=False)
    rig.block.task_filed("A1", TASK)
    assert not tasks_intents(rig)


def test_an_aufgabe_order_asks_for_person_and_due():
    import blocks.decisions_tasks_questions.policy as pol
    aufgabe = pol.Action("tag_entry", "aufgabe", 0.9, True, tag="aufgabe")
    text = pol.order_text(aufgabe, "A1", "Sam bis Donnerstag")
    assert text.endswith(pol.TASK_FIELDS_NOTE) and text.isascii()
    assert '"person"' in text and '"due"' in text
    frage = pol.Action("tag_entry", "offene_frage", 0.9, True, tag="frage")
    assert pol.TASK_FIELDS_NOTE not in pol.order_text(frage, "A2", "x")
