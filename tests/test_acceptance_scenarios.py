import importlib.util
import json
import sys
from pathlib import Path

import pytest

ACC = Path(__file__).resolve().parent.parent / "demo" / "acceptance"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ACC / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sc():
    return _load("scenarios")


# --------------------------------------------------------------- count_calls

def test_count_calls_serial_mode(sc):
    log = (
        "[10:00:00] agent session: s1\n"
        "[10:00:01]   block #1 (pause, 40 chars)\n"
        "[10:00:05]   block #2 (wake, 80 chars) — ORDER\n"
    )
    assert sc.count_calls(log) == 1


def test_count_calls_lanes_mode(sc):
    log = (
        "[10:00:00] agent session: s1\n"
        "[10:00:01]   block (pause, 40 chars) — lane\n"
        "[10:00:05]   block (wake, 80 chars) — ORDER (lane)\n"
    )
    assert sc.count_calls(log) == 1


def test_count_calls_zero(sc):
    log = "[10:00:00] agent session: s1\n[10:00:01]   block #1 (pause, 40 chars)\n"
    assert sc.count_calls(log) == 0


def test_count_calls_multiple(sc):
    log = "a — ORDER\nb — ORDER (lane)\nc\n"
    assert sc.count_calls(log) == 2


# ------------------------------------------------------- transcript_contains_keywords

def test_transcript_contains_keywords_all_present(sc):
    transcript = (
        "- **10:00** *Alex:* Wir klaeren die Infrastruktur und den Server.\n"
        "- **10:01** *Alex:* Die Oberflaechen sind auch wichtig.\n"
    )
    missing = sc.transcript_contains_keywords(
        transcript, ["Infrastruktur", "Server", "Oberflaechen"])
    assert missing == []


def test_transcript_contains_keywords_reports_missing(sc):
    transcript = "- **10:00** *Alex:* Wir klaeren die Infrastruktur.\n"
    missing = sc.transcript_contains_keywords(
        transcript, ["Infrastruktur", "Server", "Oberflaechen"])
    assert set(missing) == {"Server", "Oberflaechen"}


def test_transcript_contains_keywords_case_insensitive(sc):
    transcript = "- **10:00** *Alex:* der SERVER laeuft.\n"
    missing = sc.transcript_contains_keywords(transcript, ["Server"])
    assert missing == []


# ------------------------------------------------------------------ evaluate_scenario_a

def test_evaluate_scenario_a_pass(sc):
    log = "[10:00:00] agent session: s1\n[10:00:05]   block (wake, 80 chars) — ORDER\n"
    transcript = "- **10:00** *Alex:* Infrastruktur Server Oberflaechen alles klar.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during=None, snapshot_after=None)
    assert check.status == "PASS"


def test_evaluate_scenario_a_fails_on_wrong_call_count(sc):
    log = ("[10:00:05]   block (wake, 80 chars) — ORDER\n"
           "[10:00:10]   block (wake, 80 chars) — ORDER\n")
    transcript = "- **10:00** *Alex:* Infrastruktur Server Oberflaechen.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during=None, snapshot_after=None)
    assert check.status == "FAIL"
    assert "calls=2" in check.text


def test_evaluate_scenario_a_fails_on_missing_keyword(sc):
    log = "[10:00:05]   block (wake, 80 chars) — ORDER\n"
    transcript = "- **10:00** *Alex:* nur Infrastruktur hier.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during=None, snapshot_after=None)
    assert check.status == "FAIL"
    assert "Server" in check.text


def test_evaluate_scenario_a_listen_skipped_when_absent(sc):
    log = "[10:00:05]   block (wake, 80 chars) — ORDER\n"
    transcript = "- **10:00** *Alex:* Infrastruktur Server Oberflaechen.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during={}, snapshot_after={})
    assert check.status == "PASS"
    assert "feature not merged yet" in check.text


def test_evaluate_scenario_a_listen_checked_when_present_ok(sc):
    log = "[10:00:05]   block (wake, 80 chars) — ORDER\n"
    transcript = "- **10:00** *Alex:* Infrastruktur Server Oberflaechen.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during={"listen": {"state": "listening"}},
        snapshot_after={"listen": {"state": "idle"}})
    assert check.status == "PASS"
    assert "listen" in check.text


def test_evaluate_scenario_a_listen_checked_when_present_bad(sc):
    log = "[10:00:05]   block (wake, 80 chars) — ORDER\n"
    transcript = "- **10:00** *Alex:* Infrastruktur Server Oberflaechen.\n"
    check = sc.evaluate_scenario_a(
        log_text=log, transcript_text=transcript,
        keywords=["Infrastruktur", "Server", "Oberflaechen"],
        snapshot_during={"listen": {"state": "idle"}},
        snapshot_after={"listen": {"state": "idle"}})
    assert check.status == "FAIL"
    assert "listen" in check.text


# ------------------------------------------------------------------- agenda matching

def test_normalize_collapses_whitespace_and_case(sc):
    assert sc.normalize("  Hello   WORLD \n") == "hello world"


def test_agenda_lines_drops_empty(sc):
    assert sc.agenda_lines("one\n\n  two  \n") == ["one", "two"]


def test_matches_agenda_finds_long_line(sc):
    agenda = "Budget und Zeitplan besprechen\nkurz\n"
    answer = "Hier kommt die Antwort: budget und zeitplan besprechen, danke."
    matched = sc.matches_agenda(answer, agenda)
    assert matched == "budget und zeitplan besprechen"


def test_matches_agenda_ignores_short_lines(sc):
    agenda = "kurz\n"
    answer = "kurz ist alles was gesagt wurde"
    assert sc.matches_agenda(answer, agenda) is None


def test_matches_agenda_none_when_absent(sc):
    agenda = "Ein Punkt der nicht in der Antwort vorkommt.\n"
    answer = "Etwas ganz anderes."
    assert sc.matches_agenda(answer, agenda) is None


# ------------------------------------------------------------- mid-sentence share

def test_raw_transcript_texts_extracts_rohtranskript(sc):
    protocol = (
        "## Rohtranskript\n"
        "- **10:00** *Alex:* Ein Satz.\n"
        "- **10:01** *Sam:* Noch ein Satz ohne Ende\n"
        "## Verlauf\n"
        "- something else\n"
    )
    texts = sc.raw_transcript_texts(protocol)
    assert texts == ["Ein Satz.", "Noch ein Satz ohne Ende"]


def test_mid_sentence_share_counts_correctly(sc):
    texts = ["Ein Satz.", "Noch einer!", "Mitten im Satz", "Frage?", "ohne ende"]
    count, share = sc.mid_sentence_share(texts)
    assert count == 5
    assert share == pytest.approx(2 / 5)


def test_mid_sentence_share_empty_is_zero(sc):
    count, share = sc.mid_sentence_share([])
    assert count == 0
    assert share == 0.0


# --------------------------------------------------------------- headline check

def test_strip_leading_punct(sc):
    assert sc.strip_leading_punct("„Hey Robo“, los.") == "Hey Robo“, los."


def test_starts_with_wake_phrase_true(sc):
    assert sc.starts_with_wake_phrase('"Hey Robo, fass das zusammen."', "Hey Robo")


def test_starts_with_wake_phrase_false(sc):
    assert not sc.starts_with_wake_phrase("Budgetplanung 2027", "Hey Robo")


def test_topic_titles_extracts_headings(sc):
    protocol = (
        "### 10:00 Beginn\n"
        "- **10:00** *Alex:* hallo\n"
        "### 10:05 Hey Robo, fass das zusammen.\n"
        "- **10:05** *Alex:* weiter\n"
    )
    titles = sc.topic_titles(protocol)
    assert titles == ["Beginn", "Hey Robo, fass das zusammen."]


def test_evaluate_scenario_d_pass(sc):
    check = sc.evaluate_scenario_d(
        headline="Quartalsplanung", titles=["Beginn", "Budget"],
        wake_phrase="Hey Robo")
    assert check.status == "PASS"


def test_evaluate_scenario_d_fails_on_headline(sc):
    check = sc.evaluate_scenario_d(
        headline="Hey Robo, fass das zusammen", titles=["Beginn"],
        wake_phrase="Hey Robo")
    assert check.status == "FAIL"


def test_evaluate_scenario_d_fails_on_topic_title(sc):
    check = sc.evaluate_scenario_d(
        headline="Budget", titles=["Beginn", "Hey Robo, fass das zusammen."],
        wake_phrase="Hey Robo")
    assert check.status == "FAIL"


# ------------------------------------------------------------------- CLI wiring

def test_requires_known_scenario_names(sc):
    with pytest.raises(SystemExit) as e:
        sc.parse_args(["--audio-dir", "x", "--url-file", "u", "--cdp-port", "1",
                       "--snapshot-url", "s", "--token-file", "t",
                       "--consumer-log", "c", "--session-dir", "d",
                       "--scenarios", "a,z"])
    assert e.value.code == 2


def test_default_scenarios_are_all_four(sc):
    a = sc.parse_args(["--audio-dir", "x", "--url-file", "u", "--cdp-port", "1",
                       "--snapshot-url", "s", "--token-file", "t",
                       "--consumer-log", "c", "--session-dir", "d"])
    assert a.scenarios == ["a", "b", "c", "d"]


def test_run_scenario_a_skipped_without_wav(sc, tmp_path):
    a = sc.parse_args(["--audio-dir", str(tmp_path), "--url-file", "u",
                       "--cdp-port", "1", "--snapshot-url", "s",
                       "--token-file", "t", "--consumer-log", "c",
                       "--session-dir", "d", "--scenarios", "a"])
    check = sc.run_scenario("a", a)
    assert check.status == "SKIPPED"
    assert "a.wav" in check.text


def test_run_scenario_b_skipped_without_agenda_file(sc, tmp_path):
    (tmp_path / "b.wav").write_bytes(b"RIFF")
    a = sc.parse_args(["--audio-dir", str(tmp_path), "--url-file", "u",
                       "--cdp-port", "1", "--snapshot-url", "s",
                       "--token-file", "t", "--consumer-log", "c",
                       "--session-dir", "d", "--scenarios", "b"])
    check = sc.run_scenario("b", a)
    assert check.status == "SKIPPED"
    assert "agenda-file" in check.text
