#!/usr/bin/env python3
# CoThinker - the agenda + voice-command model: characterization tests
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The agenda + voice-command model (gateway/stage_state.py), and its wiring
into the consumer's SSE loop (gateway/consumer.py).

python3 -m pytest -q tests/test_agenda.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time as _zeit
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import consumer as k
from gateway import stage_state as ss

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "stage"))
import stage as st  # noqa: E402


# ============================================================ parse_agenda
@pytest.fixture
def parse_agenda_scenario():
    doc = ss.parse_agenda(
        "- [ ] Begruessung\n"
        "- [ ] Budget (10 min)\n"
        "- [x] Verabschiedung (5 min)\n"
        "Dauer: 90 min\n")
    doc2 = ss.parse_agenda("- [ ] Eins\nEnde: 21:30\n")
    doc3 = ss.parse_agenda("")
    return SimpleNamespace(**locals())


def test_three_items_parsed(parse_agenda_scenario):
    assert len(parse_agenda_scenario.doc.items) == 3


def test_first_item_text_no_budget(parse_agenda_scenario):
    item = parse_agenda_scenario.doc.items[0]
    assert item.text == "Begruessung" and item.budget_s is None


def test_second_item_budget_in_seconds(parse_agenda_scenario):
    assert parse_agenda_scenario.doc.items[1].budget_s == 600


def test_second_item_text_has_the_budget_stripped(parse_agenda_scenario):
    assert parse_agenda_scenario.doc.items[1].text == "Budget"


def test_third_item_is_already_ticked(parse_agenda_scenario):
    assert parse_agenda_scenario.doc.items[2].done is True


def test_first_two_items_are_not_ticked(parse_agenda_scenario):
    doc = parse_agenda_scenario.doc
    assert doc.items[0].done is False and doc.items[1].done is False


def test_dauer_header_parsed(parse_agenda_scenario):
    assert parse_agenda_scenario.doc.duration_min == 90


def test_no_ende_header_given(parse_agenda_scenario):
    assert parse_agenda_scenario.doc.end_time is None


def test_ende_header_parsed(parse_agenda_scenario):
    assert parse_agenda_scenario.doc2.end_time == (21, 30)


def test_empty_text_no_items_never_raises(parse_agenda_scenario):
    assert parse_agenda_scenario.doc3.items == []


# ======================================================== detect_agenda_command
@pytest.mark.parametrize("text", [
    "nächster Punkt", "naechster punkt", "nachster Punkt",
    "Okay, nächster Punkt", "nächste Frage bitte", "Robo, naechste Frage"])
def test_item_command(text):
    assert ss.detect_agenda_command(text) == "item"


@pytest.mark.parametrize("text", [
    "nächste Person", "naechste person bitte", "du bist dran",
    "wer ist als naechstes dran, naechster Sprecher"])
def test_person_command(text):
    assert ss.detect_agenda_command(text) == "person"


@pytest.mark.parametrize("text", [
    "Ich finde das Budget zu klein.", "", "Lasst uns weitermachen"])
def test_not_a_command(text):
    assert ss.detect_agenda_command(text) is None


# =================================================================== meeting_deadline
def test_deadline_from_dauer():
    doc_dauer = ss.parse_agenda("- [ ] A\nDauer: 30 min\n")
    assert ss.meeting_deadline(doc_dauer, 1000.0) == 1000.0 + 1800


def test_no_header_no_deadline():
    doc_none = ss.parse_agenda("- [ ] A\n")
    assert ss.meeting_deadline(doc_none, 1000.0) is None


# ====================================================== StageState.agenda
AGENDA_FIELD = {"current": 1, "current_text": "A", "next": 2, "next_text": "B",
                "total": 2, "item_started_at": 0.0, "item_budget_s": None,
                "meeting_deadline": None, "people": ["Anna", "Ben", "Carla"],
                "speaker": "Anna", "next_speaker": "Ben"}


@pytest.fixture
def stage_state_agenda_field_scenario():
    s = ss.StageState()
    content_without = s.content()
    agenda_none_without_one = content_without.get("agenda") is None
    every_other_field_exists = set(content_without) >= {
        "version", "mirror", "headline", "main", "history", "decisions",
        "notices", "question", "links", "pending"}
    s.agenda = dict(AGENDA_FIELD)
    agenda_rides_along_once_set = s.content()["agenda"]
    return SimpleNamespace(**locals())


def test_agenda_is_none_without_one(stage_state_agenda_field_scenario):
    assert stage_state_agenda_field_scenario.agenda_none_without_one


def test_every_other_field_of_a_plain_stage_state_still_exists(stage_state_agenda_field_scenario):
    assert stage_state_agenda_field_scenario.every_other_field_exists


def test_agenda_rides_along_once_set(stage_state_agenda_field_scenario):
    assert (stage_state_agenda_field_scenario.agenda_rides_along_once_set
            == AGENDA_FIELD)


# ================================================= stage.py: the agenda zone
def _set_stage(stage_tmp: Path, agenda) -> None:
    (stage_tmp / "buehne.json").write_text(json.dumps({
        "version": 2, "updated": "2026-10-21T19:00:00",
        "status": {"state": "listening", "text": "", "ticking": False,
                   "since": ""},
        "mirror": "", "headline": "", "main": {"id": "M1", "markdown": "",
                                               "set_at": "2026-10-21T19:00:00",
                                               "collapsed_lines": 0,
                                               "collapsed_markdown": ""},
        "decisions": [], "notices": [], "question": None, "links": [],
        "parked": 0, "parked_entries": [], "age_visible_after_s": 20,
        "follow_up_after_s": 120, "agenda": agenda,
    }, ensure_ascii=False), encoding="utf-8")
    st.clear_cache()


@pytest.fixture
def stage_agenda_zone_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "PROJECT", tmp_path)
    _set_stage(tmp_path, None)
    t_no_agenda = st.stage_parts()
    no_agenda_md_no_zone = t_no_agenda.get("agenda") is None

    _set_stage(tmp_path, {"current": 1, "current_text": "Begruessung", "next": 2,
                          "next_text": "Budget", "total": 3, "item_started_at": 1000.0,
                          "item_budget_s": 600, "meeting_deadline": 9000.0,
                          "people": ["Anna", "Ben"], "speaker": "Anna",
                          "next_speaker": "Ben"})
    t_agenda = st.stage_parts()
    a = t_agenda.get("agenda")
    rides_along_as_dict = isinstance(a, dict)
    current_next_total_pass_through = a and (a["current"], a["next"], a["total"]) == (1, 2, 3)
    clock_bearing_fields_pass_through = (
        a and a["item_started_at"] == 1000.0 and a["item_budget_s"] == 600
        and a["meeting_deadline"] == 9000.0)
    speaker_queue_passes_through = a and a["speaker"] == "Anna" and a["next_speaker"] == "Ben"
    return SimpleNamespace(**locals())


def test_no_agenda_md_stage_parts_carries_no_agenda_zone(stage_agenda_zone_scenario):
    assert stage_agenda_zone_scenario.no_agenda_md_no_zone


def test_agenda_rides_along_as_a_dict_not_html(stage_agenda_zone_scenario):
    assert stage_agenda_zone_scenario.rides_along_as_dict


def test_current_next_total_pass_through(stage_agenda_zone_scenario):
    assert stage_agenda_zone_scenario.current_next_total_pass_through


def test_the_clock_bearing_fields_pass_through_as_numbers(stage_agenda_zone_scenario):
    assert stage_agenda_zone_scenario.clock_bearing_fields_pass_through


def test_the_speaker_queue_passes_through(stage_agenda_zone_scenario):
    assert stage_agenda_zone_scenario.speaker_queue_passes_through


# Malformed/foreign input must never take the page down (same posture as
# every other field: the agent is a language model, not a schema).
@pytest.mark.parametrize("bad", [
    5, "nope", ["liste"], {"current": "viel", "total": "auch"},
    {"people": "keine Liste", "total": 1, "current": 1}])
def test_agenda_part_holds_up_on_malformed_input(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(st, "PROJECT", tmp_path)
    _set_stage(tmp_path, bad)
    tb = st.stage_parts()
    assert isinstance(tb, dict)


@pytest.fixture
def stage_agenda_fingerprint_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "PROJECT", tmp_path)
    _set_stage(tmp_path, {"current": 1, "current_text": "Eins", "next": None,
                          "next_text": None, "total": 1, "item_started_at": 1.0,
                          "item_budget_s": None, "meeting_deadline": None,
                          "people": [], "speaker": None, "next_speaker": None})
    fp1 = st.fingerprint(st.stage_parts())
    _set_stage(tmp_path, {"current": 1, "current_text": "Eins", "next": None,
                          "next_text": None, "total": 1, "item_started_at": 999.0,
                          "item_budget_s": None, "meeting_deadline": None,
                          "people": [], "speaker": None, "next_speaker": None})
    fp2 = st.fingerprint(st.stage_parts())
    return SimpleNamespace(**locals())


def test_the_agenda_zone_is_excluded_from_the_fingerprint(stage_agenda_fingerprint_scenario):
    assert stage_agenda_fingerprint_scenario.fp1 == stage_agenda_fingerprint_scenario.fp2


# ================================================= AgendaController: feeds
# the timer and speaker-queue blocks from agenda.md
def _wired(session: Path, agenda_text: str, clockval, people=()):
    """A controller on `session/agenda.md` with a real session block host."""
    from gateway.block_host_wiring import build_session_blocks
    session.mkdir(exist_ok=True)
    agenda_path = session / "agenda.md"
    agenda_path.write_text(agenda_text, encoding="utf-8")
    ctrl = k.AgendaController(agenda_path, session, people=list(people),
                              now=lambda: clockval[0])
    blocks = build_session_blocks(
        session_dir=session, sink=ctrl.observe, log=lambda *m: None,
        clock=lambda: clockval[0], block_configs=ctrl.block_configs())
    ctrl.attach(blocks)
    return ctrl, agenda_path


@pytest.fixture
def agenda_controller_scenario(tmp_path):
    session = tmp_path / "session"
    clockval = [1000.0]
    ctrl, agenda_path = _wired(
        session, "- [ ] Eins\n- [ ] Zwei (10 min)\n- [ ] Drei\n", clockval)
    pl = ctrl.payload()
    fresh_controller_current_1 = pl["current"] == 1
    fresh_controller_next_2 = pl["next"] == 2
    initial_payload_without_a_move = pl["item_started_at"] == 1000.0
    first_item_has_no_budget = pl["item_budget_s"] is None

    handled = ctrl.handle_utterance("nicht relevanter Satz ueber das Budget")
    ordinary_speech_not_a_command = handled is False

    clockval[0] = 1050.0
    handled2 = ctrl.handle_utterance("Okay, nächster Punkt")
    command_recognized_and_swallowed = handled2 is True
    item_1_ticked_in_agenda_md = "- [x] Eins" in agenda_path.read_text(encoding="utf-8")
    pl2 = ctrl.payload()
    current_advances_to_2 = pl2["current"] == 2
    item_timer_resets_to_command_moment = pl2["item_started_at"] == 1050.0
    new_item_carries_its_budget = pl2["item_budget_s"] == 600

    handled3 = ctrl.handle_utterance("nächste Person")
    person_command_also_swallowed = handled3 is True

    ctrl_restarted, _ = _wired(
        session, agenda_path.read_text(encoding="utf-8"), [2000.0])
    pl3 = ctrl_restarted.payload()
    restart_keeps_current_from_block_state = pl3["current"] == 2
    restart_keeps_item_start = pl3["item_started_at"] == 1050.0

    no_agenda_dir = tmp_path / "no-agenda-session"
    no_agenda_dir.mkdir()
    check_no_agenda = k.build_agenda_controller(no_agenda_dir, "")
    no_agenda_md_no_controller_no_exception = check_no_agenda is None
    return SimpleNamespace(**locals())


def test_fresh_controller_current_1(agenda_controller_scenario):
    assert agenda_controller_scenario.fresh_controller_current_1


def test_fresh_controller_next_2(agenda_controller_scenario):
    assert agenda_controller_scenario.fresh_controller_next_2


def test_the_initial_payload_exists_before_any_move(agenda_controller_scenario):
    assert agenda_controller_scenario.initial_payload_without_a_move


def test_an_item_without_minutes_has_no_budget(agenda_controller_scenario):
    assert agenda_controller_scenario.first_item_has_no_budget


def test_ordinary_speech_is_not_a_command(agenda_controller_scenario):
    assert agenda_controller_scenario.ordinary_speech_not_a_command


def test_the_command_is_recognized_and_swallowed(agenda_controller_scenario):
    assert agenda_controller_scenario.command_recognized_and_swallowed


def test_item_1_is_ticked_in_agenda_md(agenda_controller_scenario):
    assert agenda_controller_scenario.item_1_ticked_in_agenda_md


def test_current_advances_to_2(agenda_controller_scenario):
    assert agenda_controller_scenario.current_advances_to_2


def test_the_item_timer_resets_to_the_moment_of_the_command(agenda_controller_scenario):
    assert agenda_controller_scenario.item_timer_resets_to_command_moment


def test_the_new_current_item_carries_its_own_budget(agenda_controller_scenario):
    assert agenda_controller_scenario.new_item_carries_its_budget


def test_a_person_command_is_also_swallowed(agenda_controller_scenario):
    assert agenda_controller_scenario.person_command_also_swallowed


def test_a_restart_keeps_the_current_item_from_the_block_state(agenda_controller_scenario):
    assert agenda_controller_scenario.restart_keeps_current_from_block_state


def test_a_restart_keeps_the_item_start_time(agenda_controller_scenario):
    assert agenda_controller_scenario.restart_keeps_item_start


def test_no_agenda_md_no_controller_no_exception(agenda_controller_scenario):
    assert agenda_controller_scenario.no_agenda_md_no_controller_no_exception


def test_last_item_has_no_next_item(tmp_path):
    ctrl, _ = _wired(tmp_path / "s", "- [ ] Nur eins\n", [0.0])
    pl = ctrl.payload()
    assert pl["next"] is None and pl["next_text"] is None and pl["total"] == 1


def test_the_round_rides_along_with_the_agenda(tmp_path):
    ctrl, _ = _wired(tmp_path / "s", "- [ ] A\n- [ ] B\n", [0.0],
                     people=["Anna", "Ben", "Carla"])
    pl = ctrl.payload()
    assert pl["people"] == ["Anna", "Ben", "Carla"]
    assert (pl["speaker"], pl["next_speaker"]) == ("Anna", "Ben")


def test_no_speaker_queue_no_speaker_never_raises(tmp_path):
    ctrl, _ = _wired(tmp_path / "s", "- [ ] A\n", [0.0])
    pl = ctrl.payload()
    assert pl["speaker"] is None and pl["people"] == []
    assert ctrl.handle_utterance("nächste Person") is True
    assert ctrl.payload()["current"] == 1


def test_person_command_moves_the_speaker_not_the_item(tmp_path):
    ctrl, _ = _wired(tmp_path / "s", "- [ ] A\n- [ ] B\n", [0.0],
                     people=["Pia", "Finn", "Anke"])
    ctrl.handle_utterance("nächste Person")
    pl = ctrl.payload()
    assert pl["speaker"] == "Finn" and pl["current"] == 1


def test_round_wraparound_advances_the_item_and_clamps_at_the_last(tmp_path):
    ctrl, agenda_path = _wired(tmp_path / "s", "- [ ] A\n- [ ] B\n", [0.0],
                               people=["A1", "B1"])
    ctrl.handle_utterance("nächste Person")
    assert ctrl.payload()["current"] == 1
    ctrl.handle_utterance("nächste Person")          # wraps
    pl = ctrl.payload()
    assert pl["current"] == 2 and pl["speaker"] == "A1"
    assert "- [x] A" in agenda_path.read_text(encoding="utf-8")
    ctrl.handle_utterance("nächste Person")
    ctrl.handle_utterance("nächste Person")          # wraps again, clamped
    assert ctrl.payload()["current"] == 2


def test_dauer_deadline_survives_a_restart(tmp_path):
    text = "- [ ] A\nDauer: 30 min\n"
    ctrl, _ = _wired(tmp_path / "s", text, [1000.0])
    ctrl2, _ = _wired(tmp_path / "s", text, [5000.0])
    assert ctrl.payload()["meeting_deadline"] == 1000.0 + 1800
    assert ctrl2.payload()["meeting_deadline"] == 1000.0 + 1800


# ================================================== full consumer, end to end
WORKER_MARKER_SRC = """
import pathlib, sys
pathlib.Path(sys.argv[sys.argv.index('--marker') + 1]).touch()
print('{"type": "ready", "session_id": "x"}', flush=True)
for line in sys.stdin:
    pass
"""


def _stt_server(satz: str):
    """A fake STT server with exactly one utterance, then keep-alive."""

    class _STT(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path != "/events":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(
                    f"data: {json.dumps({'type': 'final', 'text': satz})}\n\n"
                    .encode())
                self.wfile.flush()
                while True:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    _zeit.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _STT)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.fixture(scope="module")
def voice_command_end_to_end_scenario(tmp_path_factory):
    """acceptance-scenario: agenda-timer-voice-next

    A real `gateway/consumer.py` process, a fake STT stream carrying
    exactly one utterance ("Okay, naechster Punkt") and a worker stub
    that marks a file the INSTANT it is launched (gateway/worker.py
    starts the agent process lazily, only on the first turn -- see
    WorkerClient.start()). If the command leaked into the block builder,
    a block would be cut on the pause below and the worker would start;
    this proves it never does, while also proving the command was
    applied (agenda.md ticked, the stage payload advanced).
    """
    tmp = tmp_path_factory.mktemp("cothinker-agenda-live")
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=tmp, capture_output=True)
    projekt = tmp / "entities" / "testentity" / "projekte" / "testprojekt"
    projekt.mkdir(parents=True)

    heute = date.today().isoformat()
    lauf = projekt / "sessions" / f"{heute}-testprojekt"
    lauf.mkdir(parents=True)
    (lauf / "agenda.md").write_text(
        "- [ ] Eins\n- [ ] Zwei\n- [ ] Drei\n", encoding="utf-8")

    workerskript = tmp / "worker.py"
    workerskript.write_text(WORKER_MARKER_SRC, encoding="utf-8")
    marker = tmp / "worker-started.marker"

    srv, port = _stt_server("Okay, nächster Punkt")
    buehne = lauf / "buehne.json"

    p = subprocess.Popen(
        [sys.executable, str(REPO / "gateway" / "consumer.py"),
         "--stt", f"http://127.0.0.1:{port}", "--project", "testprojekt",
         "--pause", "0.3", "--retry-delay", "0.2", "--commit-every", "100000",
         "--check-every", "0", "--worker-command",
         f'"{sys.executable}" "{workerskript}" --marker "{marker}"'],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
        env=dict(os.environ, COTHINKER_ENTITIES=str(tmp / "entities"),
                 PYTHONUNBUFFERED="1"))

    stand: dict = {}
    frist = _zeit.monotonic() + 30
    while _zeit.monotonic() < frist:
        if buehne.exists():
            try:
                stand = json.loads(buehne.read_text(encoding="utf-8"))
            except ValueError:
                stand = {}
            if isinstance(stand.get("agenda"), dict) and stand["agenda"].get(
                    "current") == 2:
                break
        if p.poll() is not None:
            break
        _zeit.sleep(0.1)
    # Past the point where the agenda advanced, give a slow worker start
    # (or a mistaken block) a further moment to show up on disk.
    _zeit.sleep(1.0)

    p.send_signal(signal.SIGINT)
    try:
        ausgabe = p.communicate(timeout=30)[0]
        timed_out = False
    except subprocess.TimeoutExpired:
        p.kill()
        ausgabe = p.communicate()[0]
        timed_out = True
    srv.shutdown()

    return SimpleNamespace(stand=stand, lauf=lauf, marker=marker, p=p,
                          ausgabe=ausgabe, timed_out=timed_out)


def test_the_stage_carries_the_agenda_zone(voice_command_end_to_end_scenario):
    assert isinstance(voice_command_end_to_end_scenario.stand.get("agenda"), dict)


def test_the_command_advanced_the_current_item_to_2(voice_command_end_to_end_scenario):
    agenda = voice_command_end_to_end_scenario.stand.get("agenda") or {}
    assert agenda.get("current") == 2


def test_the_command_ticked_the_first_item_in_agenda_md(voice_command_end_to_end_scenario):
    lauf = voice_command_end_to_end_scenario.lauf
    assert "- [x] Eins" in (lauf / "agenda.md").read_text(encoding="utf-8")


def test_the_worker_was_never_started_for_this_utterance(voice_command_end_to_end_scenario):
    assert not voice_command_end_to_end_scenario.marker.exists()


def test_consumer_exits_on_ctrl_c(voice_command_end_to_end_scenario):
    scenario = voice_command_end_to_end_scenario
    assert not scenario.timed_out, "timeout"
    assert scenario.p.returncode == 0, (
        f"return value {scenario.p.returncode}: {scenario.ausgabe[-800:]}")


def test_agenda_payload_carries_the_whole_list(agenda_controller_scenario):
    """Found live: the stage/monitor showed only current + next because the
    core never filled `items` (issue #129). Every item, with its status."""
    s = agenda_controller_scenario
    assert [(i["title"], i["status"]) for i in s.pl2["items"]] == [
        ("Eins", "done"), ("Zwei", "current"), ("Drei", "next")]
    assert s.pl2["items"][1]["budget_s"] == 600
