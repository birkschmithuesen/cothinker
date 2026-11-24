# CoThinker — tests of the structural event trace
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The structural event trace. Never conversation text.

Three levels, because the guarantee has to hold at all three:

  1. the module itself (allow-list, hash, a broken path),
  2. the call sites in the consumer that make the five quantities of
     docs/stage-methodology.md section 7 computable,
  3. a real consumer process against a fake STT server — the only place
     that can show that nothing anybody SAID ends up in the file.

    python3 -m pytest -q tests/test_events.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))
sys.path.insert(0, str(REPO / "stage"))
from shared.events import EventLog  # noqa: E402


class Speaks:
    """An object whose `str()` is speech — what `default=str` used to write."""

    def __str__(self) -> str:
        return "Die Frist ist der 6. Oktober"


def rows(path: Path) -> list[dict]:
    """Every line of a trace file, parsed. Also proves it stays parseable."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ================================================================ the module
@pytest.fixture(scope="module")
def trace_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("trace")


@pytest.fixture(scope="module")
def log(trace_dir):
    return EventLog(trace_dir / "stage-events.jsonl")


@pytest.fixture(scope="module")
def trace_file(trace_dir):
    return trace_dir / "stage-events.jsonl"


def lines_of(trace_file: Path) -> list[str]:
    return trace_file.read_text(encoding="utf-8").splitlines()


@pytest.fixture(scope="module")
def two_events(log, trace_file):
    log.record("notice_raised", id="N1", kind="value_conflict")
    log.record("notice_resolved", id="N1", age_s=42.5)
    return lines_of(trace_file)


def test_two_lines(two_events):
    assert len(two_events) == 2


def test_carries_the_event_name(two_events):
    assert json.loads(two_events[0])["event"] == "notice_raised"


def test_carries_the_identity(two_events):
    assert json.loads(two_events[0])["id"] == "N1"


def test_carries_a_timestamp(two_events):
    first = json.loads(two_events[0])
    assert "at" in first and "T" in first["at"]


def test_the_duration_is_carried(two_events):
    assert json.loads(two_events[1])["age_s"] == 42.5


def test_the_timestamp_carries_its_utc_offset(two_events):
    first = json.loads(two_events[0])
    assert datetime.fromisoformat(first["at"]).utcoffset() is not None


# ------------------------------------------- free text never reaches the trace
@pytest.fixture(scope="module")
def after_free_text(two_events, log, trace_file):
    log.record("notice_raised", id="N2", text="Die Frist ist der 6. Oktober")
    return json.loads(lines_of(trace_file)[-1])


def test_the_text_field_was_dropped(after_free_text):
    assert "text" not in after_free_text


def test_nothing_of_the_sentence_survived(after_free_text):
    assert "Oktober" not in json.dumps(after_free_text, ensure_ascii=False)


# ------------------------------------- the VALUES are constrained, not the keys
def last_event(trace_file: Path) -> dict:
    return json.loads(lines_of(trace_file)[-1])


def test_an_unknown_key_is_dropped(after_free_text, log, trace_file):
    log.record("notice_raised", id="N3", sprecher="Max Mustermann")
    assert "sprecher" not in last_event(trace_file)


def test_the_known_key_beside_it_survives(after_free_text, log, trace_file):
    log.record("notice_raised", id="N3", sprecher="Max Mustermann")
    assert last_event(trace_file)["id"] == "N3"


def test_an_object_is_dropped_not_stringified(log, trace_file):
    log.record("notice_raised", id=Speaks(), reason=Speaks(), kind=Speaks())
    last = last_event(trace_file)
    assert not any(k in last for k in ("id", "reason", "kind"))


def test_nothing_of_what_it_would_have_said_is_in_the_file(log, trace_file):
    log.record("notice_raised", id=Speaks(), reason=Speaks(), kind=Speaks())
    assert "Oktober" not in json.dumps(last_event(trace_file),
                                       ensure_ascii=False)


def test_a_sentence_in_a_token_field_is_dropped(log, trace_file):
    log.record("proposal_rejected",
               reason="Wir haben die Frist auf den 6. gelegt")
    assert "reason" not in last_event(trace_file)


def test_an_over_long_token_is_dropped(log, trace_file):
    log.record("notice_raised", id="N" * 40)
    assert "id" not in last_event(trace_file)


def test_a_token_with_a_space_is_dropped(log, trace_file):
    log.record("notice_raised", kind="wert konflikt")
    assert "kind" not in last_event(trace_file)


def test_a_numeric_field_refuses_text(log, trace_file):
    log.record("stage_written", chars="viele Zeichen")
    assert "chars" not in last_event(trace_file)


def test_numbers_and_booleans_are_kept(log, trace_file):
    log.record("run_finished", ok=True, duration_s=1.5, count=3)
    last = last_event(trace_file)
    assert last["ok"] is True and last["duration_s"] == 1.5 \
        and last["count"] == 3


def test_an_event_name_that_is_not_a_token_writes_no_line_at_all(
        log, trace_file):
    before = len(lines_of(trace_file))
    log.record("Robo, zeig uns bitte die vier Straenge", id="N1")
    log.record(Speaks(), id="N1")
    assert len(lines_of(trace_file)) == before


def test_every_one_of_these_attempts_left_a_parseable_file(log, trace_file):
    assert len(rows(trace_file)) == len(lines_of(trace_file)) > 2


# ------------------------------------------- the call-out quote is hashed
def test_no_quote_in_the_file(log, trace_file):
    log.record("call_answered", id="M1", quote="zeig uns die vier Stränge")
    assert "quote" not in last_event(trace_file)


def test_but_a_hash_is_there(log, trace_file):
    log.record("call_answered", id="M1", quote="zeig uns die vier Stränge")
    assert len(last_event(trace_file).get("quote_hash", "")) == 16


def test_the_same_quote_gives_the_same_hash(log, trace_file):
    log.record("call_answered", id="M1", quote="zeig uns die vier Stränge")
    assert last_event(trace_file)["quote_hash"] == EventLog.hash_quote(
        "zeig uns die vier Stränge")


# ----------------------------------------------------- hub field names
HUB_FIELDS = ("t_input", "t_emit", "t_audio_end", "lag_ms", "seq",
              "cause_type")


def test_hub_field_names_are_not_on_either_allow_list_and_are_dropped(
        log, trace_file):
    # Not relaxed here: a hub change wiring these names in must add them to
    # NUMERIC_FIELDS/TOKEN_FIELDS first.
    log.record("hub_turn", t_input=1.0, t_emit=2.5, t_audio_end=3.25,
               lag_ms=120, seq=7, cause_type="voice")
    assert not any(k in last_event(trace_file) for k in HUB_FIELDS)


def test_the_event_line_is_still_written_just_without_the_unknown_fields(
        log, trace_file):
    before = len(lines_of(trace_file))
    log.record("hub_turn", t_input=1.0, t_emit=2.5, t_audio_end=3.25,
               lag_ms=120, seq=7, cause_type="voice")
    assert len(lines_of(trace_file)) == before + 1
    assert last_event(trace_file)["event"] == "hub_turn"


# ------------------------------------------------------------ MM-B trace keys
def test_signal_mode_tokens_autonomous_blocks_numbers_kept(log, trace_file):
    log.record("policy_action", kind="tag_entry", signal="aufgabe",
               autonomous=True, mode="arbeit", blocks=2)
    last = last_event(trace_file)
    assert last.get("signal") == "aufgabe" and last.get("mode") == "arbeit" \
        and last.get("autonomous") is True and last.get("blocks") == 2


def test_a_sentence_in_signal_is_dropped(log, trace_file):
    log.record("policy_action", signal="ein ganzer Satz mit Leerzeichen")
    assert "signal" not in last_event(trace_file)


# ------------------------------------------- a broken path never takes it down
def test_writing_to_an_impossible_path_does_not_raise():
    bad = EventLog(Path("/does/not/exist/trace.jsonl"))
    bad.record("notice_raised", id="N1")


def test_an_unserialisable_value_does_not_raise(log):
    log.record("stage_written", chars=object(), id=Path("/tmp"))


# ------------------------------------------------------------------ record_all
def test_the_tick_event_was_written(log, trace_file):
    log.record_all([{"event": "notice_follow_up", "id": "N7", "age_s": 120.0},
                    {"id": "N8"},              # no event name: skipped
                    "kaputt"])                 # not a dict at all: skipped
    names = [r["event"] for r in rows(trace_file)]
    assert names[-1] == "notice_follow_up"


def test_nameless_and_broken_entries_are_skipped(log, trace_file):
    names = [r["event"] for r in rows(trace_file)]
    assert names.count("notice_follow_up") == 1


# ============================================================ consumer call sites
SPOKEN = "Hey Robo, zeig uns die vier Straenge des Antrags."


@pytest.fixture(scope="module")
def stage_run(tmp_path_factory):
    from gateway import consumer as k
    from gateway.stage_state import StageState

    proj = tmp_path_factory.mktemp("proj")
    trace = EventLog(proj / "stage-events.jsonl")
    clock = [0.0]
    state = StageState(now=lambda: clock[0])
    writer = k.StageWriter(proj, state, log=lambda *a: None, events=trace,
                           proposals=proj / "vorschlag-2026-09-08.jsonl")
    prop = proj / "vorschlag.jsonl"
    prop.write_text(
        '{"action":"decision","time":"19:43","text":"kein Pitch"}\n'
        '{"action":"notice","kind":"value_conflict",'
        '"text":"**Frist** 5. gegen 6. Oktober"}\n'
        '{"action":"question","text":"5. oder 6. Oktober?"}\n'
        '{"action":"main","quote":"zeig uns die vier Straenge",'
        '"markdown":"Ergebnis"}\n'
        '{"action":"mirror"}\n'                       # no text: rejected
        'kaputt\n', encoding="utf-8")
    # The call-out quote is checked against what was said in this run: an
    # unverifiable quote buys no main area any more.
    discarded = writer.ingest(prop, spoken=SPOKEN)
    by_name1 = {e["event"]: e for e in rows(proj / "stage-events.jsonl")}
    names1 = [e["event"] for e in rows(proj / "stage-events.jsonl")]

    with prop.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"action":"resolve","id":"N1","text":"6. Oktober gilt"}\n'
            '{"action":"resolve","id":"Q1","text":"der 6. Oktober"}\n'
            '{"action":"question","text":"Wer fragt bei der Kasse nach?"}\n'
            '{"action":"notice","kind":"value_conflict",'
            '"text":"**Summe** 12.000 gegen 14.000"}\n'
            '{"action":"notice","kind":"decision_breach",'
            '"text":"**Beschluss** kein Pitch, hier doch"}\n'
            # a third open notice: no slot left, so it is parked
            '{"action":"notice","kind":"value_conflict",'
            '"text":"**Termin** Montag gegen Dienstag"}\n'
            # three more decisions: the cap of three pushes D1 off the stage
            '{"action":"decision","time":"19:50","text":"Antrag wird gestellt"}\n'
            '{"action":"decision","time":"19:52","text":"Kasse wird gefragt"}\n'
            '{"action":"decision","time":"19:55","text":"Montag gilt"}\n')
    clock[0] = 30.0
    writer.ingest(prop, spoken=SPOKEN)
    events2 = rows(proj / "stage-events.jsonl")
    by_name2 = {e["event"]: e for e in events2}

    clock[0] = 80.0                        # the minimum hold time has passed
    with prop.open("a", encoding="utf-8") as fh:
        fh.write('{"action":"topic","title":"Rechtsform des Vereins",'
                 '"markdown":"## Rechtsform\\n- Verein oder gGmbH"}\n'
                 # straight after it a second one: the brake, not an error
                 '{"action":"topic","title":"Rechtsform des Vereins",'
                 '"markdown":"## Rechtsform"}\n')
    writer.ingest(prop, spoken=SPOKEN)
    by_name3 = {e["event"]: e for e in rows(proj / "stage-events.jsonl")}

    wrote = writer.write()
    names_after_write = {e["event"] for e in rows(proj / "stage-events.jsonl")}
    before = len(rows(proj / "stage-events.jsonl"))
    wrote_again = writer.write()
    after = len(rows(proj / "stage-events.jsonl"))

    clock[0] = 700.0                       # past follow-up AND question expiry
    trace.record_all(state.tick())
    names_end = [e["event"] for e in rows(proj / "stage-events.jsonl")]
    raw = (proj / "stage-events.jsonl").read_text(encoding="utf-8")
    return SimpleNamespace(
        proj=proj, discarded=discarded, by_name1=by_name1, names1=names1,
        events2=events2, by_name2=by_name2, by_name3=by_name3, wrote=wrote,
        names_after_write=names_after_write, wrote_again=wrote_again,
        unchanged_count=(before == after), names_end=names_end, raw=raw)


def test_the_read_of_the_proposal_file_is_recorded(stage_run):
    assert "proposals_read" in stage_run.names1


def test_with_the_count_and_the_discarded_lines(stage_run):
    ev = stage_run.by_name1["proposals_read"]
    assert ev["count"] == 5 and ev["discarded"] == 1 == stage_run.discarded


def test_the_appended_decision_is_recorded(stage_run):
    assert stage_run.by_name1["decision_added"]["id"] == "D1"


def test_the_notice_is_recorded_with_its_kind(stage_run):
    ev = stage_run.by_name1["notice_raised"]
    assert ev["id"] == "N1" and ev["kind"] == "value_conflict"


def test_the_question_is_recorded(stage_run):
    assert stage_run.by_name1["question_raised"]["id"] == "Q1"


def test_the_call_out_is_recorded_as_answered(stage_run):
    assert stage_run.by_name1["call_answered"]["id"] == "M1"


def test_with_the_instruction_hashed_never_stored(stage_run):
    assert stage_run.by_name1["call_answered"]["quote_hash"] \
        == EventLog.hash_quote("zeig uns die vier Straenge")


def test_a_rejected_proposal_is_recorded_with_its_reason(stage_run):
    assert stage_run.by_name1["proposal_rejected"]["reason"] == "no_text"


# ---------------------------------- resolution, decision cap, parked notice
def test_the_resolution_is_recorded_with_the_age(stage_run):
    ev = stage_run.by_name2["notice_resolved"]
    assert ev["id"] == "N1" and ev["age_s"] == 30.0


def test_a_resolved_question_is_told_apart_from_a_resolved_notice(stage_run):
    ev = stage_run.by_name2["question_resolved"]
    assert ev["id"] == "Q1" and ev["age_s"] == 30.0


def test_the_question_that_took_its_place_has_its_own_identity(stage_run):
    assert stage_run.by_name2["question_raised"]["id"] == "Q2"


def test_a_notice_with_no_free_slot_is_recorded_as_parked(stage_run):
    assert stage_run.by_name2["proposal_rejected"]["reason"] == "parked"


def test_the_decision_pushed_off_the_stage_is_recorded_and_it_is_the_oldest(
        stage_run):
    assert stage_run.by_name2["decision_dropped"]["id"] == "D1"


def test_exactly_one_decision_was_dropped_after_the_fourth_was_added(
        stage_run):
    names = [e["event"] for e in stage_run.events2]
    assert names.count("decision_dropped") == 1
    assert stage_run.by_name2["decision_added"]["id"] == "D4"


# ------------------------------------- the automatic topic change
def test_the_topic_change_is_recorded_with_the_surfaces_identity(stage_run):
    assert stage_run.by_name3["topic_changed"]["id"] == "M2"


def test_with_the_titles_length_and_no_field_that_could_hold_the_title(
        stage_run):
    # A topic title is a sentence out of the meeting. Its LENGTH is a
    # measurement, its text would be speech in a file that must never carry
    # any.
    ev = stage_run.by_name3["topic_changed"]
    assert ev["chars"] == len("Rechtsform des Vereins")
    assert not any(isinstance(v, str) and " " in v for v in ev.values())


def test_the_refused_second_change_is_recorded_as_a_token_reason(stage_run):
    assert stage_run.by_name3["proposal_rejected"]["reason"] == "too_soon"


# ------------------------------------------ expiry and the surface writes
def test_writing_the_stage_reports_a_change(stage_run):
    assert stage_run.wrote is True


def test_writing_the_stage_is_recorded(stage_run):
    assert "stage_written" in stage_run.names_after_write


def test_a_write_that_changes_nothing_records_nothing(stage_run):
    assert stage_run.wrote_again is False
    assert stage_run.unchanged_count


def test_the_follow_up_state_is_recorded(stage_run):
    assert "notice_follow_up" in stage_run.names_end \
        or "question_follow_up" in stage_run.names_end


def test_an_unanswered_question_is_recorded_as_expired(stage_run):
    assert "question_expired" in stage_run.names_end


def test_a_cleared_receipt_is_recorded(stage_run):
    assert "notice_cleared" in stage_run.names_end


def test_not_one_word_of_the_proposals_reached_the_trace(stage_run):
    assert not any(w in stage_run.raw for w in (
        "Pitch", "Oktober", "Straenge", "Frist", "Ergebnis", "Summe",
        "Kasse", "Montag", "Antrag", "Beschluss", "Termin", "Rechtsform",
        "Verein", "gGmbH"))


# ------------------------------------------- the stage never serves the trace
@pytest.fixture(scope="module")
def stage_view(stage_run):
    import stage as view

    old = view.PROJECT
    view.PROJECT = stage_run.proj
    (stage_run.proj / "protokoll-2026-09-08.md").write_text(
        "# x\n", encoding="utf-8")
    view.clear_cache()
    yield view
    view.PROJECT = old
    view.clear_cache()


def test_the_file_view_finds_the_protocol(stage_view):
    assert "protokoll-2026-09-08.md" in [p.name for p in stage_view.list_files()]


def test_the_file_view_never_finds_the_trace(stage_view):
    assert not any(p.name.startswith("stage-events")
                   for p in stage_view.list_files())


def test_and_it_cannot_be_opened_by_name_either(stage_view):
    assert stage_view.find_file("stage-events") is None
    assert stage_view.find_file("stage-events.jsonl") is None


# ============================================== a real consumer, a real block
SENTENCES = [
    "Wir fangen mit dem Budget an, das ist der erste Punkt heute.",
    "Insgesamt stehen zwoelftausend Euro zur Verfuegung.",
    "Hey Robo, zeig uns bitte die vier Straenge des Antrags.",
]


class FakeSTT(BaseHTTPRequestHandler):
    """Sends the three sentences once, then keeps the stream open."""
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
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for sentence in SENTENCES:
                self.wfile.write(
                    f"data: {json.dumps({'type': 'final', 'text': sentence})}"
                    "\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            while True:
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass


@pytest.fixture(scope="module")
def meeting(tmp_path_factory):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSTT)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp = tmp_path_factory.mktemp("cothinker-trace")
    project = tmp / "entities" / "testentity" / "projekte" / "testprojekt"
    project.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=tmp, capture_output=True)

    p = subprocess.Popen(
        [sys.executable, str(REPO / "gateway" / "consumer.py"),
         "--stt", f"http://127.0.0.1:{srv.server_address[1]}",
         "--project", "testprojekt", "--dry-agent", "--pause", "0.4",
         "--retry-delay", "0.2", "--commit-every", "100000",
         "--check-every", "0"],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
        env=dict(os.environ, COTHINKER_ENTITIES=str(tmp / "entities"),
                 PYTHONUNBUFFERED="1"))
    # In the session folder of this run, next to the protocol and the
    # proposal file (`sessions/<YYYY-MM-DD>-<slug>/`). Without a `--title`
    # the folder is named after the project.
    trace_path = (project / "sessions"
                  / f"{date.today().isoformat()}-testprojekt"
                  / "stage-events.jsonl")
    deadline = time.monotonic() + 40
    try:
        while time.monotonic() < deadline:
            if trace_path.exists() and "latency" in trace_path.read_text(
                    encoding="utf-8"):
                break
            if p.poll() is not None:
                break
            time.sleep(0.2)
        p.send_signal(signal.SIGINT)
        output = p.communicate(timeout=60)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        output = p.communicate()[0]
    finally:
        srv.shutdown()

    events = rows(trace_path) if trace_path.exists() else []
    content = trace_path.read_text(encoding="utf-8") \
        if trace_path.exists() else ""
    return SimpleNamespace(
        path=trace_path, events=events,
        names=[e["event"] for e in events],
        by_name={e["event"]: e for e in events},
        content=content, output=output, returncode=p.returncode)


def test_the_trace_was_written_next_to_the_protocol(meeting):
    assert meeting.path.exists()


def test_the_meeting_is_started(meeting):
    assert "meeting_started" in meeting.names


def test_the_meeting_is_ended(meeting):
    assert "meeting_ended" in meeting.names


def test_the_enqueued_block_is_recorded_with_its_size(meeting):
    assert meeting.by_name.get("block_enqueued", {}).get("chars", 0) > 0


def test_a_call_out_block_is_marked_as_an_order(meeting):
    assert any(e["event"] == "block_enqueued" and e.get("kind") == "order"
               for e in meeting.events)


def test_the_run_is_recorded_from_start_to_finish(meeting):
    assert "run_started" in meeting.names
    assert meeting.by_name["run_finished"]["ok"] is True
    assert meeting.by_name["run_finished"]["duration_s"] >= 0


def test_the_block_is_recorded_before_the_run_that_picks_it_up(meeting):
    assert meeting.names.index("block_enqueued") \
        < meeting.names.index("run_started")


def test_the_latency_of_the_block_is_recorded(meeting):
    assert meeting.by_name["latency"]["latency_s"] >= 0


def test_the_latency_says_whether_anything_actually_changed(meeting):
    # --dry-agent proposes nothing
    assert meeting.by_name["latency"]["changed"] is False


def test_an_order_that_produced_no_result_is_still_recorded(meeting):
    assert meeting.by_name.get("call_unanswered", {}).get("kind") == "none"
    assert "call_answered" not in meeting.names


def test_the_surface_writes_are_recorded(meeting):
    assert "stage_written" in meeting.names


def test_no_spoken_word_is_in_the_trace(meeting):
    assert not any(w in meeting.content for w in (
        "Budget", "Euro", "Straenge", "Antrag", "zwoelftausend", "Robo"))


def test_the_consumer_came_down_cleanly(meeting):
    assert meeting.returncode == 0 and "Traceback" not in meeting.output
