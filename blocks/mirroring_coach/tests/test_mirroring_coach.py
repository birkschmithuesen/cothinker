# CoThinker — tests for the mirroring-coach block (blocks/mirroring_coach)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The coach lane as a block: topic eligibility, the one-shot and rhythm
triggers, Nachtrag, the LanePort adapter the session host builds for it,
the `mirror:` view intents and the legacy stage sink row.

Two kinds of tests. The ported scenarios run the REAL block host
(`build_session_blocks`), the real call lane for the host-side topic
confirmation, the real `LaneQueuePort` and real worker processes (the fake
stand-in), never a real agent. The rest drive the block directly with an
in-process fake `LanePort`.

    python3 -m pytest -q blocks/mirroring_coach/tests/test_mirroring_coach.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gateway"))
from blocks.mirroring_coach import coach as coach_mod  # noqa: E402
from blocks.mirroring_coach.coach import (  # noqa: E402
    COACH_PAUSE_S, COACH_REQUEST, COACH_REQUEST_END, COACH_REQUEST_START,
    DEFAULT_CONFIG, GROUNDING_RULE, MirroringCoach,
    resolve_config, speaker_lines, valid_coach_reply)
from call_lane import CallLane  # noqa: E402
from gateway import block_host_wiring as bhw  # noqa: E402
from gateway.block_host_wiring import (  # noqa: E402
    LaneQueuePort, build_session_blocks, coach_block_config, make_agent_for)
from gateway.stage_surface import StageSurface  # noqa: E402
from shared.block_registry import load_block  # noqa: E402
from shared.blocks import BLOCK_API_VERSION, BlockContext  # noqa: E402
from shared.lane_port import LanePort  # noqa: E402
from shared.modules import CoreEvent, StateStore  # noqa: E402
from shared.surface import ViewIntent  # noqa: E402
from stage_state import StageState  # noqa: E402

FAKE = str(ROOT / "tests" / "lane_fake_worker.py")


def forget(session_id: str) -> None:
    for p in Path(tempfile.gettempdir()).glob(f"cothinker-fake-*-{session_id}"):
        try:
            p.unlink()
        except OSError:
            pass


class Clock:
    """Controllable time, shared by the hysteresis, the block host and the
    real StageState's own pacing brake -- the fake worker subprocesses still
    run on real wall-clock sleeps (kept tiny), a different axis."""
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeResult:
    """Stands in for gateway.decide.Result -- only by_id() is used."""
    def __init__(self, neues_thema=None, rueckkehr_thema=None) -> None:
        self._by_id = {}
        if neues_thema is not None:
            self._by_id["neues_thema"] = {"p": neues_thema}
        if rueckkehr_thema is not None:
            self._by_id["rueckkehr_thema"] = {"p": rueckkehr_thema}

    def by_id(self) -> dict:
        return self._by_id


def snap(block_text: str):
    return SimpleNamespace(block=block_text)


class ObservedState(StageState):
    """The real StageState, plus a plain list of every accepted
    `apply()` call -- test observability only, no behaviour change."""
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.accepted: list = []

    def apply(self, action, spoken: str = "") -> str:
        reason = super().apply(action, spoken)
        if not reason:
            self.accepted.append(dict(action))
        return reason


class FakeWriter:
    def __init__(self, state=None) -> None:
        self.state = state
        self.writes = 0
        self._warning = ""

    def write(self) -> None:
        self.writes += 1

    def warn(self, text: str) -> None:
        self._warning = text

    def clear_warning(self, only: str = "") -> None:
        if not only or self._warning == only:
            self._warning = ""

    def warning(self) -> str:
        return self._warning


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


REFLECT = ROOT / "templates" / "reflect" / "template.toml"


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v)


def template_with(directory: Path, **config) -> Path:
    """The Reflect template with `config` added to the mirroring-coach table."""
    text = REFLECT.read_text(encoding="utf-8")
    head = '[[blocks]]\nid = "mirroring-coach"\n'
    start = text.index(head)
    end = text.index("[[blocks]]", start + len(head))
    lines = "".join(f"{k} = {_toml(v)}\n" for k, v in config.items())
    path = directory / "template.toml"
    path.write_text(text[:start] + head + lines + text[end:], encoding="utf-8")
    return path


class Rig:
    """The old `Lanes` object, rebuilt from its parts: the real block host
    with the mirroring coach on a real `LaneQueuePort`, the stage surface
    onto a real StageState, and the call lane for the host-side topic
    confirmation (`decide`: the `topics` block, then `CallLane.on_topic`)."""

    def __init__(self, tag: str, clock: Clock, *, delay: float = 45.0,
                 coach_sleep: float = 0.05, coach_final: str = "Spiegel: Testbeitrag",
                 rhythm: bool = False, call_sleep: float = 0.05,
                 log=lambda *a: None, **config) -> None:
        self.clock = clock
        base = f"cothinker-lane-it-{tag}"
        forget(f"{base}-coach")
        forget(f"{base}-call")
        self.state = ObservedState(now=clock)
        self.writer = FakeWriter(self.state)
        self.intents: list = []
        self.logs: list = []

        def _log(*a):
            self.logs.append(" ".join(str(x) for x in a))
            log(*a)
        surface = StageSurface(self.writer, self.state, _log)
        self.call = CallLane(
            call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep",
                          str(call_sleep)],
            call_session_id=f"{base}-call", project_agenda="PROJECT\nAGENDA",
            state=self.state, writer=self.writer, now=clock, log=_log)

        def block_sink(intent) -> None:
            self.intents.append(intent)
            surface.render([intent])
            self.call.observe_intent(intent)
        self.ports: dict = {}
        inner = make_agent_for(
            coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep",
                           str(coach_sleep), "--final", coach_final],
            session_id=base, log=_log)

        def agent_for(block_id: str):
            port = inner(block_id)
            if port is not None:
                self.ports[block_id] = port
            return port
        self.tmp = Path(tempfile.mkdtemp(prefix="coach-rig-"))
        template = template_with(self.tmp, lane_coach_delay=delay,
                                 coach_rhythm=rhythm, **config)
        self.blocks = build_session_blocks(
            session_dir=self.tmp / "session", sink=block_sink, log=_log,
            agent_for=agent_for, clock=clock, template_path=template)
        self.call.attach(self.blocks.dispatch)
        # As the consumer wires it: the `topics` block's output reaches the
        # call lane, which pushes the history entry and re-announces it.
        self.blocks.on_output(
            lambda payload: self.call.on_topic(payload["topic"])
            if "topic" in payload and payload.get("from") != "session-host"
            else None)
        self.call.start()

    def decide(self, result, snap, reason: str = "pause") -> None:
        """The consumer's decider callback: the block text to the call lane,
        the decider's numbers to the `topics` block."""
        by_id = result.by_id()
        block_text = str(getattr(snap, "block", "") or "")
        self.call.note_decider_block(block_text)
        self.blocks.dispatch("block_output", {"decider_result": {
            "neues_thema": (by_id.get("neues_thema") or {}).get("p"),
            "rueckkehr_thema": (by_id.get("rueckkehr_thema") or {}).get("p"),
            "block": block_text, "previous": ""}})

    @property
    def block(self) -> MirroringCoach:
        return self.blocks.get("mirroring-coach")

    @property
    def port(self) -> LaneQueuePort:
        return self.ports["mirroring-coach"]

    def coach_stats(self) -> dict:
        return self.block.stats()

    def handle_block(self, speaker: str, text: str, *, wake_word: bool = False):
        """What the consumer's `block_done` does: the `segment` event to the
        block host, then the call lane."""
        self.blocks.dispatch("segment", {
            "id": 1, "text": text, "speakers": [], "wake_word": wake_word,
            "reason": "pause", "at": self.clock(), "speaker": speaker,
            "agent_text": self.call.agent_text(text)})
        return self.call.handle_block(speaker, text, wake_word=wake_word)

    def stop(self) -> None:
        self.blocks.stop()
        self.call.stop()


_RESULTS: dict = {}


def _case_ids(names):
    return [pytest.param(i, id=n) for i, n in enumerate(names)]


def _expect(section, i, expected):
    """Run `section` once (it records its checks), then assert check `i`.

    Every check site of the section is its own pytest case; they share one
    run of the section because the scenarios are sequential and real
    (worker subprocesses, a shared fake clock)."""
    if section not in _RESULTS:
        rec: list = []

        def check(name, cond, info=""):
            rec.append((name, bool(cond), str(info)))

        err = ""
        try:
            section(check)
        except Exception:
            err = traceback.format_exc(limit=4)
        _RESULTS[section] = (rec, err)
    rec, err = _RESULTS[section]
    if i >= len(rec):
        pytest.fail(f"section stopped after {len(rec)} checks: {err}")
    name, ok, info = rec[i]
    assert ok, f"{name}: {info}"
    if i == expected - 1:
        assert len(rec) == expected, f"section recorded {len(rec)} checks"
        assert not err, err


def _run_coach_fires_once_per_confirmed_topic_no_earlier_than(check) -> None:
    """coach fires once per confirmed topic, no earlier than the delay"""
    clock = Clock()
    rig = Rig("3", clock, delay=45.0)
    state, block = rig.state, rig.block
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.8),
                               snap("Lasst uns ueber Budget reden"), "pause")
    check("hysteresis confirmed a new topic",
          block._eligible and not block._contributed)
    check("the confirmed topic got exactly one history entry",
          len(state.history) == 1, str(state.history))
    rig.handle_block("the operator", "zu frueh")
    check("coach does not fire before the minimum delay",
          rig.coach_stats()["jobs_run"] == 0)
    clock.advance(45)
    rig.handle_block("the operator", "jetzt reicht die Zeit")
    check("coach fires once the delay has passed",
          wait_until(lambda: rig.coach_stats()["jobs_run"] == 1),
          str(rig.coach_stats()))
    rig.handle_block("the operator", "noch im selben Thema")
    time.sleep(0.1)
    check("coach does not fire a second time in the same topic",
          rig.coach_stats()["jobs_run"] == 1, str(rig.coach_stats()))
    check("the coach's contribution landed INSIDE the topic's own entry, "
         "still exactly one entry",
          wait_until(lambda: len(state.history) == 1
                    and "Testbeitrag" in state.history[0].markdown),
          str(state.history))
    rig.stop()


def _run_a_topic_change_while_the_coach_works_files_the(check) -> None:
    """a topic change while the coach works files the result into an OLDER history entry as a Nachtrag, the current one is untouched"""
    clock = Clock()
    rig = Rig("4", clock, coach_sleep=0.5)
    state, writer = rig.state, rig.writer
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.95), snap("Thema eins"),
                               "pause")
    old_entry_id = state.main.id
    clock.advance(45)
    rig.handle_block("the operator", "triggert den Coach")
    check("coach job is actually running (not finished yet)",
          wait_until(lambda: rig.port.lane.running(), timeout=2.0),
          "never observed running -- the race window was missed")
    # The topic changes WHILE the coach is still working (coach_sleep=0.5s
    # gives a reliable window — confirmed: still running() at this point).
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.97), snap("Thema zwei"),
                               "pause")
    new_entry_id = state.main.id
    check("a second, different history entry exists for the new topic",
          new_entry_id != old_entry_id and len(state.history) == 2,
          str(state.history))
    check("the coach's (now stale) contribution lands in the OLD entry, "
         "marked as a Nachtrag",
          wait_until(lambda: "Nachtrag" in state.history[0].markdown
                    and "Testbeitrag" in state.history[0].markdown),
          str(state.history))
    check("the CURRENT (new) entry's markdown was never touched by the "
         "stale coach result",
          "Testbeitrag" not in state.main.markdown, state.main.markdown)
    check("a small Nachtrag notice names the OLD topic's own title "
         "(point 6d)",
          wait_until(lambda: "Thema eins" in writer._warning), writer._warning)
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.95), snap("Thema drei"),
                               "pause")
    check("the Nachtrag notice disappears on the NEXT topic change",
          writer._warning == "", writer._warning)
    rig.stop()


def _run_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already(check) -> None:
    """the Nachtrag notice never clobbers an unrelated warning already on the stage"""
    clock = Clock()
    rig = Rig("4b", clock, coach_sleep=0.5)
    state, writer = rig.state, rig.writer
    writer.warn("Achtung: Konfliktdateien im Projektordner")
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.95), snap("Thema A"), "pause")
    clock.advance(45)
    rig.handle_block("the operator", "triggert")
    wait_until(lambda: rig.port.lane.running(), timeout=2.0)
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.97), snap("Thema B"), "pause")
    check("the stale coach result still landed in the old entry",
          wait_until(lambda: "Testbeitrag" in state.history[0].markdown),
          str(state.history))
    check("an unrelated warning already showing is never overwritten by "
         "the Nachtrag notice",
          writer._warning == "Achtung: Konfliktdateien im Projektordner",
          writer._warning)
    rig.stop()


def _run_update_stable_prefix(check) -> None:
    """update_stable_prefix() changes the coach's project_agenda text too, exactly as asked (point 7)"""
    clock = Clock()
    rig = Rig("5", clock)
    before_coach = rig.block.context.project_agenda
    rig.call.update_stable_prefix("PROJECT\nTeilnehmende: the operator, the designer")
    check("coach lane's stable prefix changed too",
          rig.block.context.project_agenda != before_coach
          and "the operator, the designer" in rig.block.context.project_agenda)
    rig.stop()


def _run_robo_s_own_output_is_broadcast_into_both_lanes(check) -> None:
    """Robo's own output is broadcast into BOTH lanes' windows (point 8), never a second channel"""
    clock = Clock()
    rig = Rig("6", clock)
    rig.handle_block("the operator", "Robo, was ist offen?", wake_word=True)
    check("the call's ack reached the COACH lane's own window too",
          any("Robo (Zuruf)" == u.speaker for u in rig.block.context.window()),
          str(rig.block.context.window()))
    rig.stop()


def _run_a_confirmed_topic_shortly_after_a_call_result_still(check) -> None:
    """a confirmed topic shortly after a call result still gets its entry; the call result lives inside the topic entry; the coach attaches to the real topic (2026-10-20 replay: 'too soon')"""
    clock = Clock()
    rig = Rig("12", clock, delay=45.0)
    state, logs12 = rig.state, rig.logs
    clock.advance(95)
    rig.decide(FakeResult(neues_thema=0.84), snap("Thema Budget"),
                               "pause")
    check("first confirmed topic -> one entry", len(state.history) == 1,
          str(state.history))
    first_id = state.main.id
    clock.advance(80)
    rig.handle_block("the operator", "Robo, was kostet das?", wake_word=True)
    check("the call result lands in the current topic's entry",
          wait_until(lambda: "mitgeschrieben" in state.history[0].markdown),
          str(state.history))
    clock.advance(15)          # 95 s after topic 1, 15 s after the call result
    rig.decide(FakeResult(neues_thema=0.77), snap("Thema Personal"),
                               "pause")
    check("the second confirmed topic got its own entry despite the stage brake",
          len(state.history) == 2 and state.main.title.startswith("Thema Personal"),
          str([(h.id, h.title) for h in state.history]))
    check("no history entry is a call result",
          not any(h.title.startswith("Robo") for h in state.history),
          str([h.title for h in state.history]))
    check("one consumer log line per confirmed topic change",
          sum(1 for m in logs12 if m.startswith("topic: new ")) == 2
          and any("topic: new Thema Personal" in m for m in logs12),
          str(logs12))
    clock.advance(45)
    rig.handle_block("the operator", "weiter zum Personal")
    coach_lines = lambda: [i for i in rig.intents if i.payload.get("text", "").endswith("Testbeitrag")]
    check("the coach attaches to the real (current) topic entry",
          wait_until(lambda: "Testbeitrag" in state.main.markdown)
          and coach_lines() and coach_lines()[-1].payload["topic_id"] == state.main.id
          and state.main.id != first_id, str(state.history))
    rig.stop()


def _run_the_coach_s_stable_prefix_carries_no_call_paths(check) -> None:
    """the coach's stable prefix carries no call paths, even with the paths on the call lane"""
    clock = Clock()
    rig = Rig("paths", clock)
    rig.call.call_paths = "Projektordner: /srv/proj\nProtokoll: /srv/proj/p.md"
    rig.call.update_stable_prefix("Projekt: X")
    coach_p = rig.block.context.build_prompt("coach")
    check("the call lane's own prefix carries the paths",
          "Projektordner: /srv/proj" in rig.call.call_ctx.project_agenda)
    check("coach prompt carries no paths",
          "Projektordner" not in coach_p and "/srv/proj" not in coach_p, coach_p)
    check("the coach prompt carries the project text",
          coach_p.startswith("Projekt: X"), coach_p[:80])
    rig.stop()


def _run_stats_reports_a_cache_read_ratio_for_the_coach(check) -> None:
    """stats() reports a cache read ratio for the coach lane too"""
    clock = Clock()
    rig = Rig("stats", clock)
    stats = rig.block.stats()
    check("coach stats carry the cache read ratio",
          "cache_read_ratio" in stats, str(stats))
    check("coach stats carry the lane counters",
          "jobs_run" in stats and "jobs_dropped" in stats, str(stats))
    rig.stop()


_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than = [
    'hysteresis confirmed a new topic',
    'the confirmed topic got exactly one history entry',
    'coach does not fire before the minimum delay',
    'coach fires once the delay has passed',
    'coach does not fire a second time in the same topic',
    "the coach's contribution landed INSIDE the topic's own entry, still "
    "exactly one entry"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than))
def test_coach_fires_once_per_confirmed_topic_no_earlier_than(i):
    _expect(_run_coach_fires_once_per_confirmed_topic_no_earlier_than, i, len(_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than))


_NAMES_a_topic_change_while_the_coach_works_files_the = [
    'coach job is actually running (not finished yet)',
    'a second, different history entry exists for the new topic',
    "the coach's (now stale) contribution lands in the OLD entry, marked as "
    "a Nachtrag",
    "the CURRENT (new) entry's markdown was never touched by the stale coach "
    'result',
    "a small Nachtrag notice names the OLD topic's own title (point 6d)",
    'the Nachtrag notice disappears on the NEXT topic change']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_topic_change_while_the_coach_works_files_the))
def test_a_topic_change_while_the_coach_works_files_the(i):
    _expect(_run_a_topic_change_while_the_coach_works_files_the, i, len(_NAMES_a_topic_change_while_the_coach_works_files_the))


_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already = [
    'the stale coach result still landed in the old entry',
    'an unrelated warning already showing is never overwritten by the Nachtrag notice']


@pytest.mark.parametrize("i", _case_ids(_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already))
def test_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already(i):
    _expect(_run_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already, i, len(_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already))


_NAMES_update_stable_prefix = ["coach lane's stable prefix changed too"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_update_stable_prefix))
def test_update_stable_prefix(i):
    _expect(_run_update_stable_prefix, i, len(_NAMES_update_stable_prefix))


_NAMES_robo_s_own_output_is_broadcast_into_both_lanes = ["the call's ack reached the COACH lane's own window too"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_robo_s_own_output_is_broadcast_into_both_lanes))
def test_robo_s_own_output_is_broadcast_into_both_lanes(i):
    _expect(_run_robo_s_own_output_is_broadcast_into_both_lanes, i, len(_NAMES_robo_s_own_output_is_broadcast_into_both_lanes))


_NAMES_a_confirmed_topic_shortly_after_a_call_result_still = [
    'first confirmed topic -> one entry',
    "the call result lands in the current topic's entry",
    'the second confirmed topic got its own entry despite the stage brake',
    'no history entry is a call result',
    'one consumer log line per confirmed topic change',
    'the coach attaches to the real (current) topic entry']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_confirmed_topic_shortly_after_a_call_result_still))
def test_a_confirmed_topic_shortly_after_a_call_result_still(i):
    _expect(_run_a_confirmed_topic_shortly_after_a_call_result_still, i, len(_NAMES_a_confirmed_topic_shortly_after_a_call_result_still))


_NAMES_the_coach_s_stable_prefix_carries_no_call_paths = [
    "the call lane's own prefix carries the paths",
    'coach prompt carries no paths',
    'the coach prompt carries the project text']


@pytest.mark.parametrize("i", _case_ids(_NAMES_the_coach_s_stable_prefix_carries_no_call_paths))
def test_the_coach_s_stable_prefix_carries_no_call_paths(i):
    _expect(_run_the_coach_s_stable_prefix_carries_no_call_paths, i, len(_NAMES_the_coach_s_stable_prefix_carries_no_call_paths))


_NAMES_stats_reports_a_cache_read_ratio_for_the_coach = [
    'coach stats carry the cache read ratio',
    'coach stats carry the lane counters']


@pytest.mark.parametrize("i", _case_ids(_NAMES_stats_reports_a_cache_read_ratio_for_the_coach))
def test_stats_reports_a_cache_read_ratio_for_the_coach(i):
    _expect(_run_stats_reports_a_cache_read_ratio_for_the_coach, i, len(_NAMES_stats_reports_a_cache_read_ratio_for_the_coach))


# ---------------------------------------------------------------- new tests
# The block driven directly, with an in-process fake LanePort.

class FakeAgent:
    """A `LanePort` that records submissions; the test settles them."""
    def __init__(self) -> None:
        self.jobs: list = []
        self.started = False
        self.stopped = False
        self.rolls = 0

    def submit(self, job_id, prompt, *, on_result, on_dropped) -> None:
        self.jobs.append(SimpleNamespace(id=job_id, prompt=prompt,
                                         on_result=on_result,
                                         on_dropped=on_dropped))

    def stats(self) -> dict:
        return {"jobs_run": len(self.jobs), "jobs_dropped": 0}

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True

    def on_roll(self) -> None:
        self.rolls += 1


class Harness:
    def __init__(self, tmp_path, *, agent="fake", **config) -> None:
        self.clock = Clock()
        self.agent = FakeAgent() if agent == "fake" else agent
        self.emitted: list = []
        self.artefacts: dict = {}
        self.traces: list = []
        self.state_dir = tmp_path / "state"
        self.ctx = BlockContext(
            instance=None, session_dir=tmp_path,
            state=StateStore(self.state_dir), agent=self.agent, llm=None,
            language="de", clock=self.clock, emit=self.emitted.append,
            artefact=lambda n, t: self.artefacts.__setitem__(n, t),
            trace=lambda e, f: self.traces.append((e, dict(f))))
        self.block = MirroringCoach()
        self.block.start(self.ctx, config)

    def send(self, kind, payload):
        return self.block.on_event(CoreEvent(kind, payload))

    def segment(self, text, speaker="Ann", **kw):
        return self.send("segment", {"text": text, "speaker": speaker, **kw})

    def topic(self, entry_id="t1", title="Budget", *, returned=False,
              eligible=True, **kw):
        t = {"id": entry_id, "title": title, "returned": returned,
             "coach_eligible": eligible, **kw}
        return self.send("block_output", {"from": "topics", "topic": t})

    def settle(self, job, final="Spiegel: ok", *, ok=True, **extra):
        job.on_result({"ok": ok, "final": final, "error": "", **extra}, 0.1)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def _fire_one_shot(h: Harness):
    h.topic()
    h.clock.advance(46)
    h.segment("Wir reden ueber das Budget.")
    assert len(h.agent.jobs) == 1
    return h.agent.jobs[0]


# --- LanePort adapter ------------------------------------------------------

def _port(tag, *, sleep=0.05, timeout=60.0, final="Spiegel: x", mode="ok"):
    sid = f"cothinker-lane-it-port-{tag}"
    forget(sid)
    port = LaneQueuePort(
        command=[sys.executable, FAKE, "--mode", mode, "--sleep", str(sleep),
                 "--final", final],
        session_id=sid, turn_timeout_s=timeout, log=lambda *a: None)
    port.start()
    return port


def test_lane_port_adapter_submit_delivers_result_and_duration():
    port = _port("a")
    got: list = []
    port.submit("job-1", "prompt", on_result=lambda r, d: got.append((r, d)),
                on_dropped=lambda: got.append("dropped"))
    assert wait_until(lambda: got)
    result, duration = got[0]
    assert result["ok"] is True and result["final"] == "Spiegel: x"
    assert isinstance(duration, float) and duration >= 0
    assert "timed_out" not in result
    port.stop()


def test_lane_port_adapter_stats_counts_jobs():
    port = _port("b")
    done: list = []
    port.submit("job-1", "p", on_result=lambda r, d: done.append(r),
                on_dropped=lambda: None)
    assert wait_until(lambda: done)
    stats = port.stats()
    assert stats["jobs_run"] == 1 and stats["jobs_dropped"] == 0
    assert isinstance(stats, dict)
    port.stop()


def test_lane_port_adapter_is_a_lane_port():
    port = _port("c")
    assert callable(port.submit) and callable(port.stats)
    assert LanePort.__name__ == "LanePort"
    port.stop()


def test_lane_port_adapter_newest_wins_and_reports_the_dropped_job():
    port = _port("d", sleep=0.6)
    events: list = []

    def submit(name):
        port.submit(name, "p", on_result=lambda r, d: events.append(("result", name)),
                    on_dropped=lambda: events.append(("dropped", name)))
    submit("first")
    assert wait_until(lambda: port.lane.running())
    submit("second")
    submit("third")
    assert ("dropped", "second") in events
    assert wait_until(lambda: ("result", "third") in events, timeout=6)
    assert ("result", "second") not in events
    assert port.stats()["jobs_dropped"] == 1
    port.stop()


def test_lane_port_adapter_flags_a_turn_that_outlived_its_timeout():
    port = _port("e", sleep=3.0, timeout=0.3)
    got: list = []
    port.submit("job-1", "p", on_result=lambda r, d: got.append(r),
                on_dropped=lambda: None)
    assert wait_until(lambda: got, timeout=6)
    assert got[0]["timed_out"] is True and got[0]["ok"] is False
    port.stop()


def test_lane_port_adapter_reports_a_job_still_waiting_at_stop():
    port = _port("f", sleep=1.0)
    events: list = []
    port.submit("a", "p", on_result=lambda r, d: events.append("result"),
                on_dropped=lambda: events.append("dropped-a"))
    assert wait_until(lambda: port.lane.running())
    port.submit("b", "p", on_result=lambda r, d: events.append("result-b"),
                on_dropped=lambda: events.append("dropped-b"))
    port.lane.stop(grace=0.3)
    assert "dropped-b" in events


def test_lane_port_adapter_survives_a_raising_callback():
    port = _port("g")
    logged: list = []
    port.log = lambda *a: logged.append(a)
    done: list = []

    def boom(r, d):
        done.append(1)
        raise RuntimeError("boom")
    port.submit("job-1", "p", on_result=boom, on_dropped=lambda: None)
    assert wait_until(lambda: done and logged)
    port.submit("job-2", "p", on_result=lambda r, d: done.append(2),
                on_dropped=lambda: None)
    assert wait_until(lambda: 2 in done)
    port.stop()


def test_lane_port_adapter_over_an_agent_seam_lane_factory():
    made: dict = {}

    class SeamLane:
        def __init__(self, session_id, on_result, on_dropped, briefing):
            made.update(session_id=session_id, briefing=briefing)
            self.on_result = on_result
            self.rolls = 0

        def submit(self, job):
            self.on_result(job, {"ok": True, "final": "Frage: ja?"}, 0.2)
            return False

        def stats(self):
            return {"jobs_run": 1}

        def start(self):
            return True

        def stop(self, grace=5.0):
            pass

        def on_roll(self):
            self.rolls += 1

    port = LaneQueuePort(command=[], session_id="sess", lane_factory=SeamLane,
                         briefing="BRIEF", log=lambda *a: None)
    got: list = []
    port.submit("j", "p", on_result=lambda r, d: got.append((r, d)),
                on_dropped=lambda: None)
    assert got == [({"ok": True, "final": "Frage: ja?"}, 0.2)]
    assert made == {"session_id": "sess", "briefing": "BRIEF"}
    port.on_roll()
    assert port.lane.rolls == 1


def test_make_agent_for_builds_a_port_only_for_the_coach():
    agent_for = make_agent_for(coach_command=[sys.executable, FAKE],
                               session_id="sess-x", log=lambda *a: None)
    assert agent_for("capture-bullets") is None
    port = agent_for("mirroring-coach")
    assert isinstance(port, LaneQueuePort)
    assert port.lane.worker.session_id == "sess-x-coach"
    assert port.turn_timeout_s == bhw.COACH_TURN_TIMEOUT_S == 60.0


def test_the_block_is_registered_for_the_reflect_template():
    assert type(load_block("mirroring-coach")) is MirroringCoach
    block = MirroringCoach()
    assert block.id == "mirroring-coach" and block.api_version == BLOCK_API_VERSION


# --- block configuration -----------------------------------------------------

def test_block_config_defaults_match_the_former_coach_flags():
    assert DEFAULT_CONFIG["lane_coach_delay"] == 45.0       # --lane-coach-delay
    assert DEFAULT_CONFIG["coach_rhythm"] is False          # --coach-rhythm
    assert DEFAULT_CONFIG["lane_coach_worker_command"] == ""  # --lane-coach-worker-command
    assert coach_mod.COACH_MIN_DELAY_S == 45.0


def test_the_template_carries_the_coach_flags_with_the_same_defaults():
    from shared.templates import load_template, merged_block_config
    cfg = merged_block_config(load_template(REFLECT), None)["mirroring-coach"]
    assert cfg == {"lane_coach_delay": 45.0, "coach_rhythm": False,
                   "lane_coach_worker_command": ""}


def test_coach_block_config_is_the_template_over_the_defaults():
    assert coach_block_config() == dict(DEFAULT_CONFIG)
    assert coach_block_config(REFLECT.parent / "template.toml")["lane_coach_delay"] == 45.0


def test_resolve_config_lets_the_template_override_a_default():
    assert resolve_config({"lane_coach_delay": 10.0})["lane_coach_delay"] == 10.0
    assert resolve_config(None) == dict(DEFAULT_CONFIG)


def test_block_config_reaches_the_block_through_the_host(tmp_path):
    template = template_with(tmp_path, lane_coach_delay=7.5, coach_rhythm=True)
    blocks = build_session_blocks(session_dir=tmp_path / "s", sink=lambda i: None,
                                  log=lambda m: None, template_path=template)
    block = blocks.get("mirroring-coach")
    assert block._min_delay_s == 7.5 and block._rhythm is True


# --- inert without an agent ---------------------------------------------------

def test_the_block_is_inert_without_an_agent(tmp_path):
    h = Harness(tmp_path, agent=None)
    h.topic()
    h.clock.advance(100)
    assert h.segment("Hallo zusammen") == ()
    assert h.send("tick", {}) == ()
    assert h.emitted == []


def test_the_host_without_lanes_gives_the_block_no_agent(tmp_path):
    blocks = build_session_blocks(session_dir=tmp_path / "s", sink=lambda i: None,
                                  log=lambda m: None)
    block = blocks.get("mirroring-coach")
    assert block is not None and block._agent is None
    assert blocks.dispatch("segment", {"text": "x", "speaker": "A"}) > 0


def test_the_host_hands_the_block_its_lane_port(tmp_path):
    seen: list = []

    def agent_for(block_id):
        seen.append(block_id)
        return FakeAgent() if block_id == "mirroring-coach" else None
    blocks = build_session_blocks(session_dir=tmp_path / "s", sink=lambda i: None,
                                  log=lambda m: None, agent_for=agent_for)
    assert "mirroring-coach" in seen
    assert isinstance(blocks.get("mirroring-coach")._agent, FakeAgent)
    assert blocks.get("mirroring-coach")._agent.started is True
    blocks.stop()
    assert blocks.get("mirroring-coach")._agent.stopped is True


# --- events in ----------------------------------------------------------------

def test_a_segment_goes_into_the_coach_window_under_its_speaker(h):
    h.segment("Wir brauchen mehr Zeit.", speaker="Ann")
    assert [(u.speaker, u.text) for u in h.block.context.window()] == [
        ("Ann", "Wir brauchen mehr Zeit.")]


def test_a_segment_prefers_the_prefiltered_agent_text(h):
    h.segment("aehm also aehm Budget", agent_text="also Budget")
    assert h.block.context.window()[0].text == "also Budget"


def test_a_wake_word_segment_belongs_to_the_call_lane(h):
    h.segment("Robo, fass zusammen", wake_word=True)
    assert h.block.context.window() == []


def test_a_segment_without_speaker_is_attributed_to_unbekannt(h):
    h.send("segment", {"text": "ohne Namen"})
    assert h.block.context.window()[0].speaker == "unbekannt"


def test_speaker_split_lines_become_one_utterance_each(h):
    h.segment("Ann: eins\nBen: zwei\nweiter", speaker="x")
    assert [(u.speaker, u.text) for u in h.block.context.window()] == [
        ("Ann", "eins"), ("Ben", "zwei weiter")]
    assert speaker_lines("A", "kein umbruch") == [("A", "kein umbruch")]


def test_the_assistants_own_line_joins_the_window_as_an_ordinary_line(h):
    h.send("block_output", {"from": "session-host",
                            "robo_line": {"speaker": "Robo (Zuruf)", "text": "Ack"}})
    assert [(u.speaker, u.text) for u in h.block.context.window()] == [
        ("Robo (Zuruf)", "Ack")]


def test_the_blocks_own_output_is_ignored_when_routed_back(h):
    h.send("block_output", {"from": "mirroring-coach",
                            "robo_line": {"speaker": "x", "text": "y"}})
    assert h.block.context.window() == []


def test_a_confirmed_topic_makes_the_coach_eligible_after_the_delay(h):
    h.topic()
    h.segment("zu frueh")
    assert h.agent.jobs == []
    h.clock.advance(46)
    h.segment("jetzt")
    assert len(h.agent.jobs) == 1
    h.segment("noch einmal")
    assert len(h.agent.jobs) == 1                 # once per topic


def test_a_returned_topic_never_makes_the_coach_eligible(h):
    h.topic(returned=True, eligible=False, entry_id="")
    h.clock.advance(100)
    h.segment("wieder da")
    assert h.agent.jobs == []


def test_a_topic_the_stage_refused_still_resets_the_trigger(h):
    h.topic(entry_id="")                          # no history entry pushed
    h.clock.advance(46)
    h.segment("los")
    assert len(h.agent.jobs) == 1
    assert "[Thema" not in h.agent.jobs[0].prompt


def test_the_prompt_carries_the_topic_header_and_the_request(h):
    job = _fire_one_shot(h)
    assert "[Thema t1: Budget]" in job.prompt
    assert COACH_REQUEST in job.prompt
    assert "Ann: Wir reden ueber das Budget." in job.prompt


def test_a_retitle_updates_the_header_of_the_running_topic(h):
    h.topic("t1", "Budget")
    h.send("block_output", {"from": "session-host", "topic": {
        "id": "t1", "title": "Haushalt", "retitled": True}})
    h.clock.advance(46)
    h.segment("los")
    assert "[Thema t1: Haushalt]" in h.agent.jobs[0].prompt


def test_the_stable_prefix_event_replaces_the_agenda_and_keeps_the_grounding(h):
    h.send("block_output", {"from": "session-host",
                            "stable_prefix": "Projekt: X\nTeilnehmende: A, B"})
    prefix = h.block.context.project_agenda
    assert prefix.startswith("Projekt: X\nTeilnehmende: A, B")
    assert GROUNDING_RULE in prefix


def test_a_window_roll_tells_the_lane(tmp_path):
    h = Harness(tmp_path, window_size=3, roll_every=2)
    for i in range(6):
        h.segment(f"Satz {i}")
    assert h.agent.rolls >= 1


def test_utterance_events_move_the_pause_clock(tmp_path):
    h = Harness(tmp_path, coach_rhythm=True)
    h.topic()
    h.clock.advance(60)
    h.send("utterance", {"text": "gerade gesprochen"})
    assert h.block.coach_tick() is None           # mid-exchange
    h.clock.advance(COACH_PAUSE_S + 1)
    assert h.block.coach_tick() == "start"


# --- the grounding rule ---------------------------------------------------------

def test_the_grounding_rule_is_appended_to_the_coach_briefing(tmp_path):
    h = Harness(tmp_path, project_agenda="Projekt: X")
    assert h.block.briefing() == f"Projekt: X\n\n{GROUNDING_RULE}"


def test_the_grounding_rule_rides_on_every_coach_prompt(h):
    job = _fire_one_shot(h)
    assert GROUNDING_RULE in job.prompt
    assert job.prompt.index(GROUNDING_RULE) < job.prompt.index("## Auftrag")


def test_the_grounding_text_states_the_rule_of_the_architecture():
    assert "Block- oder Werkzeugergebnissen" in GROUNDING_RULE
    assert "grounding" in GROUNDING_RULE


def test_the_grounding_text_can_come_from_the_template(tmp_path):
    h = Harness(tmp_path, grounding="Nur Belegtes.")
    assert h.block.briefing() == "Nur Belegtes."
    assert GROUNDING_RULE not in h.block.briefing()


def test_without_a_grounding_text_the_briefing_is_just_the_agenda(tmp_path):
    h = Harness(tmp_path, project_agenda="P", grounding="")
    assert h.block.briefing() == "P"


# --- view intents out --------------------------------------------------------------

def test_a_contribution_is_emitted_as_a_mirror_prompt_intent_for_the_room(h):
    job = _fire_one_shot(h)
    h.settle(job, "Spiegel: Es geht um Geld.")
    assert len(h.emitted) == 1
    intent = h.emitted[0]
    assert isinstance(intent, ViewIntent)
    assert intent.id == "mirror:1"
    assert intent.kind == "prompt" and intent.audience == "room"
    assert intent.block == "mirroring-coach"
    assert intent.payload["text"] == "Robo (Coach): Spiegel: Es geht um Geld."
    assert intent.payload["line"] == "Spiegel: Es geht um Geld."
    assert intent.payload["topic_id"] == "t1" and intent.payload["stale"] is False
    assert intent.payload["contribution"] == "reply"
    assert intent.payload["addition"] == "Spiegel: Es geht um Geld."
    assert intent.expires_at is None


def test_every_contribution_gets_its_own_mirror_number(tmp_path):
    h = Harness(tmp_path, coach_rhythm=False)
    h.topic("t1")
    h.clock.advance(46)
    h.segment("a")
    h.settle(h.agent.jobs[0])
    h.topic("t2", "Zwei")
    h.clock.advance(46)
    h.segment("b")
    h.settle(h.agent.jobs[1], "Frage: und dann?")
    assert [i.id for i in h.emitted] == ["mirror:1", "mirror:2"]


def test_the_coachs_own_words_join_its_window(h):
    h.settle(_fire_one_shot(h), "Struktur: erst A, dann B")
    assert ("Robo (Coach)", "Struktur: erst A, dann B") in [
        (u.speaker, u.text) for u in h.block.context.window()]


def test_silence_emits_nothing(h):
    h.settle(_fire_one_shot(h), "[SILENT]")
    assert h.emitted == []


@pytest.mark.parametrize("final", ["Ich glaube, das passt.",
                                   "Spiegel: " + "x" * 700, ""])
def test_an_unusable_reply_shows_nothing_and_is_recorded(h, final):
    h.settle(_fire_one_shot(h), final)
    assert [i.payload["contribution"] for i in h.emitted] == ["failure"]
    assert all(i.payload["text"] == "" for i in h.emitted)
    attempts = StateStore(h.state_dir).read("coach-history.json")["attempts"]
    assert len(attempts) == 1 and attempts[0]["final"] == final


def test_a_failed_turn_shows_nothing_and_is_recorded(h):
    h.settle(_fire_one_shot(h), "", ok=False)
    assert [i.payload["contribution"] for i in h.emitted] == ["failure"]
    assert h.emitted[0].payload["text"] == "" and h.emitted[0].payload["record"]["ok"] is False
    assert len(StateStore(h.state_dir).read("coach-history.json")["attempts"]) == 1


def test_a_timed_out_turn_shows_nothing_and_is_recorded(h):
    h.settle(_fire_one_shot(h), "", ok=False, timed_out=True)
    assert [i.payload["contribution"] for i in h.emitted] == ["failure"]
    assert h.emitted[0].payload["text"] == "" and h.emitted[0].payload["record"]["ok"] is False
    assert len(StateStore(h.state_dir).read("coach-history.json")["attempts"]) == 1


def test_a_dropped_job_emits_nothing(h):
    _fire_one_shot(h).on_dropped()
    assert h.emitted == []


def test_a_late_result_is_labelled_a_nachtrag_with_the_old_topics_title(h):
    job = _fire_one_shot(h)
    h.topic("t2", "Personal")
    h.settle(job, "Spiegel: spaet")
    text = h.emitted[-1].payload["text"]
    assert "Nachtrag" in text and "„Budget“" in text and text.endswith("spaet")
    assert h.emitted[-1].payload["stale"] is True
    p = h.emitted[-1].payload
    assert p["contribution"] == "Nachtrag" and p["notice"] is True
    assert p["topic_id"] == "t1" and p["topic_title"] == "Budget"
    assert p["addition"].startswith("**Nachtrag ") and p["addition"].endswith("spaet")


def test_the_next_new_topic_clears_the_nachtrag_notice(h):
    job = _fire_one_shot(h)
    h.topic("t2", "Personal")
    h.settle(job, "Spiegel: spaet")
    h.topic("t3", "Drittes")
    clear = h.emitted[-1]
    assert clear.payload["contribution"] == "clear" and clear.payload["text"] == ""


def test_a_contribution_is_kept_as_an_artefact(h):
    h.settle(_fire_one_shot(h), "Spiegel: Geld")
    body = h.artefacts["mirroring-coach.md"]
    assert body.startswith("# Mirroring coach") and "Spiegel: Geld" in body


def test_the_trace_carries_no_conversation_text(h):
    h.settle(_fire_one_shot(h), "Spiegel: Geld")
    names = [e for e, _ in h.traces]
    assert "coach_fired" in names and "coach_reply" in names
    assert all("Geld" not in json.dumps(f) for _, f in h.traces)


# --- rhythm ------------------------------------------------------------------------------

def _rhythm(tmp_path) -> Harness:
    h = Harness(tmp_path, coach_rhythm=True)
    h.topic()
    return h


def test_the_rhythm_never_fires_the_one_shot_trigger(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(100)
    h.segment("Gerede")
    assert h.agent.jobs == []


def test_the_rhythm_opens_a_topic_in_the_first_pause_after_the_delay(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    assert h.send("tick", {}) == ()
    assert len(h.agent.jobs) == 1
    assert COACH_REQUEST_START in h.agent.jobs[0].prompt
    assert h.agent.jobs[0].id.startswith("coach-start-")


def test_the_rhythm_waits_for_the_delay(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(44)
    assert h.block.coach_tick() is None


def test_the_rhythm_never_speaks_mid_exchange(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    h.send("utterance", {"text": "gerade"})
    assert h.block.coach_tick() is None


def test_a_silent_rhythm_turn_costs_only_a_retry_pause(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    assert h.block.coach_tick() == "start"
    h.settle(h.agent.jobs[0], "[SILENT]")
    assert h.block.coach_tick() is None           # retry pause
    h.clock.advance(coach_mod.COACH_RETRY_S + 1)
    assert h.block.coach_tick() == "start"


def test_a_spoken_start_blocks_the_next_live_fire_for_the_min_gap(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    h.block.coach_tick()
    h.settle(h.agent.jobs[0], "Frage: worum geht es?")
    h.clock.advance(coach_mod.COACH_LONG_TOPIC_S)
    assert h.block.coach_tick() is None           # < COACH_MIN_GAP_S
    h.clock.advance(coach_mod.COACH_MIN_GAP_S)
    assert h.block.coach_tick() == "pause"


def test_one_rhythm_turn_at_a_time(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    assert h.block.coach_tick() == "start"
    assert h.block.coach_tick() is None           # in flight


def test_a_dropped_rhythm_job_frees_the_slot(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    h.block.coach_tick()
    h.agent.jobs[0].on_dropped()
    h.clock.advance(coach_mod.COACH_RETRY_S + 1)
    assert h.block.coach_tick() == "start"


def test_the_rhythm_ends_a_topic_with_its_structure_before_the_next_one(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(60)
    h.topic("t2", "Neues")
    end = h.agent.jobs[-1]
    assert end.id.startswith("coach-end-")
    assert COACH_REQUEST_END in end.prompt
    assert "[Thema t1: Budget]" in end.prompt     # the CLOSED topic's header
    h.settle(end, "Struktur: A, B")
    assert h.emitted[-1].payload["text"].startswith("Robo (Coach): Abschluss ")
    assert h.emitted[-1].payload["contribution"] == "Abschluss"
    assert h.emitted[-1].payload["addition"].startswith("**Abschluss ")
    assert h.emitted[-1].payload["notice"] is False


def test_a_too_young_topic_gets_no_end_turn(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(10)
    h.topic("t2", "Neues")
    assert h.agent.jobs == []


def test_a_long_topic_gets_a_long_pause_turn_after_its_opening(tmp_path):
    h = _rhythm(tmp_path)
    h.clock.advance(46)
    h.block.coach_tick()
    h.settle(h.agent.jobs[0], "[SILENT]")
    h.clock.advance(coach_mod.COACH_LONG_TOPIC_S)
    assert h.block.coach_tick() == "pause"


# --- helpers -------------------------------------------------------------------------------

def test_valid_coach_reply_contract():
    assert valid_coach_reply("[SILENT]") and valid_coach_reply(" [SILENT] ")
    for label in ("Spiegel:", "Struktur:", "Frage:"):
        assert valid_coach_reply(f"{label} etwas")
    assert not valid_coach_reply("Hallo")
    assert not valid_coach_reply("Frage: " + "x" * 600)


def test_valid_coach_reply_pins_600_char_boundary():
    label = "Frage: "
    text_600 = label + "x" * (600 - len(label))
    text_601 = label + "x" * (601 - len(label))
    assert len(text_600) == 600
    assert len(text_601) == 601
    assert valid_coach_reply(text_600)
    assert not valid_coach_reply(text_601)


# --- the legacy stage sink's mirror row --------------------------------------------------------

def _surface(tmp_path, *, coach_history_path=None):
    clock = Clock()
    state = ObservedState(now=clock)
    writer = FakeWriter(state)
    logs: list = []
    return (StageSurface(writer, state, logs.append,
                         coach_history_path=coach_history_path),
            state, writer, logs)


def _mirror_intent(contribution="reply", **payload):
    return ViewIntent(id="mirror:1", block="mirroring-coach", kind="prompt",
                      audience="room",
                      payload={"text": "Robo (Coach): x", "contribution": contribution,
                               **payload})


def test_the_mirror_row_files_the_reply_into_the_topics_entry_not_the_mirror_line(tmp_path):
    surface, state, writer, logs = _surface(tmp_path)
    state.confirmed_topic("Budget", "Es geht um Geld")
    entry = state.main.id
    surface.render([_mirror_intent(topic_id=entry, addition="Spiegel: x")])
    assert "Spiegel: x" in state.history[0].markdown
    assert state.mirror != "Robo (Coach): x" and writer.writes == 1


def test_the_mirror_row_falls_back_to_the_jsonl_when_the_entry_is_gone(tmp_path):
    path = tmp_path / "coach-history.jsonl"
    surface, state, writer, logs = _surface(tmp_path, coach_history_path=path)
    rec = {"at": "t", "stale": False, "ok": True, "final": "Spiegel: x",
           "topic_started_at": 1.0}
    surface.render([_mirror_intent(topic_id="gone", addition="Spiegel: x", record=rec)])
    assert json.loads(path.read_text(encoding="utf-8")) == rec
    assert writer.writes == 0


def test_the_mirror_row_records_a_failure_in_the_jsonl(tmp_path):
    path = tmp_path / "coach-history.jsonl"
    surface, state, writer, logs = _surface(tmp_path, coach_history_path=path)
    rec = {"at": "t", "stale": False, "ok": False, "final": "",
           "topic_started_at": 1.0}
    surface.render([_mirror_intent("failure", record=rec)])
    assert json.loads(path.read_text(encoding="utf-8")) == rec
    assert writer.writes == 0 and state.accepted == []


def test_the_mirror_row_raises_the_nachtrag_notice_and_clears_it(tmp_path):
    surface, state, writer, _ = _surface(tmp_path)
    state.confirmed_topic("Eins", "x")
    entry = state.main.id
    surface.render([_mirror_intent("Nachtrag", topic_id=entry,
                                   addition="**Nachtrag 10:00:** y", notice=True)])
    assert "Nachtrag" in writer.warning() and "Eins" in writer.warning()
    surface.render([_mirror_intent("clear")])
    assert writer.warning() == ""


def test_the_mirror_row_never_clobbers_an_unrelated_warning(tmp_path):
    surface, state, writer, _ = _surface(tmp_path)
    writer.warn("Achtung: Konflikt")
    state.confirmed_topic("Eins", "x")
    surface.render([_mirror_intent("Nachtrag", topic_id=state.main.id,
                                   addition="**Nachtrag 10:00:** y", notice=True)])
    assert writer.warning() == "Achtung: Konflikt"
    surface.render([_mirror_intent("clear")])
    assert writer.warning() == "Achtung: Konflikt"


def test_the_block_to_sink_path_end_to_end(tmp_path):
    surface, state, writer, _ = _surface(tmp_path)
    h = Harness(tmp_path)
    h.ctx.emit = lambda intent: surface.render([intent])  # the surface as the host's emit
    state.confirmed_topic("Budget", "Geld")
    h.topic(state.main.id, "Budget")
    h.clock.advance(46)
    h.segment("Wir reden ueber das Budget.")
    h.settle(h.agent.jobs[0], "Frage: wohin?")
    assert "Frage: wohin?" in state.history[0].markdown
    assert state.mirror != "Robo (Coach): Frage: wohin?"


# --- the block stays inside its boundary ----------------------------------------------------------

def test_the_block_never_imports_the_session_host():
    for name in ("coach.py", "context.py", "__init__.py"):
        text = (Path(coach_mod.__file__).parent / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import gateway", "from gateway",
                                            "import stage", "from stage",
                                            "import workshop", "from workshop"))
