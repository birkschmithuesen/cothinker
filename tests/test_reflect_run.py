# CoThinker — level-3 template run test: Reflect through its one phase
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""ADR 0022 level 3 for the Reflect template: a recorded transcript runs
through the real block host, covering the template's one phase end to end.

Placed under `tests/`, not `templates/reflect/tests/` (ADR 0022's own
example path): issue #155 (the component-tests collection and allowlist
ADR 0022 level 1 asks for) has not landed yet, and `python3 -m pytest -q
tests` -- the exact command this card and CI both run -- only collects
files under `tests/`. A test at the ADR's literal path would never run.
Move it once #155 lands.

The fixture (`tests/fixtures/reflect_meeting.jsonl`) is the fake agent:
each line is one recorded utterance, and the few lines that close an
agent turn carry a pre-written `agent_reply` (the capture-bullets JSON
shape, see `tests/fake_worker.py`) instead of a live model call.

`build_session_blocks`' default `load` (`shared.block_registry.load_block`)
finds all fifteen of the Reflect template's blocks by package name (issues
#178, #181, #28); this test still keeps its own map (`FULL_REGISTRY`) of
each package's `make_block` rather than relying on that default -- ADR 0022
level 3 is about the TEMPLATE, not about which blocks `gateway/consumer.py`
happens to be wired to, so this test must not go silently stale the next
time a block is temporarily missing from the host.

python3 -m pytest -q tests/test_reflect_run.py
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from gateway import block_host_wiring as bhw
from gateway import consumer
from gateway.raw_transcript import MinutesFile
from gateway.stage_state import StageState
from gateway.stage_surface import StageSurface
import blocks.call_window, blocks.capture_bullets, blocks.condense  # noqa: E401
import blocks.decisions_tasks_questions, blocks.graph, blocks.knowledge_link  # noqa: E401
import blocks.live_transcript, blocks.minutes_document, blocks.mirroring_coach  # noqa: E401
import blocks.speaker_queue, blocks.speaking_share, blocks.status_line  # noqa: E401
import blocks.timer, blocks.topics, blocks.voice_attribution  # noqa: E401
from shared.block_registry import load_from
from shared.templates import load_template
from surfaces.contract.models import StageStateSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "reflect" / "template.toml"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "reflect_meeting.jsonl"

# All fifteen Reflect blocks, pinned to each package's own make_block()
# rather than to the host's default `load` (see the module docstring above).
FULL_REGISTRY = {
    "capture-bullets": blocks.capture_bullets.make_block,
    "topics": blocks.topics.make_block,
    "decisions-tasks-questions": blocks.decisions_tasks_questions.make_block,
    "condense": blocks.condense.make_block,
    "minutes-document": blocks.minutes_document.make_block,
    "mirroring-coach": blocks.mirroring_coach.make_block,
    "knowledge-link": blocks.knowledge_link.make_block,
    "timer": blocks.timer.make_block,
    "speaker-queue": blocks.speaker_queue.make_block,
    "speaking-share": blocks.speaking_share.make_block,
    "status-line": blocks.status_line.make_block,
    "graph": blocks.graph.make_block,
    "voice-attribution": blocks.voice_attribution.make_block,
    "call-window": blocks.call_window.make_block,
    "live-transcript": blocks.live_transcript.make_block,
}

# Invented meeting, invented association (AGENTS.md rule 7: only example
# data under invented names, never a real deployment's).
HEAD = dict(title="Werkstatt-Planung", entity="beispielverein",
           day="2026-11-24", pslug="werkstatt",
           participants="Mira, Jonas, Alina", start="19:00")

# The blocks this fixture drives to at least one output, and the blocks
# that stay silent on purpose (each for a reason that already holds in a
# real session, not a shortcut of this test):
#   timer                     -- no `items` configured in template.toml
#   knowledge-link            -- its `on_event` only reacts to a
#                                `{"output": ...}` shape nothing in this
#                                repository emits yet (interim: the host
#                                still calls it directly, see its own
#                                docstring)
#   mirroring-coach           -- no agent lane configured for this fixture
#   decisions-tasks-questions -- its decider is opt-in (COTHINKER_DECIDER)
#                                and off here, exactly like a default session
#   condense                  -- never sees a second topic, so it never
#                                closes the first (no LLM call needed either)
EXPECTED_ACTIVE = {"capture-bullets", "minutes-document", "topics",
                  "speaker-queue", "speaking-share", "status-line", "graph",
                  "voice-attribution", "live-transcript"}
EXPECTED_SILENT = {"timer", "knowledge-link", "mirroring-coach",
                  "decisions-tasks-questions", "condense"}


def _load_fixture() -> list[dict]:
    lines = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class FakeClock:
    """The block host's `clock`: a float the test moves by hand."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = t


class _FakeWriter:
    """What `StageSurface` needs of `gateway.consumer.StageWriter`, without
    the stage's legacy file-writing machinery this test does not exercise."""

    def write(self) -> None:
        pass

    def announce(self, kind: str, label: str) -> str:
        return "p1"

    def settle(self, pending_id: str, outcome: str) -> bool:
        return True

    def warn(self, text: str) -> None:
        pass

    def clear_warning(self, only: str | None = None) -> None:
        pass

    def warning(self) -> str:
        return ""

    def set_status(self, state: str, *, at: str, count: int, failed) -> None:
        pass


def run_session(session_dir: Path, template_path: Path, registry: dict,
                fixture_lines: list[dict]) -> SimpleNamespace:
    """Feeds `fixture_lines` through the real block host built from
    `template_path`, using `gateway.consumer`'s own minutes wiring
    (`MinutesPort`, `ReplyIntake`, `BULLET_TAGS`) and `gateway.stage_surface
    .StageSurface` over a real `gateway.stage_state.StageState`, exactly the
    pieces `gateway/consumer.py::main()` wires together -- only the STT
    reader, the worker subprocess and the stage's own file writer are
    replaced, by the fixture and by `_FakeWriter`.
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    protocol_path = session_dir / "protokoll.md"
    protocol_path.write_text(consumer.protocol_header(**HEAD, mode="sitzung"),
                             encoding="utf-8")

    clock = FakeClock(0.0)
    state = StageState(now=clock, clock=lambda: datetime.fromtimestamp(clock()))
    stage_surface = StageSurface(_FakeWriter(), state, lambda *a: None)
    blocks_cell: list = []
    produced: set[str] = set()

    def sink(intent) -> None:
        produced.add(intent.block)
        current = {i.id: i for i in blocks_cell[0].intents.current()}
        current.setdefault(intent.id, intent)
        stage_surface.render(list(current.values()))

    logs: list[str] = []
    blocks = bhw.build_session_blocks(
        session_dir=session_dir, sink=sink, log=logs.append, clock=clock,
        template_path=template_path, load=load_from(registry))
    blocks_cell.append(blocks)

    def track_from(payload) -> None:
        frm = payload.get("from") if hasattr(payload, "get") else None
        if frm:
            produced.add(frm)
    blocks.on_output(track_from)

    minutes_file = MinutesFile(protocol_path, threading.RLock())
    minutes_port = consumer.MinutesPort(minutes_file, blocks,
                                        session_dir=session_dir, mode="sitzung")
    stage_surface.render(blocks.intents.current())

    reply_intake = consumer.ReplyIntake(blocks)
    turn = 0
    for i, line in enumerate(fixture_lines, start=1):
        clock.set(line["t_start"])
        for action in line.get("actions", ()):
            blocks.dispatch("action", {**action, "at": line["t_start"]})
        bhw.feed_utterance(minutes_file, blocks, line["text"], line["speaker"],
                           {"turn_id": i, "t_start": line["t_start"],
                            "t_end": line["t_end"], "at": line["t_start"]})
        clock.set(line["t_end"])
        blocks.dispatch("tick", {"at": line["t_end"], "now": line["t_end"],
                                 "components": {"queue_backlog": 0}})
        if "agenda_change" in line:
            blocks.dispatch("block_output", {"decider_result": line["agenda_change"]})
        if "agent_reply" in line:
            turn += 1
            reply = reply_intake.read(
                json.dumps(line["agent_reply"], ensure_ascii=False), turn)
            for bullet in reply.bullets:
                tag = consumer.BULLET_TAGS[bullet["section"]]
                minutes_port.add_entry(tag, bullet["text"])

    blocks.stop()
    return SimpleNamespace(
        protocol_text=protocol_path.read_text(encoding="utf-8"),
        snapshot=state.content(),
        produced=produced,
        logs=logs,
    )


def _rendered_head(protocol_text: str) -> str:
    """Everything the minutes-document block rendered, in front of the raw
    transcript -- the raw section carries the fixture's spoken text
    regardless of which blocks ran, so checking the whole file would pass
    even without the block that is actually under test."""
    head, _, _ = protocol_text.partition("## Rohtranskript")
    return head


def _assert_minutes_has_decision_task_question(head: str) -> None:
    assert "entscheidet sich" in head and "Spendenkorb" in head, \
        "decision missing from the minutes"
    assert "Jonas" in head and "28. November" in head, \
        "task (person + deadline) missing from the minutes"
    assert "Haftung" in head, "question missing from the minutes"


def test_reflect_session_runs_through_its_single_phase(tmp_path):
    fixture = _load_fixture()
    assert len({line["speaker"] for line in fixture}) == 3
    assert sum(1 for line in fixture if line.get("wake_word")) == 1
    assert sum(1 for line in fixture if "agenda_change" in line) == 1

    result = run_session(tmp_path / "session", TEMPLATE_PATH, FULL_REGISTRY, fixture)

    _assert_minutes_has_decision_task_question(_rendered_head(result.protocol_text))

    assert EXPECTED_ACTIVE <= result.produced, \
        f"expected blocks silent: {EXPECTED_ACTIVE - result.produced}"
    assert not (EXPECTED_SILENT & result.produced), \
        f"unexpectedly active: {EXPECTED_SILENT & result.produced}"

    snapshot = StageStateSnapshot.model_validate(result.snapshot)

    assert snapshot.share is not None
    words = {row.person: row.words for row in snapshot.share.shares}
    assert words.get("Mira", 0) > 0
    assert words.get("Jonas", 0) > 0
    assert words.get("Alina", 0) > 0
    assert abs(sum(row.share for row in snapshot.share.shares) - 1.0) < 1e-6

    assert snapshot.queue is not None
    assert snapshot.session_graph is not None

    # issue #181 L3: transcript:recent carries the last 10 finished lines,
    # each with its speaker.
    assert snapshot.transcript_recent, "no finished lines reached the stage snapshot"
    assert len(snapshot.transcript_recent) <= 10
    assert {line.speaker for line in snapshot.transcript_recent} >= {"Mira", "Jonas", "Alina"}
    assert all(line.text for line in snapshot.transcript_recent)


def _mutant_template_without_minutes_document(tmp_path: Path) -> Path:
    """A copy of the real Reflect template with the `minutes-document`
    block dropped from both the `[[blocks]]` list (what
    `build_session_blocks` instantiates from) and the `session` phase's
    `blocks` list (`shared.templates.load_template` refuses a phase that
    names a block the top-level list does not have)."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    without_block = text.replace('[[blocks]]\nid = "minutes-document"\n', "", 1)
    without_phase_entry = without_block.replace('"minutes-document", ', "", 1)
    assert without_phase_entry != text, "mutation did not change the template"
    tmp_path.mkdir(parents=True, exist_ok=True)
    mutant_path = tmp_path / "template.toml"
    mutant_path.write_text(without_phase_entry, encoding="utf-8")
    load_template(mutant_path)       # still a valid template, just one block short
    return mutant_path


def test_mutation_removing_minutes_document_breaks_the_decision_task_question_capture(tmp_path):
    """Named mutation check (card #157): drop `minutes-document` from a
    COPY of the Reflect template and show the same assertions the real
    template passes now fail -- red on the mutant, green on the real
    template (see the test above)."""
    fixture = _load_fixture()
    mutant_path = _mutant_template_without_minutes_document(tmp_path / "mutant")

    result = run_session(tmp_path / "session", mutant_path, FULL_REGISTRY, fixture)

    assert "minutes-document" not in result.produced
    head = _rendered_head(result.protocol_text)
    for needle in ("entscheidet sich", "Spendenkorb", "28. November", "Haftung"):
        assert needle not in head, (
            f"{needle!r} unexpectedly present without the minutes-document block"
        )
