# CoThinker — characterization tests for the lane integration (call lane + coach lane)
# Copyright (C) 2026 Ann Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Lanes: call ack + call lane + coach eligibility + coach lane + history.

Real worker processes (the fake stand-in), never a real agent. Uses the
REAL StageState (not a hand-rolled fake) so the topic/history mechanics
(the pacing brake, append_to_history(), history_title()) are exercised
as they really behave, sharing the test's own fake clock so the brake's
timing lines up with the simulated time advances below.

    python3 -m pytest -q tests/test_lane_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
import time
from pathlib import Path
from types import SimpleNamespace

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
from lane_context import EXCERPT_CHARS  # noqa: E402
from lane_integration import (  # noqa: E402
    CALL_ANSWER_RULE, CALL_ANSWER_VISIBLE_S, CALL_INTENT_SHAPES, MIRROR_EXPIRY_S,
    Lanes, call_paths_text, extra_dirs_from_env)
from stage_state import StageState  # noqa: E402

HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "lane_fake_worker.py")


def forget(session_id: str) -> None:
    for p in Path(tempfile.gettempdir()).glob(f"cothinker-fake-*-{session_id}"):
        try:
            p.unlink()
        except OSError:
            pass


class Clock:
    """Controllable time, shared by the hysteresis AND the real
    StageState's own pacing brake -- the fake worker subprocesses still
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
    def __init__(self) -> None:
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


def make_lanes(tag: str, clock: Clock, *, coach_min_delay_s: float = 45.0,
              call_sleep: float = 0.05, coach_sleep: float = 0.05,
              coach_final: str = "Spiegel: Testbeitrag",
              call_mode: str = "ok", log=lambda *a: None):
    call_id = f"cothinker-lane-it-call-{tag}"
    coach_id = f"cothinker-lane-it-coach-{tag}"
    forget(call_id)
    forget(coach_id)
    state = ObservedState(now=clock)
    writer = FakeWriter()
    lanes = Lanes(
        call_command=[sys.executable, FAKE, "--mode", call_mode, "--sleep",
                      str(call_sleep)],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep",
                       str(coach_sleep), "--final", coach_final],
        call_session_id=call_id, coach_session_id=coach_id,
        project_agenda="PROJECT\nAGENDA", state=state, writer=writer,
        coach_min_delay_s=coach_min_delay_s, now=clock, log=log)
    lanes.start()
    return lanes, state, writer


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


def _run_a_wake_word_block_gets_an_instant_ack_then(check) -> None:
    """a wake-word block gets an instant ack, then the real result"""
    clock = Clock()
    lanes, state, writer = make_lanes("1", clock)
    ack = lanes.handle_block("the operator", "Robo, fass zusammen", wake_word=True)
    check("ack returned immediately", ack == "Robo: Zusammenfassung läuft …",
          ack)
    check("ack already on the stage", state.mirror == ack, state.mirror)
    check("call lane result eventually lands",
          wait_until(lambda: "mitgeschrieben" in state.mirror), state.mirror)
    time.sleep(0.2)
    check("a call result never creates its own topic entry (2026-10-20 "
         "replay: 'Robo: Antwort' entries crowded out the real topics)",
          not any(a.get("action") == "topic" for a in state.accepted)
          and not state.history, str(state.accepted))
    lanes.stop()


def _run_a_non_call_block_never_triggers_the_call_lane(check) -> None:
    """a non-call block never triggers the call lane"""
    clock = Clock()
    lanes, state, writer = make_lanes("2", clock)
    result = lanes.handle_block("the operator", "Wir sollten das Budget besprechen.",
                                wake_word=False)
    check("no ack for a plain block", result is None)
    check("call lane never ran", lanes.call_lane.stats()["jobs_run"] == 0)
    lanes.stop()


def _run_coach_fires_once_per_confirmed_topic_no_earlier_than(check) -> None:
    """coach fires once per confirmed topic, no earlier than the delay"""
    clock = Clock()
    lanes, state, writer = make_lanes("3", clock, coach_min_delay_s=45.0)
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.8),
                            snap("Lasst uns ueber Budget reden"), "pause")
    check("hysteresis confirmed a new topic",
          lanes._coach_eligible and not lanes._coach_contributed)
    check("the confirmed topic got exactly one history entry",
          len(state.history) == 1, str(state.history))
    lanes.handle_block("the operator", "zu frueh", wake_word=False)
    check("coach does not fire before the minimum delay",
          lanes.coach_lane.stats()["jobs_run"] == 0)
    clock.advance(45)
    lanes.handle_block("the operator", "jetzt reicht die Zeit", wake_word=False)
    check("coach fires once the delay has passed",
          wait_until(lambda: lanes.coach_lane.stats()["jobs_run"] == 1),
          str(lanes.coach_lane.stats()))
    lanes.handle_block("the operator", "noch im selben Thema", wake_word=False)
    time.sleep(0.1)
    check("coach does not fire a second time in the same topic",
          lanes.coach_lane.stats()["jobs_run"] == 1, str(lanes.coach_lane.stats()))
    check("the coach's contribution landed INSIDE the topic's own entry, "
         "still exactly one entry",
          wait_until(lambda: len(state.history) == 1
                    and "Testbeitrag" in state.history[0].markdown),
          str(state.history))
    lanes.stop()


def _run_a_topic_change_while_the_coach_works_files_the(check) -> None:
    """a topic change while the coach works files the result into an OLDER history entry as a Nachtrag, the current one is untouched"""
    clock = Clock()
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
        history_path = Path(fh.name)
    lanes, state, writer = make_lanes("4", clock, coach_sleep=0.5)
    lanes.history_path = history_path
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.95), snap("Thema eins"),
                            "pause")
    old_entry_id = state.main.id
    clock.advance(45)
    lanes.handle_block("the operator", "triggert den Coach", wake_word=False)
    check("coach job is actually running (not finished yet)",
          wait_until(lambda: lanes.coach_lane.running(), timeout=2.0),
          "never observed running -- the race window was missed")
    # The topic changes WHILE the coach is still working (coach_sleep=0.5s
    # gives a reliable window — confirmed: still running() at this point).
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.97), snap("Thema zwei"),
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
    lanes.on_decider_result(FakeResult(neues_thema=0.95), snap("Thema drei"),
                            "pause")
    check("the Nachtrag notice disappears on the NEXT topic change",
          writer._warning == "", writer._warning)
    history_path.unlink(missing_ok=True)
    lanes.stop()


def _run_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already(check) -> None:
    """the Nachtrag notice never clobbers an unrelated warning already on the stage"""
    clock = Clock()
    lanes, state, writer = make_lanes("4b", clock, coach_sleep=0.5)
    writer.warn("Achtung: Konfliktdateien im Projektordner")
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.95), snap("Thema A"), "pause")
    old_id_4b = state.main.id
    clock.advance(45)
    lanes.handle_block("the operator", "triggert", wake_word=False)
    wait_until(lambda: lanes.coach_lane.running(), timeout=2.0)
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.97), snap("Thema B"), "pause")
    check("the stale coach result still landed in the old entry",
          wait_until(lambda: "Testbeitrag" in state.history[0].markdown),
          str(state.history))
    check("an unrelated warning already showing is never overwritten by "
         "the Nachtrag notice",
          writer._warning == "Achtung: Konfliktdateien im Projektordner",
          writer._warning)
    lanes.stop()


def _run_update_stable_prefix(check) -> None:
    """update_stable_prefix() changes the project_agenda text on both lanes, exactly as asked (point 7)"""
    clock = Clock()
    lanes, state, writer = make_lanes("5", clock)
    before_call = lanes.call_ctx.project_agenda
    before_coach = lanes.coach_ctx.project_agenda
    lanes.update_stable_prefix("PROJECT\nTeilnehmende: the operator, the designer")
    check("call lane's stable prefix changed",
          lanes.call_ctx.project_agenda != before_call
          and "the operator, the designer" in lanes.call_ctx.project_agenda)
    check("coach lane's stable prefix changed too",
          lanes.coach_ctx.project_agenda != before_coach
          and "the operator, the designer" in lanes.coach_ctx.project_agenda)
    lanes.stop()


def _run_robo_s_own_output_is_broadcast_into_both_lanes(check) -> None:
    """Robo's own output is broadcast into BOTH lanes' windows (point 8), never a second channel"""
    clock = Clock()
    lanes, state, writer = make_lanes("6", clock)
    lanes.handle_block("the operator", "Robo, was ist offen?", wake_word=True)
    check("the call's ack reached the COACH lane's own window too",
          any("Robo (Zuruf)" == u.speaker for u in lanes.coach_ctx.window()),
          str(lanes.coach_ctx.window()))
    lanes.stop()


def _run_a_call_turn_that_outlives_its_timeout_aborts_visibly(check) -> None:
    """a call turn that outlives its timeout aborts visibly on stage (point 2)"""
    clock = Clock()
    call_id7, coach_id7 = "cothinker-lane-it-call-7", "cothinker-lane-it-coach-7"
    forget(call_id7)
    forget(coach_id7)
    state7 = ObservedState(now=clock)
    writer7 = FakeWriter()
    lanes7 = Lanes(
        call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "3.0"],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05",
                       "--final", "[SILENT]"],
        call_session_id=call_id7, coach_session_id=coach_id7,
        project_agenda="P", state=state7, writer=writer7,
        call_turn_timeout_s=0.3, coach_turn_timeout_s=0.3,
        now=clock, log=lambda *a: None)
    lanes7.start()
    lanes7.handle_block("the operator", "Robo, strukturiere", wake_word=True)
    check("a call turn stuck past its own timeout shows the abort line",
          wait_until(lambda: "abgebrochen" in state7.mirror, timeout=5.0),
          state7.mirror)
    check("the configured 0.3s timeout is what actually fired (not a "
         "left-over default)",
          lanes7.call_lane.stats()["jobs_timed_out"] == 1,
          str(lanes7.call_lane.stats()))
    lanes7.stop()


def _run_four_back_to_back_calls_every_one_answered_none(check) -> None:
    """four back-to-back calls: every one answered, none dropped, every ack resolved (2026-10-20 replay: 3 of 4 silently dropped)"""
    clock = Clock()
    logs8: list = []
    lanes, state, writer = make_lanes("8", clock, call_sleep=0.6,
                                      log=lambda *a: logs8.append(" ".join(map(str, a))))
    seen8: list = []
    _apply8 = state.apply
    state.apply = lambda action, spoken="": (seen8.append(action.get("text"))
                                             if action.get("action") == "mirror"
                                             else None) or _apply8(action, spoken)
    lanes.handle_block("the operator", "Robo, recherchiere eins", wake_word=True)
    wait_until(lambda: lanes.call_lane.running())
    for i in ("zwei", "drei", "vier"):
        lanes.handle_block("the operator", f"Robo, recherchiere {i}", wake_word=True)
    check("the waiting slot never held more than one job",
          lanes.call_lane.waiting_count() == 1
          and lanes.call_lane.stats()["max_waiting_seen"] == 1)
    check("two jobs ran: the first call, then the three merged",
          wait_until(lambda: lanes.call_lane.stats()["jobs_run"] == 2, timeout=6),
          str(lanes.call_lane.stats()))
    stats8 = lanes.call_lane.stats()
    check("no call was dropped", stats8["jobs_dropped"] == 0, str(stats8))
    check("all four calls were answered",
          sum(int(m.split(" call(s)")[0].rsplit(" ", 1)[1]) for m in logs8
              if " answered, " in m) == 4, str(logs8))
    check("the stage ends on an answer, never on a running ack",
          wait_until(lambda: state.mirror.startswith("Robo: OK")), state.mirror)
    lanes.stop()


def _run_a_merged_call_job_s_prompt_lists_every_pending(check) -> None:
    """a merged call job's prompt lists every pending call in order"""
    clock = Clock()
    lanes, state, writer = make_lanes("9", clock, call_sleep=0.6)
    captured9: list = []
    _merge9 = lanes._merge_calls
    lanes.call_lane.merge = lambda w, n: captured9.append(_merge9(w, n)) or captured9[-1]
    lanes.handle_block("the operator", "Robo, recherchiere eins", wake_word=True)
    wait_until(lambda: lanes.call_lane.running())
    lanes.handle_block("the operator", "Robo, recherchiere zwei", wake_word=True)
    lanes.handle_block("the operator", "Robo, recherchiere drei", wake_word=True)
    prompt9 = captured9[-1].prompt if captured9 else ""
    check("both pending calls are in the merged prompt, numbered, in order",
          "1. Robo, recherchiere zwei" in prompt9
          and "2. Robo, recherchiere drei" in prompt9
          and prompt9.index("1. Robo") < prompt9.index("2. Robo"), prompt9[-400:])
    lanes.stop()


def _run_a_failed_call_turns_its_ack_into_a_visible(check) -> None:
    """a failed call turns its ack into a visible failure line"""
    clock = Clock()
    lanes, state, writer = make_lanes("10", clock, call_mode="fail")
    lanes.handle_block("the operator", "Robo, recherchiere das", wake_word=True)
    check("the ack is replaced by 'Zuruf nicht geschafft'",
          wait_until(lambda: "Zuruf nicht geschafft" in state.mirror), state.mirror)
    lanes.stop()


def _run_a_call_still_waiting_at_shutdown_resolves_its_ack(check) -> None:
    """a call still waiting at shutdown resolves its ack too"""
    clock = Clock()
    lanes, state, writer = make_lanes("11", clock, call_sleep=1.0)
    lanes.handle_block("the operator", "Robo, recherchiere eins", wake_word=True)
    wait_until(lambda: lanes.call_lane.running())
    lanes.handle_block("the operator", "Robo, recherchiere zwei", wake_word=True)
    lanes.call_lane.stop(grace=0.3)
    check("the mirror shows the failure line, not the running ack",
          "Zuruf nicht geschafft" in state.mirror, state.mirror)
    lanes.coach_lane.stop()


def _run_a_confirmed_topic_shortly_after_a_call_result_still(check) -> None:
    """a confirmed topic shortly after a call result still gets its entry; the call result lives inside the topic entry; the coach attaches to the real topic (2026-10-20 replay: 'too soon')"""
    clock = Clock()
    logs12: list = []
    lanes, state, writer = make_lanes("12", clock, coach_min_delay_s=45.0,
                                      log=lambda *a: logs12.append(" ".join(map(str, a))))
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.84), snap("Thema Budget"),
                            "pause")
    check("first confirmed topic -> one entry", len(state.history) == 1,
          str(state.history))
    first_id = state.main.id
    clock.advance(80)
    lanes.handle_block("the operator", "Robo, was kostet das?", wake_word=True)
    check("the call result lands in the current topic's entry",
          wait_until(lambda: "mitgeschrieben" in state.history[0].markdown),
          str(state.history))
    clock.advance(15)          # 95 s after topic 1, 15 s after the call result
    lanes.on_decider_result(FakeResult(neues_thema=0.77), snap("Thema Personal"),
                            "pause")
    check("the second confirmed topic got its own entry despite the stage brake",
          len(state.history) == 2 and state.main.title.startswith("Thema Personal"),
          str([(h.id, h.title) for h in state.history]))
    check("no history entry is a call result",
          not any(h.title.startswith("Robo") for h in state.history),
          str([h.title for h in state.history]))
    check("one consumer log line per confirmed topic change",
          sum(1 for m in logs12 if m.startswith("topic: new ")) == 2
          and any("topic: new Thema Personal p=0.77" in m for m in logs12),
          str(logs12))
    clock.advance(45)
    lanes.handle_block("the operator", "weiter zum Personal", wake_word=False)
    check("the coach attaches to the real (current) topic entry",
          wait_until(lambda: "Testbeitrag" in state.main.markdown)
          and state.main.id != first_id, str(state.history))
    lanes.stop()


def _run_the_call_lane_s_stable_prefix_names_the_project(check) -> None:
    """the call lane's stable prefix names the project folder; the coach's does not"""
    lanes8 = Lanes(
        call_command=[sys.executable, FAKE], coach_command=[sys.executable, FAKE],
        call_session_id="cothinker-lane-it-paths-call",
        coach_session_id="cothinker-lane-it-paths-coach",
        project_agenda="Projekt: X", state=ObservedState(now=Clock()),
        writer=FakeWriter(),
        call_paths=call_paths_text("/srv/proj", "/srv/proj/sitzungen/p.md"))
    p1 = lanes8._call_prompt([{"text": "Robo, was steht in kosten.md?"}])
    lanes8.call_ctx.add_utterance("the operator", "noch ein Satz")
    p2 = lanes8._call_prompt([{"text": "Robo, und die offenen Punkte?"}])
    coach_p = lanes8.coach_ctx.build_prompt("coach")
    check("call prompt carries Projektordner", "Projektordner: /srv/proj\n" in p1, p1)
    check("call prompt carries Protokoll",
          "Protokoll: /srv/proj/sitzungen/p.md" in p1, p1)
    check("call prompt carries the read-only-targeted line",
          "Lies Dateien nur gezielt aus dem Projektordner." in p1, p1)
    check("coach prompt carries no paths",
          "Projektordner" not in coach_p and "/srv/proj" not in coach_p, coach_p)
    prefix = p1.split("\n\n")[0]
    check("call prefix is byte-stable across turns",
          p2.startswith(prefix + "\n\n") and prefix.startswith("Projekt: X\n"),
          repr(p2[:200]))
    lanes8.update_stable_prefix("Projekt: X\nTeilnehmende: A, B")
    p3 = lanes8._call_prompt([{"text": "Robo?"}])
    check("the paths survive the enrolled-names prefix update",
          "Teilnehmende: A, B\nProjektordner: /srv/proj" in p3, p3)
    ex = call_paths_text("/srv/proj", "/srv/proj/p.md",
                         extra_dirs_from_env("/srv/ref-a::/srv/ref-b "))
    check("COTHINKER_EXTRA_DIRS: one read-only line per dir plus the rule once",
          ex.endswith("Zusatzquelle (nur lesen): /srv/ref-a\n"
                      "Zusatzquelle (nur lesen): /srv/ref-b\n"
                      "Für Fragen zu CoThinker zuerst dort nachsehen.\n"
                      "Soll ein Bild gezeigt werden, schreib eine eigene Zeile "
                      "BILD: <absoluter Pfad>."), ex)
    import tempfile as _tf  # noqa: E402
    with _tf.TemporaryDirectory(dir=os.environ.get("HERMES_SCRATCH")) as _d:
        _src = Path(_d) / "src"
        _ref = Path(_d) / "ref"
        _src.mkdir()
        _ref.mkdir()
        (_src / "vision.md").write_text("x", encoding="utf-8")
        (_ref / "README.md").write_text("y", encoding="utf-8")
        (_ref / "vision.md").symlink_to(_src / "vision.md")
        (_ref / "dead.md").symlink_to(_src / "missing.md")
        t = call_paths_text("/srv/proj", "/srv/proj/p.md", [str(_ref)])
        check("a symlink reference folder lists its files (README first, dead "
              "links skipped) so read_file can reach them",
              "Dateien (direkt mit read_file lesen, search_files findet Links "
              "nicht): README.md, vision.md\n" in t and "dead.md" not in t, t)
        check("a plain reference folder lists nothing extra",
              "Dateien (direkt" not in call_paths_text("/p", "/p/x.md", [str(_src)]))
    check("COTHINKER_EXTRA_DIRS empty: text unchanged",
          call_paths_text("/srv/proj", "/srv/proj/p.md", extra_dirs_from_env(""))
          == call_paths_text("/srv/proj", "/srv/proj/p.md"))
    check("without call_paths the prefix is unchanged (old behaviour)",
          Lanes(call_command=[sys.executable, FAKE],
                coach_command=[sys.executable, FAKE],
                call_session_id="x-c", coach_session_id="x-k",
                project_agenda="P", state=ObservedState(now=Clock()),
                writer=FakeWriter()).call_ctx.project_agenda == "P")


def _run_across_multiple_window_rolls_no_stale_utterance_leaks_into(check) -> None:
    """across multiple window rolls, no stale utterance leaks into the actually-submitted prompt outside the window/Stand summary"""
    # window_size=3, roll_every=2 so a handful of calls already produce
    # several rolls. Each call's own marker is placed AFTER 85 'X's --
    # EXCERPT_CHARS (80) truncates the Stand's excerpt before it ever reaches
    # the marker, so a folded utterance's marker should vanish from every
    # LATER prompt entirely, not just move into a summary.
    clock = Clock()
    call_id9, coach_id9 = "cothinker-lane-it-call-9", "cothinker-lane-it-coach-9"
    forget(call_id9)
    forget(coach_id9)
    state9 = ObservedState(now=clock)
    writer9 = FakeWriter()
    lanes9 = Lanes(
        call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        call_session_id=call_id9, coach_session_id=coach_id9,
        project_agenda="P", state=state9, writer=writer9,
        window_size=3, roll_every=2, now=clock, log=lambda *a: None)
    lanes9.start()
    captured_prompts: list = []
    # Intercepts the exact prompt handle_block() hands to the real queue --
    # never actually runs a turn, so this stays fast and needs no echo mode.
    lanes9.call_lane.submit = lambda job: captured_prompts.append(job.prompt)
    LONG_FILLER = "X" * (EXCERPT_CHARS + 5)
    for i in range(6):
        lanes9.handle_block("the operator", f"{LONG_FILLER}MARKER{i}END", wake_word=True)
    check("6 calls queued exactly 6 prompts", len(captured_prompts) == 6,
          str(len(captured_prompts)))
    last_prompt = captured_prompts[-1]
    check("at least one roll actually happened by the last call",
          "## Stand" in last_prompt, last_prompt[:400])
    check("an early marker, long folded past the excerpt length, is gone "
         "from the latest prompt entirely -- not even inside the Stand "
         "summary",
          "MARKER0END" not in last_prompt, last_prompt)
    check("a second early marker is gone too",
          "MARKER1END" not in last_prompt, last_prompt)
    check("the current call's own marker is present -- it is this turn's "
         "request",
          "MARKER5END" in last_prompt, last_prompt[-400:])
    check("stats() reports a cache read ratio per lane (point: 'report "
         "cache read ratio per lane')",
          "cache_read_ratio" in lanes9.stats()["call"]
          and "cache_read_ratio" in lanes9.stats()["coach"],
          str(lanes9.stats()))
    lanes9.stop()


def _run_a_window_roll_is_logged_for_the_lane_it(check) -> None:
    """a window roll is logged for the lane it happened on (the add_utterance() return value is actually evaluated, not dropped)"""
    clock = Clock()
    call_id10, coach_id10 = "cothinker-lane-it-call-10", "cothinker-lane-it-coach-10"
    forget(call_id10)
    forget(coach_id10)
    state10 = ObservedState(now=clock)
    writer10 = FakeWriter()
    logged10: list = []
    lanes10 = Lanes(
        call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        call_session_id=call_id10, coach_session_id=coach_id10,
        project_agenda="P", state=state10, writer=writer10,
        window_size=3, roll_every=2, now=clock,
        log=lambda *a: logged10.append(" ".join(str(x) for x in a)))
    lanes10.start()
    for i in range(6):
        lanes10.handle_block("the operator", f"Satz {i} ist lang genug um zu zaehlen.",
                             wake_word=True)
    check("at least one roll was logged for the call lane",
          any("call lane" in line and "roll" in line for line in logged10),
          str(logged10))
    lanes10.stop()


def _run_a_call_answer_that_is_the_literal_word_none(check) -> None:
    """a call answer that is the literal word 'none' is rejected -- the ack stays on stage, 'none' is never published as Robo's answer (live finding 3, 2026-10-26 live session, 18:44)"""
    # live finding 3, 2026-10-26 live session, 18:44
    clock = Clock()
    call_id11, coach_id11 = "cothinker-lane-it-call-11", "cothinker-lane-it-coach-11"
    forget(call_id11)
    forget(coach_id11)
    state11 = ObservedState(now=clock)
    writer11 = FakeWriter()
    logged11: list = []
    lanes11 = Lanes(
        call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05",
                      "--final", "none"],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        call_session_id=call_id11, coach_session_id=coach_id11,
        project_agenda="P", state=state11, writer=writer11,
        now=clock, log=lambda *a: logged11.append(" ".join(str(x) for x in a)))
    lanes11.start()
    ack11 = lanes11.handle_block("the operator", "Robo, was ist das Thema?",
                                wake_word=True)
    check("the call lane finished", wait_until(
        lambda: lanes11.call_lane.stats()["jobs_run"] == 1))
    check("the literal word 'none' never reaches the mirror as Robo's answer",
          state11.mirror == ack11, state11.mirror)
    check("the rejection is logged", any("meta" in l.lower() for l in logged11),
          str(logged11))
    lanes11.stop()


def _run_a_call_answer_that_reports_its_own_status_instead(check) -> None:
    """a call answer that reports its own status instead of answering is rejected the same way (live finding 3, 2026-10-26 live session, 19:44: "Thema: Wake-Word-Test ... keine Dateiänderung")"""
    # live finding 3, 2026-10-26 live session, 19:44
    clock = Clock()
    call_id12, coach_id12 = "cothinker-lane-it-call-12", "cothinker-lane-it-coach-12"
    forget(call_id12)
    forget(coach_id12)
    state12 = ObservedState(now=clock)
    writer12 = FakeWriter()
    STATUS_TEXT = "Thema: Wake-Word-Test – keine Dateiänderung"
    lanes12 = Lanes(
        call_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05",
                      "--final", STATUS_TEXT],
        coach_command=[sys.executable, FAKE, "--mode", "ok", "--sleep", "0.05"],
        call_session_id=call_id12, coach_session_id=coach_id12,
        project_agenda="P", state=state12, writer=writer12,
        now=clock, log=lambda *a: None)
    lanes12.start()
    ack12 = lanes12.handle_block("the operator", "Robo, was ist das Thema?",
                                wake_word=True)
    check("the second call lane finished", wait_until(
        lambda: lanes12.call_lane.stats()["jobs_run"] == 1))
    check("a self-status report never reaches the mirror as Robo's answer",
          state12.mirror == ack12, state12.mirror)
    lanes12.stop()


def _run_a_call_answer_on_the_mirror_fades_back_to(check) -> None:
    """a call answer on the mirror fades back to empty after the expiry window -- the answer already lives in the topic's history entry, so nothing is lost (live finding 4, 2026-10-26 live session: an answer from 18:59 stood under the title for 40 minutes straight)"""
    # live finding 4, 2026-10-26 live session
    clock = Clock()
    lanes, state, writer = make_lanes("13", clock)
    ack13 = lanes.handle_block("the operator", "Robo, fass zusammen", wake_word=True)
    check("the call lane finished", wait_until(
        lambda: lanes.call_lane.stats()["jobs_run"] == 1))
    check("the real answer replaced the ack before the expiry window",
          state.mirror not in ("", ack13), state.mirror)
    clock.advance(MIRROR_EXPIRY_S + 1)
    lanes.mirror_tick()
    check("the mirror fades back to empty after the expiry window",
          state.mirror == "", state.mirror)
    lanes.stop()


def _run_mirror_tick(check) -> None:
    """mirror_tick() never clobbers a DIFFERENT writer's line -- only the call lane's own last-written text expires"""
    clock = Clock()
    lanes, state, writer = make_lanes("14", clock)
    lanes.handle_block("the operator", "Robo, fass zusammen", wake_word=True)
    check("the call lane finished", wait_until(
        lambda: lanes.call_lane.stats()["jobs_run"] == 1))
    state.apply({"action": "mirror", "text": "Robo hört mit."})
    clock.advance(MIRROR_EXPIRY_S + 1)
    lanes.mirror_tick()
    check("a line written by someone else meanwhile is left untouched",
          state.mirror == "Robo hört mit.", state.mirror)
    lanes.stop()

    # 27.10.2026: the funding overview answered a call, landed
    # in the topic's history, and was gone from the room's view within seconds
    # once the topic moved on -- `mirror` fades in 60s and `main`/`topic` is
    # free to move at any time (by design). `call_answer` is neither: it
    # outlives both, for CALL_ANSWER_VISIBLE_S.
    from lane_integration import CALL_ANSWER_VISIBLE_S  # noqa: E402


def _run_a_call_answer_also_lands_on_call_answer_independent(check) -> None:
    """a call answer also lands on call_answer, independent of mirror and of the current topic"""
    clock = Clock()
    lanes, state, writer = make_lanes("15", clock)
    lanes.handle_block("the operator", "Robo, fass zusammen", wake_word=True)
    check("the call lane finished", wait_until(
        lambda: lanes.call_lane.stats()["jobs_run"] == 1))
    check("call_answer carries the real answer, same text as the mirror",
          state.call_answer == state.mirror and state.call_answer != "",
          state.call_answer)
    state.apply({"action": "topic", "title": "Naechstes Thema", "markdown": "x"})
    check("a topic change does not clear call_answer",
          state.call_answer != "", state.call_answer)
    clock.advance(MIRROR_EXPIRY_S + 1)
    lanes.mirror_tick()
    check("call_answer survives the mirror's own, shorter expiry",
          state.call_answer != "", state.call_answer)
    clock.advance(CALL_ANSWER_VISIBLE_S - MIRROR_EXPIRY_S)
    lanes.mirror_tick()
    check("call_answer fades back to empty only after its own, longer window",
          state.call_answer == "", state.call_answer)
    lanes.stop()


def _run_an_answer_rule_rides_on_every_call_prompt_and(check) -> None:
    """an answer rule rides on every call prompt, and a structure call gets its own shape"""
    lanes8 = Lanes(call_command=[sys.executable, FAKE], coach_command=[sys.executable, FAKE],
                   call_session_id="cothinker-lane-it-shape-call",
                   coach_session_id="cothinker-lane-it-shape-coach",
                   project_agenda="Projekt: X", state=ObservedState(now=Clock()),
                   writer=FakeWriter())
    # Answer style (live test 2026-10-20 17:14: a wall of text about Robo's own steps)
    p_s = lanes8._call_prompt([{"text": "Robo, strukturier das bitte.", "intent": "structure"}])
    check("call prompt carries the answer rule (result only, no report)",
          CALL_ANSWER_RULE in p_s, p_s[-300:])
    check("a structure call gets the topics + sub-steps shape",
          CALL_INTENT_SHAPES["structure"] in p_s, p_s[-300:])
    p_q = lanes8._call_prompt([{"text": "Robo, was steht in kosten.md?", "intent": "question"}])
    check("a plain question gets the answer rule but no structure shape",
          CALL_ANSWER_RULE in p_q and CALL_INTENT_SHAPES["structure"] not in p_q, p_q[-300:])


def _run_a_confirmed_topic_sets_the_headline_and_opens_the(check) -> None:
    """a confirmed topic sets the headline and opens the protocol block stamped with the BLOCK's time, not the decision's"""
    from datetime import datetime as _dt  # noqa: E402
    clock = Clock()
    lanes, state, writer = make_lanes("t1-proto", clock)
    opened: list = []
    writer.open_protocol_topic = lambda title, stamp=None: opened.append((title, stamp)) or True
    clock.advance(95)
    block_at = _dt(2026, 9, 26, 12, 31, 5)
    lanes.on_decider_result(FakeResult(neues_thema=0.9),
                            SimpleNamespace(block="Bausteine abwaegen und Budget", at=block_at),
                            "pause")
    check("headline = the confirmed topic's title", state.headline == state.main.title
          and state.headline, (state.headline, state.main.title))
    check("protocol topic opened once, stamped 12:31 (block time)",
          opened == [(state.main.title, "12:31")], str(opened))
    lanes.on_decider_result(FakeResult(rueckkehr_thema=0.9),
                            SimpleNamespace(block="zurueck", at=block_at), "pause")
    check("a confirmed return opens no protocol block", len(opened) == 1, str(opened))
    lanes.stop()


def _run_with_a_titler_wired_the_confirmed_topic_does_not(check) -> None:
    """with a titler wired the confirmed topic does NOT set the headline; the retitle of the current entry does"""
    class _FakeTitler:
        def __init__(self, on_title, on_fail):
            self.on_title = on_title
            self.requests: list = []

        def request(self, entry_id, provisional, block, previous=""):
            self.requests.append((entry_id, provisional))

        def stop(self):
            pass


    clock = Clock()
    state_t = ObservedState(now=clock)
    lanes_t = Lanes(call_command=[sys.executable, FAKE], coach_command=[sys.executable, FAKE],
                    call_session_id="cothinker-lane-it-call-titler",
                    coach_session_id="cothinker-lane-it-coach-titler",
                    project_agenda="P", state=state_t, writer=FakeWriter(), now=clock,
                    titler_factory=_FakeTitler, log=lambda *a: None)
    clock.advance(95)
    lanes_t.on_decider_result(FakeResult(neues_thema=0.9), snap("Bausteine abwaegen"), "pause")
    check("titler wired: headline left to the titler",
          state_t.headline == "" and state_t.main.title, state_t.headline)
    prov = state_t.main.title
    check("titler wired: retitle of the current entry sets the headline",
          state_t.retitle(state_t.main.id, prov, "Bausteine im Budget")
          and state_t.headline == "Bausteine im Budget", state_t.headline)


def _run_a_snapshot_without_at_still_opens_the_block(check) -> None:
    """a snapshot without `at` still opens the block (stamp None = now)"""
    clock = Clock()
    lanes, state, writer = make_lanes("t1-noat", clock)
    opened2: list = []
    writer.open_protocol_topic = lambda title, stamp=None: opened2.append(stamp) or True
    clock.advance(95)
    lanes.on_decider_result(FakeResult(neues_thema=0.9), snap("Thema ohne Zeit"), "pause")
    check("stamp None when the snapshot has no time", opened2 == [None], str(opened2))
    lanes.stop()


def _run_zuruf_lane_pending_at_once_a_named_project_file(check) -> None:
    """Zuruf lane: pending at once, a named project file on the stage WITHOUT a model, the worker refines into that entry"""
    import file_match as fm  # noqa: E402
    proj = Path(tempfile.mkdtemp(prefix="lane-files-"))
    (proj / "kosten.md").write_text("# Kosten\n\n| Posten | PT |\n|---|---|\n| A | 3 |\n",
                                    encoding="utf-8")
    (proj / "protokoll-2026-09-26.md").write_text("# Kosten\n\n## Rohtranskript\n- x\n",
                                                  encoding="utf-8")
    clock = Clock()
    lanes, state, writer = make_lanes("t3-file", clock, call_sleep=1.0)
    lanes.file_index = fm.FileIndex(proj, ignore_keys=("robo",))
    t0 = time.monotonic()
    ack = lanes.handle_block("Ann", "Robo, zeig mal die Kosten", wake_word=True)
    sync_s = time.monotonic() - t0
    check("handle_block returns well under the worker's turn (no model on this path)",
          sync_s < 0.5, f"{sync_s:.3f}s")
    check("kosten.md is on the stage immediately", state.main.title == "Kosten"
          and "Posten" in state.main.markdown
          and state.main.markdown.endswith("*Datei: kosten.md*"), state.main.markdown)
    check("never the protocol file", "Rohtranskript" not in state.main.markdown)
    check("one pending of kind main with the file in its label",
          [(p.kind, p.label.startswith("Zuruf: Kosten")) for p in state.pending]
          == [("main", True)], str(state.pending))
    file_entry = state.main.id
    check("the worker's answer lands, the pending ends",
          wait_until(lambda: not state.pending, timeout=5.0), str(state.pending))
    check("the refinement is appended to the FILE's entry",
          wait_until(lambda: "**Robo (" in (state.history[-1].markdown
                                            if state.history else "")),
          state.history[-1].markdown if state.history else "")
    check("still the same entry on display", state.main.id == file_entry)
    lanes.stop()


def _run_the_worker_s_answer_goes_to_the_file_s(check) -> None:
    """the worker's answer goes to the file's entry even after the stage moved on"""
    clock = Clock()
    lanes, state, writer = make_lanes("t3-moved", clock, call_sleep=1.0)
    lanes.file_index = fm.FileIndex(proj, ignore_keys=("robo",))
    lanes.handle_block("Ann", "Robo, zeig mal die Kosten", wake_word=True)
    file_entry = state.main.id
    clock.advance(120)
    check("a later topic takes the stage",
          state.apply({"action": "topic", "title": "Anderes Thema",
                       "markdown": "# Anderes Thema\n\ntext"}) == "", state.main.title)
    check("the answer still lands in the file's entry, not the new main",
          wait_until(lambda: any(e.id == file_entry and "**Robo (" in e.markdown
                                 for e in state.history))
          and "**Robo (" not in state.main.markdown, str([e.title for e in state.history]))
    lanes.stop()


def _run_no_file_hit_pending_with_the_intent_label_no(check) -> None:
    """no file hit: pending with the intent label, no surface change"""
    clock = Clock()
    lanes, state, writer = make_lanes("t3-nofile", clock, call_sleep=0.5)
    lanes.file_index = fm.FileIndex(proj, ignore_keys=("robo",))
    lanes.handle_block("Ann", "Robo, recherchier das bitte", wake_word=True)
    check("pending 'Zuruf: Recherche' at once",
          [p.label for p in state.pending] == ["Zuruf: Recherche"], str(state.pending))
    check("main untouched", not state.history)
    check("pending settled after the answer", wait_until(lambda: not state.pending, 5.0))
    lanes.stop()


def _run_the_call_prompt_tells_the_worker_the_file_is(check) -> None:
    """the call prompt tells the worker the file is already shown"""
    lanes, state, writer = make_lanes("t3-shown", Clock())
    calls = [{"text": "zeig die Kosten", "intent": "generic", "file_rel": "kosten.md"}]
    check("CALL_FILE_SHOWN names the file",
          "kosten.md" in lanes._call_prompt(calls)
          and "steht schon auf der Bühne" in lanes._call_prompt(calls))
    lanes.stop()


def _run_a_failing_worker_settles_the_pending_as_failed(check) -> None:
    """a failing worker settles the pending as failed"""
    clock = Clock()
    lanes, state, writer = make_lanes("t3-fail", clock, call_mode="fail")
    lanes.handle_block("Ann", "Robo, fass zusammen", wake_word=True)
    check("pending ends after the failure", wait_until(lambda: not state.pending, 5.0),
          str(state.pending))
    lanes.stop()


def _run_a_dropped_call_job_settles_its_pending(check) -> None:
    """a dropped call job settles its pending"""
    clock = Clock()
    lanes, state, writer = make_lanes("t3-drop", clock, call_sleep=0.5)
    pid = state.announce("main", "Zuruf: Test")
    lanes._on_call_dropped(SimpleNamespace(id="x", meta={"calls": [{"pending_id": pid}]}))
    check("dropped -> pending gone", not state.pending, str(state.pending))
    lanes.stop()


_NAMES_a_wake_word_block_gets_an_instant_ack_then = ['ack returned immediately',
 'ack already on the stage',
 'call lane result eventually lands',
 'a call result never creates its own topic entry (2026-10-20 replay: '
 "'Robo: Antwort' entries crowded out the real topics)"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_wake_word_block_gets_an_instant_ack_then))
def test_a_wake_word_block_gets_an_instant_ack_then(i):
    _expect(_run_a_wake_word_block_gets_an_instant_ack_then, i, len(_NAMES_a_wake_word_block_gets_an_instant_ack_then))


_NAMES_a_non_call_block_never_triggers_the_call_lane = ['no ack for a plain block', 'call lane never ran']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_non_call_block_never_triggers_the_call_lane))
def test_a_non_call_block_never_triggers_the_call_lane(i):
    _expect(_run_a_non_call_block_never_triggers_the_call_lane, i, len(_NAMES_a_non_call_block_never_triggers_the_call_lane))


_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than = ['hysteresis confirmed a new topic',
 'the confirmed topic got exactly one history entry',
 'coach does not fire before the minimum delay',
 'coach fires once the delay has passed',
 'coach does not fire a second time in the same topic',
 "the coach's contribution landed INSIDE the topic's own entry, still "
 'exactly one entry']


@pytest.mark.parametrize("i", _case_ids(_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than))
def test_coach_fires_once_per_confirmed_topic_no_earlier_than(i):
    _expect(_run_coach_fires_once_per_confirmed_topic_no_earlier_than, i, len(_NAMES_coach_fires_once_per_confirmed_topic_no_earlier_than))


_NAMES_a_topic_change_while_the_coach_works_files_the = ['coach job is actually running (not finished yet)',
 'a second, different history entry exists for the new topic',
 "the coach's (now stale) contribution lands in the OLD entry, marked as a "
 'Nachtrag',
 "the CURRENT (new) entry's markdown was never touched by the stale coach "
 'result',
 "a small Nachtrag notice names the OLD topic's own title (point 6d)",
 'the Nachtrag notice disappears on the NEXT topic change']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_topic_change_while_the_coach_works_files_the))
def test_a_topic_change_while_the_coach_works_files_the(i):
    _expect(_run_a_topic_change_while_the_coach_works_files_the, i, len(_NAMES_a_topic_change_while_the_coach_works_files_the))


_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already = ['the stale coach result still landed in the old entry',
 'an unrelated warning already showing is never overwritten by the '
 'Nachtrag notice']


@pytest.mark.parametrize("i", _case_ids(_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already))
def test_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already(i):
    _expect(_run_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already, i, len(_NAMES_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already))


_NAMES_update_stable_prefix = ["call lane's stable prefix changed",
 "coach lane's stable prefix changed too"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_update_stable_prefix))
def test_update_stable_prefix(i):
    _expect(_run_update_stable_prefix, i, len(_NAMES_update_stable_prefix))


_NAMES_robo_s_own_output_is_broadcast_into_both_lanes = ["the call's ack reached the COACH lane's own window too"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_robo_s_own_output_is_broadcast_into_both_lanes))
def test_robo_s_own_output_is_broadcast_into_both_lanes(i):
    _expect(_run_robo_s_own_output_is_broadcast_into_both_lanes, i, len(_NAMES_robo_s_own_output_is_broadcast_into_both_lanes))


_NAMES_a_call_turn_that_outlives_its_timeout_aborts_visibly = ['a call turn stuck past its own timeout shows the abort line',
 'the configured 0.3s timeout is what actually fired (not a left-over '
 'default)']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_turn_that_outlives_its_timeout_aborts_visibly))
def test_a_call_turn_that_outlives_its_timeout_aborts_visibly(i):
    _expect(_run_a_call_turn_that_outlives_its_timeout_aborts_visibly, i, len(_NAMES_a_call_turn_that_outlives_its_timeout_aborts_visibly))


_NAMES_four_back_to_back_calls_every_one_answered_none = ['the waiting slot never held more than one job',
 'two jobs ran: the first call, then the three merged',
 'no call was dropped',
 'all four calls were answered',
 'the stage ends on an answer, never on a running ack']


@pytest.mark.parametrize("i", _case_ids(_NAMES_four_back_to_back_calls_every_one_answered_none))
def test_four_back_to_back_calls_every_one_answered_none(i):
    _expect(_run_four_back_to_back_calls_every_one_answered_none, i, len(_NAMES_four_back_to_back_calls_every_one_answered_none))


_NAMES_a_merged_call_job_s_prompt_lists_every_pending = ['both pending calls are in the merged prompt, numbered, in order']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_merged_call_job_s_prompt_lists_every_pending))
def test_a_merged_call_job_s_prompt_lists_every_pending(i):
    _expect(_run_a_merged_call_job_s_prompt_lists_every_pending, i, len(_NAMES_a_merged_call_job_s_prompt_lists_every_pending))


_NAMES_a_failed_call_turns_its_ack_into_a_visible = ["the ack is replaced by 'Zuruf nicht geschafft'"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_failed_call_turns_its_ack_into_a_visible))
def test_a_failed_call_turns_its_ack_into_a_visible(i):
    _expect(_run_a_failed_call_turns_its_ack_into_a_visible, i, len(_NAMES_a_failed_call_turns_its_ack_into_a_visible))


_NAMES_a_call_still_waiting_at_shutdown_resolves_its_ack = ['the mirror shows the failure line, not the running ack']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_still_waiting_at_shutdown_resolves_its_ack))
def test_a_call_still_waiting_at_shutdown_resolves_its_ack(i):
    _expect(_run_a_call_still_waiting_at_shutdown_resolves_its_ack, i, len(_NAMES_a_call_still_waiting_at_shutdown_resolves_its_ack))


_NAMES_a_confirmed_topic_shortly_after_a_call_result_still = ['first confirmed topic -> one entry',
 "the call result lands in the current topic's entry",
 'the second confirmed topic got its own entry despite the stage brake',
 'no history entry is a call result',
 'one consumer log line per confirmed topic change',
 'the coach attaches to the real (current) topic entry']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_confirmed_topic_shortly_after_a_call_result_still))
def test_a_confirmed_topic_shortly_after_a_call_result_still(i):
    _expect(_run_a_confirmed_topic_shortly_after_a_call_result_still, i, len(_NAMES_a_confirmed_topic_shortly_after_a_call_result_still))


_NAMES_the_call_lane_s_stable_prefix_names_the_project = ['call prompt carries Projektordner',
 'call prompt carries Protokoll',
 'call prompt carries the read-only-targeted line',
 'coach prompt carries no paths',
 'call prefix is byte-stable across turns',
 'the paths survive the enrolled-names prefix update',
 'COTHINKER_EXTRA_DIRS: one read-only line per dir plus the rule once',
 'a symlink reference folder lists its files (README first, dead links '
 'skipped) so read_file can reach them',
 'a plain reference folder lists nothing extra',
 'COTHINKER_EXTRA_DIRS empty: text unchanged',
 'without call_paths the prefix is unchanged (old behaviour)']


@pytest.mark.parametrize("i", _case_ids(_NAMES_the_call_lane_s_stable_prefix_names_the_project))
def test_the_call_lane_s_stable_prefix_names_the_project(i):
    _expect(_run_the_call_lane_s_stable_prefix_names_the_project, i, len(_NAMES_the_call_lane_s_stable_prefix_names_the_project))


_NAMES_across_multiple_window_rolls_no_stale_utterance_leaks_into = ['6 calls queued exactly 6 prompts',
 'at least one roll actually happened by the last call',
 'an early marker, long folded past the excerpt length, is gone from the '
 'latest prompt entirely -- not even inside the Stand summary',
 'a second early marker is gone too',
 "the current call's own marker is present -- it is this turn's request",
 "stats() reports a cache read ratio per lane (point: 'report cache read "
 "ratio per lane')"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_across_multiple_window_rolls_no_stale_utterance_leaks_into))
def test_across_multiple_window_rolls_no_stale_utterance_leaks_into(i):
    _expect(_run_across_multiple_window_rolls_no_stale_utterance_leaks_into, i, len(_NAMES_across_multiple_window_rolls_no_stale_utterance_leaks_into))


_NAMES_a_window_roll_is_logged_for_the_lane_it = ['at least one roll was logged for the call lane']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_window_roll_is_logged_for_the_lane_it))
def test_a_window_roll_is_logged_for_the_lane_it(i):
    _expect(_run_a_window_roll_is_logged_for_the_lane_it, i, len(_NAMES_a_window_roll_is_logged_for_the_lane_it))


_NAMES_a_call_answer_that_is_the_literal_word_none = ['the call lane finished',
 "the literal word 'none' never reaches the mirror as Robo's answer",
 'the rejection is logged']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_answer_that_is_the_literal_word_none))
def test_a_call_answer_that_is_the_literal_word_none(i):
    _expect(_run_a_call_answer_that_is_the_literal_word_none, i, len(_NAMES_a_call_answer_that_is_the_literal_word_none))


_NAMES_a_call_answer_that_reports_its_own_status_instead = ['the second call lane finished',
 "a self-status report never reaches the mirror as Robo's answer"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_answer_that_reports_its_own_status_instead))
def test_a_call_answer_that_reports_its_own_status_instead(i):
    _expect(_run_a_call_answer_that_reports_its_own_status_instead, i, len(_NAMES_a_call_answer_that_reports_its_own_status_instead))


_NAMES_a_call_answer_on_the_mirror_fades_back_to = ['the call lane finished',
 'the real answer replaced the ack before the expiry window',
 'the mirror fades back to empty after the expiry window']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_answer_on_the_mirror_fades_back_to))
def test_a_call_answer_on_the_mirror_fades_back_to(i):
    _expect(_run_a_call_answer_on_the_mirror_fades_back_to, i, len(_NAMES_a_call_answer_on_the_mirror_fades_back_to))


_NAMES_mirror_tick = ['the call lane finished',
 'a line written by someone else meanwhile is left untouched']


@pytest.mark.parametrize("i", _case_ids(_NAMES_mirror_tick))
def test_mirror_tick(i):
    _expect(_run_mirror_tick, i, len(_NAMES_mirror_tick))


_NAMES_a_call_answer_also_lands_on_call_answer_independent = ['the call lane finished',
 'call_answer carries the real answer, same text as the mirror',
 'a topic change does not clear call_answer',
 "call_answer survives the mirror's own, shorter expiry",
 'call_answer fades back to empty only after its own, longer window']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_call_answer_also_lands_on_call_answer_independent))
def test_a_call_answer_also_lands_on_call_answer_independent(i):
    _expect(_run_a_call_answer_also_lands_on_call_answer_independent, i, len(_NAMES_a_call_answer_also_lands_on_call_answer_independent))


_NAMES_an_answer_rule_rides_on_every_call_prompt_and = ['call prompt carries the answer rule (result only, no report)',
 'a structure call gets the topics + sub-steps shape',
 'a plain question gets the answer rule but no structure shape']


@pytest.mark.parametrize("i", _case_ids(_NAMES_an_answer_rule_rides_on_every_call_prompt_and))
def test_an_answer_rule_rides_on_every_call_prompt_and(i):
    _expect(_run_an_answer_rule_rides_on_every_call_prompt_and, i, len(_NAMES_an_answer_rule_rides_on_every_call_prompt_and))


_NAMES_a_confirmed_topic_sets_the_headline_and_opens_the = ["headline = the confirmed topic's title",
 'protocol topic opened once, stamped 12:31 (block time)',
 'a confirmed return opens no protocol block']


@pytest.mark.xfail(reason='the lane integration here does not set the stage headline nor open a protocol topic block on a confirmed topic; characterization finding', strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_a_confirmed_topic_sets_the_headline_and_opens_the))
def test_a_confirmed_topic_sets_the_headline_and_opens_the(i):
    _expect(_run_a_confirmed_topic_sets_the_headline_and_opens_the, i, len(_NAMES_a_confirmed_topic_sets_the_headline_and_opens_the))


_NAMES_with_a_titler_wired_the_confirmed_topic_does_not = ['titler wired: headline left to the titler',
 'titler wired: retitle of the current entry sets the headline']


@pytest.mark.parametrize("i", _case_ids(_NAMES_with_a_titler_wired_the_confirmed_topic_does_not))
def test_with_a_titler_wired_the_confirmed_topic_does_not(i):
    _expect(_run_with_a_titler_wired_the_confirmed_topic_does_not, i, len(_NAMES_with_a_titler_wired_the_confirmed_topic_does_not))


_NAMES_a_snapshot_without_at_still_opens_the_block = ['stamp None when the snapshot has no time']


@pytest.mark.xfail(reason='the lane integration here does not open a protocol topic block; characterization finding', strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_a_snapshot_without_at_still_opens_the_block))
def test_a_snapshot_without_at_still_opens_the_block(i):
    _expect(_run_a_snapshot_without_at_still_opens_the_block, i, len(_NAMES_a_snapshot_without_at_still_opens_the_block))


_NAMES_zuruf_lane_pending_at_once_a_named_project_file = ["handle_block returns well under the worker's turn (no model on this "
 'path)',
 'kosten.md is on the stage immediately',
 'never the protocol file',
 'one pending of kind main with the file in its label',
 "the worker's answer lands, the pending ends",
 "the refinement is appended to the FILE's entry",
 'still the same entry on display']


@pytest.mark.xfail(reason="the file-shortcut feature (project file index, module file_match) is not part of this tree's gateway; characterization finding", strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_zuruf_lane_pending_at_once_a_named_project_file))
def test_zuruf_lane_pending_at_once_a_named_project_file(i):
    _expect(_run_zuruf_lane_pending_at_once_a_named_project_file, i, len(_NAMES_zuruf_lane_pending_at_once_a_named_project_file))


_NAMES_the_worker_s_answer_goes_to_the_file_s = ['a later topic takes the stage',
 "the answer still lands in the file's entry, not the new main"]


@pytest.mark.xfail(reason="the file-shortcut feature (project file index, module file_match) is not part of this tree's gateway; characterization finding", strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_the_worker_s_answer_goes_to_the_file_s))
def test_the_worker_s_answer_goes_to_the_file_s(i):
    _expect(_run_the_worker_s_answer_goes_to_the_file_s, i, len(_NAMES_the_worker_s_answer_goes_to_the_file_s))


_NAMES_no_file_hit_pending_with_the_intent_label_no = ["pending 'Zuruf: Recherche' at once",
 'main untouched',
 'pending settled after the answer']


@pytest.mark.xfail(reason="the file-shortcut feature (project file index, module file_match) is not part of this tree's gateway; characterization finding", strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_no_file_hit_pending_with_the_intent_label_no))
def test_no_file_hit_pending_with_the_intent_label_no(i):
    _expect(_run_no_file_hit_pending_with_the_intent_label_no, i, len(_NAMES_no_file_hit_pending_with_the_intent_label_no))


_NAMES_the_call_prompt_tells_the_worker_the_file_is = ['CALL_FILE_SHOWN names the file']


@pytest.mark.xfail(reason="the file-shortcut feature (project file index, module file_match) is not part of this tree's gateway; characterization finding", strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_the_call_prompt_tells_the_worker_the_file_is))
def test_the_call_prompt_tells_the_worker_the_file_is(i):
    _expect(_run_the_call_prompt_tells_the_worker_the_file_is, i, len(_NAMES_the_call_prompt_tells_the_worker_the_file_is))


_NAMES_a_failing_worker_settles_the_pending_as_failed = ['pending ends after the failure']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_failing_worker_settles_the_pending_as_failed))
def test_a_failing_worker_settles_the_pending_as_failed(i):
    _expect(_run_a_failing_worker_settles_the_pending_as_failed, i, len(_NAMES_a_failing_worker_settles_the_pending_as_failed))


_NAMES_a_dropped_call_job_settles_its_pending = ['dropped -> pending gone']


@pytest.mark.xfail(reason='the lane integration here does not settle stage pending entries (no pending/announce handling); characterization finding', strict=False)
@pytest.mark.parametrize("i", _case_ids(_NAMES_a_dropped_call_job_settles_its_pending))
def test_a_dropped_call_job_settles_its_pending(i):
    _expect(_run_a_dropped_call_job_settles_its_pending, i, len(_NAMES_a_dropped_call_job_settles_its_pending))


SECTIONS = [_run_a_wake_word_block_gets_an_instant_ack_then, _run_a_non_call_block_never_triggers_the_call_lane, _run_coach_fires_once_per_confirmed_topic_no_earlier_than, _run_a_topic_change_while_the_coach_works_files_the, _run_the_nachtrag_notice_never_clobbers_an_unrelated_warning_already, _run_update_stable_prefix, _run_robo_s_own_output_is_broadcast_into_both_lanes, _run_a_call_turn_that_outlives_its_timeout_aborts_visibly, _run_four_back_to_back_calls_every_one_answered_none, _run_a_merged_call_job_s_prompt_lists_every_pending, _run_a_failed_call_turns_its_ack_into_a_visible, _run_a_call_still_waiting_at_shutdown_resolves_its_ack, _run_a_confirmed_topic_shortly_after_a_call_result_still, _run_the_call_lane_s_stable_prefix_names_the_project, _run_across_multiple_window_rolls_no_stale_utterance_leaks_into, _run_a_window_roll_is_logged_for_the_lane_it, _run_a_call_answer_that_is_the_literal_word_none, _run_a_call_answer_that_reports_its_own_status_instead, _run_a_call_answer_on_the_mirror_fades_back_to, _run_mirror_tick, _run_a_call_answer_also_lands_on_call_answer_independent, _run_an_answer_rule_rides_on_every_call_prompt_and, _run_a_confirmed_topic_sets_the_headline_and_opens_the, _run_with_a_titler_wired_the_confirmed_topic_does_not, _run_a_snapshot_without_at_still_opens_the_block, _run_zuruf_lane_pending_at_once_a_named_project_file, _run_the_worker_s_answer_goes_to_the_file_s, _run_no_file_hit_pending_with_the_intent_label_no, _run_the_call_prompt_tells_the_worker_the_file_is, _run_a_failing_worker_settles_the_pending_as_failed, _run_a_dropped_call_job_settles_its_pending]
