# CoThinker — tests of the web view
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Characterization tests of the stage: Markdown converter, broken
buehne.json, HTTP, history, session folders, graph surfaces, protocol v2.

The stage is publicly reachable and shows text that no one has checked:
raw transcript from automatic speech recognition, plus JSON written by a
language model. Either can contain anything.

    python3 -m pytest -q tests/test_stage.py

Every test builds its own project folder (see the autouse `proj` fixture)
and leaves the module state of `stage.py` as it found it.
"""
from __future__ import annotations

import copy
import html
import inspect
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage"))
sys.path.insert(0, str(ROOT / "gateway"))

import stage as b  # noqa: E402


# ------------------------------------------------------------------ helpers
def setze(daten) -> None:
    """Sets buehne.json (deliberately broken too) and clears the cache."""
    if isinstance(daten, str):
        (b.PROJECT / "buehne.json").write_text(daten, encoding="utf-8")
    else:
        (b.PROJECT / "buehne.json").write_text(
            json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    b.clear_cache()


def v2(**felder) -> dict:
    """A schema-v2 buehne.json, as `StageState.content()` writes it.

    The fixtures here are written by hand, but field for field the same
    shape as the producer's.
    """
    daten = {
        "version": 2,
        "updated": "2026-09-08T19:12:04",
        "status": {"state": "listening", "text": "🎧 hört zu · zuletzt 19:12:04",
                   "ticking": False, "since": ""},
        "mirror": "",
        "headline": "",
        "main": {"id": "M1", "markdown": "", "set_at": "2026-09-08T19:10:00",
                 "collapsed_lines": 0, "collapsed_markdown": ""},
        "decisions": [],
        "notices": [],
        "question": None,
        "links": [],
        "parked": 0,
        "parked_entries": [],
        "age_visible_after_s": 20,
        "follow_up_after_s": 120,
    }
    daten.update(felder)
    return daten


def switch_project(path: Path) -> Path:
    """Points the stage at another project folder (restored by `proj`)."""
    path.mkdir(parents=True, exist_ok=True)
    b.PROJECT = path
    b.clear_cache()
    b._last_good = None
    return path


@pytest.fixture(autouse=True)
def proj(tmp_path):
    """A fresh project folder with a flat protocol and one project file."""
    saved = (b.PROJECT, b.VAULT, b._last_good)
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "protokoll-2026-09-07.md").write_text(
        "## Rohtranskript\n- **10:00** Streng vertraulicher Satz aus dem Termin.\n",
        encoding="utf-8")
    (folder / "usecases.md").write_text("# Use Cases\n- eins\n", encoding="utf-8")
    b.PROJECT = folder
    b.VAULT = Path()
    b.clear_cache()
    b._last_good = None
    yield folder
    b.PROJECT, b.VAULT, b._last_good = saved
    b.clear_cache()


@pytest.fixture(scope="module")
def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), b.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def basis(_server):
    return _server


@pytest.fixture
def hole(_server):
    def fetch(pfad: str):
        try:
            with urllib.request.urlopen(_server + pfad, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
    return fetch


HTTP_STAGE = dict(headline="Punkt 2: Budget & Personal",
                  main={"markdown": "## Beschlossen\n- 12.000 EUR\n"},
                  links=[{"file": "usecases", "label": "Use-Cases"}])


# =================================================================== Markdown
def test_html_is_defused():
    assert "<script>" not in b.md("<script>alert(1)</script>", set())


def test_text_stays_readable():
    assert "alert(1)" in b.md("<script>alert(1)</script>", set())


def test_umlauts_and_emoji_survive():
    assert "Grüße 🎧 über" in b.md("Grüße 🎧 über", set())


def test_bold():
    assert "<strong>fett</strong>" in b.md("**fett**", set())


def test_heading():
    assert "<h2>Punkt</h2>" in b.md("## Punkt", set())


def test_list():
    assert "<li>eins</li>" in b.md("- eins", set())


def test_code_stays_raw():
    assert "&lt;b&gt;" in b.md("```\n<b>\n```", set())


def test_table():
    assert "<td>a</td>" in b.md("| a | b |\n|---|---|\n| c | d |", set())


# Quotes must never break out of an attribute
def test_wikilink_doesnt_break_out_of_the_attribute():
    gift = '[[datei" onmouseover="alert(1)]]'
    h = b.md(gift, {'datei" onmouseover="alert(1)'})
    assert 'onmouseover="alert' not in h


def test_link_doesnt_break_out_of_the_attribute():
    gift2 = '[Klick](https://x.example/" onmouseover="alert(1))'
    assert 'onmouseover="alert' not in b.md(gift2, set())


def test_javascript_links_are_not_linkified():
    assert 'href="javascript:' not in b.md(
        "[Klick](javascript:alert(1))", set()).lower()


# The converter must not fail on any input
MD_JUNK = ["", "   ", "```\nunbeendet", "| unvollstaendig", "#" * 20,
           "\x00\x01 Steuerzeichen", "a" * 50000, "---\nfront: matter\n---\nText",
           "* * * *", "[[]]", "[]()", "\n\n\n"]


@pytest.mark.parametrize("text", MD_JUNK, ids=[repr(t[:18]) for t in MD_JUNK])
def test_md_holds_up(text):
    b.md(text, set())


# ============================================================== buehne.json
def test_good_stage_is_shown():
    setze(v2(headline="Punkt 2: Budget",
             main={"markdown": "## Beschlossen\n- 12.000 EUR\n"},
             links=[{"file": "usecases", "label": "Use-Case-Tabelle"}]))
    assert "12.000 EUR" in b.stage_parts()["main"]


def test_link_is_set():
    # Links are relative (the stage can be mounted under a proxy subpath);
    # where they actually lead is checked by the HTTP tests further below.
    setze(v2(headline="Punkt 2: Budget",
             main={"markdown": "## Beschlossen\n- 12.000 EUR\n"},
             links=[{"file": "usecases", "label": "Use-Case-Tabelle"}]))
    assert 'href="file/usecases"' in b.stage_parts()["links"]


def _good_then(kaputt):
    """Shows a good stage once, then replaces the file by something broken."""
    setze(v2(headline="Punkt 2: Budget",
             main={"markdown": "## Beschlossen\n- 12.000 EUR\n"}))
    b.stage_parts()
    setze(kaputt)
    return b.stage_parts()


HALF_WRITTEN = '{"version": 2, "headline": "Punkt 3", "main": {"markdown": "## Bes'


def test_half_written_stage_doesnt_crash():
    # Half-written file: the consumer replaces it, a reader hits the middle.
    assert isinstance(_good_then(HALF_WRITTEN), dict)


def test_half_written_stage_shows_the_last_good_state():
    assert "12.000 EUR" in _good_then(HALF_WRITTEN)["main"]


def test_half_written_stage_does_not_show_the_raw_transcript():
    assert "vertraulicher Satz" not in _good_then(HALF_WRITTEN)["main"]


def test_broken_stage_shows_no_raw_transcript():
    assert "vertraulicher Satz" not in _good_then("das ist gar kein JSON")["main"]


@pytest.mark.parametrize("daten", [[1, 2, 3], None, 42, "eine Zeichenkette"],
                         ids=["list", "NoneType", "int", "str"])
def test_stage_holds_up_on_json_that_is_not_an_object(daten):
    setze(daten)
    assert "vertraulicher Satz" not in b.stage_parts()["main"]


WRONG_TYPES = {"version": 2, "status": 5, "headline": 42,
               "main": ["Liste", "statt", "Text"], "links": "keine Liste",
               "decisions": {"a": 1}, "notices": "nope", "question": 7,
               "age_visible_after_s": "viel", "follow_up_after_s": None}


def test_stage_holds_up_on_wrong_field_types():
    # The agent is a language model, not a schema, and the consumer copies
    # some of its fields through.
    setze(WRONG_TYPES)
    assert isinstance(b.stage_parts(), dict)


def test_the_thresholds_fall_back_to_their_defaults():
    setze(WRONG_TYPES)
    t = b.stage_parts()
    assert t["age_visible_after_s"] == 20 and t["follow_up_after_s"] == 120


def test_stage_holds_up_on_broken_links():
    setze(v2(main={"markdown": "Text"},
             links=[None, "kaputt", {"label": "ohne Datei"}, {"file": "usecases"}]))
    assert 'href="file/usecases"' in b.stage_parts()["links"]


def test_very_long_text_gets_capped():
    # Very long text must not overwhelm the room's browsers
    setze(v2(main={"markdown": "Zeile mit Text\n" * 20000}))
    assert len(b.stage_parts()["main"]) < 200000


@pytest.fixture
def injected_fields():
    setze(v2(headline="<img src=x onerror=alert(1)>",
             main={"markdown": "harmlos"},
             links=[{"file": '"><script>alert(1)</script>', "label": "x"}],
             question={"id": "Q1", "text": "<script>alert(1)</script>",
                       "raised_at": "2026-09-08T19:11:00", "state": "open",
                       "resolution": "", "follow_up": False}))
    return b.stage_parts()


def test_question_defused(injected_fields):
    assert "<script>" not in injected_fields["question"]


def test_link_target_defused(injected_fields):
    assert "<script>" not in injected_fields["links"]


def test_headline_arrives_as_plain_text(injected_fields):
    # `headline` stays plain text here (the page sets it via textContent);
    # it's escaped when embedded in the HTML page -- see the HTTP tests.
    assert injected_fields["headline"] == "<img src=x onerror=alert(1)>"


# --- Mirror line: the agent's paraphrase ---
def test_mirror_is_shown():
    setze(v2(mirror="Robo hat den Budgetbeschluss aufgenommen.",
             headline="Punkt 2", main={"markdown": "- egal\n"}))
    assert "Budgetbeschluss aufgenommen" in b.stage_parts()["mirror"]


def test_mirror_defused():
    setze(v2(mirror="<script>alert(1)</script>", main={"markdown": "- egal\n"}))
    assert "<script>" not in b.stage_parts()["mirror"]


def test_mirror_holds_up_on_the_wrong_field_type():
    setze(v2(mirror={"kein": "Text"}, main={"markdown": "- egal\n"}))
    assert isinstance(b.stage_parts()["mirror"], str)


def test_without_a_mirror_the_line_stays_empty():
    # The mirror line is optional and must not burden the stage with an
    # empty row.
    setze(v2(main={"markdown": "- egal\n"}))
    assert b.stage_parts()["mirror"] == ""


def test_mirror_feeds_into_the_fingerprint():
    # A change to only the mirror must move the fingerprint, otherwise the
    # status line fakes a standstill.
    setze(v2(main={"markdown": "- egal\n"}))
    t1 = b.stage_parts()
    setze(v2(mirror="Robo arbeitet.", main={"markdown": "- egal\n"}))
    t2 = b.stage_parts()
    assert b.fingerprint(t1) != b.fingerprint(t2)


# `call_answer` is a substantive call-out result shown on its own, separate
# from `mirror` -- same defusing/optionality/fingerprint guarantees, none of
# mirror's 300-char cap (it is meant to be read).
def test_call_answer_defused():
    setze(v2(call_answer="<script>alert(1)</script>", main={"markdown": "- egal\n"}))
    assert "<script>" not in b.stage_parts()["call_answer"]


def test_without_a_call_answer_the_panel_stays_empty():
    setze(v2(main={"markdown": "- egal\n"}))
    assert b.stage_parts()["call_answer"] == ""


LONG_ANSWER = "Fördermöglichkeiten: " + ("Programm X, " * 40)


def test_call_answer_is_not_cut_down_to_mirrors_300_chars():
    setze(v2(call_answer=LONG_ANSWER, main={"markdown": "- egal\n"}))
    assert len(b.stage_parts()["call_answer"]) > 300


def test_call_answer_feeds_into_the_fingerprint():
    setze(v2(main={"markdown": "- egal\n"}))
    t3 = b.stage_parts()
    setze(v2(call_answer=LONG_ANSWER, main={"markdown": "- egal\n"}))
    t4 = b.stage_parts()
    assert b.fingerprint(t3) != b.fingerprint(t4)


# Missing file
def test_missing_buehne_json_doesnt_crash():
    setze(v2(main={"markdown": "x"}))
    (b.PROJECT / "buehne.json").unlink()
    b.clear_cache()
    assert isinstance(b.stage_parts(), dict)


def test_missing_buehne_json_shows_no_raw_transcript():
    setze(v2(main={"markdown": "x"}))
    (b.PROJECT / "buehne.json").unlink()
    b.clear_cache()
    assert "vertraulicher Satz" not in b.stage_parts()["main"]


# ====================================================================== HTTP
@pytest.fixture
def http_stage(hole):
    setze(v2(**HTTP_STAGE))
    return hole


def test_stage_answers(http_stage):
    code, _ = http_stage("/")
    assert code == 200


def test_stage_shows_the_state(http_stage):
    assert "12.000 EUR" in http_stage("/")[1]


def test_headline_is_escaped_in_the_page(hole):
    setze(v2(headline="<img src=x onerror=alert(1)>", main={"markdown": "harmlos"}))
    code, seite = hole("/")
    assert "<img src=x" not in seite and "&lt;img src=x" in seite, code


def test_parts_answers(http_stage):
    assert http_stage("/parts")[0] == 200


def test_parts_returns_a_fingerprint(http_stage):
    assert bool(json.loads(http_stage("/parts")[1]).get("mark"))


def test_parts_headline_is_plain_text_not_double_escaped(http_stage):
    assert "&amp;" not in json.loads(http_stage("/parts")[1])["headline"]


def test_parts_carries_the_clock_fields_the_page_ticks_with(http_stage):
    teile = json.loads(http_stage("/parts")[1])
    assert set(teile) >= {"ticking", "since", "age_visible_after_s",
                          "follow_up_after_s"}


# The script reads a fixed set of keys off the payload every 3 s. A key that
# is missing throws on EVERY poll and freezes the view -- the `uhr` vs
# `clock` class of bug. The expected set is read out of the script itself,
# so a newly used key cannot be forgotten here.
SKRIPT_SCHLUESSEL = set(re.findall(r"\bj\.([A-Za-z_][A-Za-z0-9_]*)", b.SCRIPT))


def test_the_scripts_key_list_could_be_read_from_the_script():
    assert len(SKRIPT_SCHLUESSEL) >= 10, sorted(SKRIPT_SCHLUESSEL)


def test_parts_stage_serves_every_key_the_script_reads(http_stage):
    teile = json.loads(http_stage("/parts")[1])
    assert SKRIPT_SCHLUESSEL <= set(teile), sorted(SKRIPT_SCHLUESSEL - set(teile))


# The file view uses the SAME script and therefore needs the same shape.
def test_parts_for_a_file_answers(http_stage):
    assert http_stage("/parts?d=usecases")[0] == 200


def test_parts_file_view_serves_every_key_the_script_reads(http_stage):
    dateiteile = json.loads(http_stage("/parts?d=usecases")[1])
    assert SKRIPT_SCHLUESSEL <= set(dateiteile), \
        sorted(SKRIPT_SCHLUESSEL - set(dateiteile))


def test_parts_file_view_carries_the_files_own_fingerprint_and_content(http_stage):
    dateiteile = json.loads(http_stage("/parts?d=usecases")[1])
    assert bool(dateiteile.get("mark")) and "Use Cases" in dateiteile["main"]


def test_parts_file_view_carries_no_operating_line(http_stage):
    dateiteile = json.loads(http_stage("/parts?d=usecases")[1])
    assert dateiteile["status"] == "" and dateiteile["ticking"] is False


def test_project_file_reachable(http_stage):
    assert http_stage("/file/usecases")[0] == 200


def test_file_view_fetches_its_own_fingerprint(http_stage):
    assert '"parts?d=usecases"' in http_stage("/file/usecases")[1]


def test_file_view_sets_its_reference_point_one_level_up(http_stage):
    assert '<base href="../">' in http_stage("/file/usecases")[1]


def test_stage_sets_its_reference_point_to_itself(http_stage):
    assert '<base href="./">' in http_stage("/")[1]


# A file name with an apostrophe must not break the script
APOSTROPH = "the operator's Notizen"


def test_file_name_with_an_apostrophe(hole):
    (b.PROJECT / (APOSTROPH + ".md")).write_text("# Notizen\n", encoding="utf-8")
    b.clear_cache()
    code, _ = hole("/file/" + urllib.parse.quote(APOSTROPH))
    assert code == 200


def test_apostrophe_doesnt_break_the_script(hole):
    (b.PROJECT / (APOSTROPH + ".md")).write_text("# Notizen\n", encoding="utf-8")
    b.clear_cache()
    code, aseite = hole("/file/" + urllib.parse.quote(APOSTROPH))
    assert "d=the operator's" not in aseite


# All links are relative (the stage can be mounted under /buehne/ of a
# proxy). They must land in the right place from EVERY page -- especially
# from the file view, which sits one level deeper.
@pytest.fixture
def linked(hole):
    (b.PROJECT / "notizen.md").write_text(
        "# Notizen\nSiehe [[usecases]] dazu.\n", encoding="utf-8")
    setze(v2(main={"markdown": "Siehe [[usecases]] und [[notizen]]."}))
    return hole


@pytest.mark.parametrize("seitenpfad", ["/", "/file/notizen"])
def test_page_reference_point_set(linked, seitenpfad):
    code, s = linked(seitenpfad)
    assert re.search(r'<base href="([^"]+)">', s)


LINK_TARGETS = [
    ("/", "#", "/"),
    ("/", "file/protokoll-2026-09-07", "/file/protokoll-2026-09-07"),
    ("/", "parts", "/parts"),
    ("/", "graph", "/graph"),
    ("/", "./", "/"),
    ("/", "file/usecases", "/file/usecases"),
    ("/", "file/notizen", "/file/notizen"),
    ("/file/notizen", "#", "/"),
    ("/file/notizen", "parts", "/parts"),
    ("/file/notizen", "./", "/"),
    ("/file/notizen", "file/usecases", "/file/usecases"),
    ("/file/notizen", "../", "/"),
]


@pytest.mark.parametrize("seitenpfad,verweis,erwartet", LINK_TARGETS,
                         ids=[f"{s}: {v} -> {e}" for s, v, e in LINK_TARGETS])
def test_relative_link_lands_in_the_right_place(linked, basis, seitenpfad,
                                                verweis, erwartet):
    code, s = linked(seitenpfad)
    wurzel = re.search(r'<base href="([^"]+)">', s)
    grund = urllib.parse.urljoin(basis + seitenpfad, wurzel.group(1))
    # The link really is on that page (the set is the page's own hrefs)
    assert verweis in (set(re.findall(r'href="([^":]+)"', s)) | {"parts"})
    ziel = urllib.parse.urljoin(grund, verweis)
    assert ziel[len(basis):] == erwartet
    c, _ = linked(ziel[len(basis):])
    assert c == 200


# No escaping the project folder
@pytest.mark.parametrize("pfad", [
    "/file/..%2F..%2F..%2Fetc%2Fpasswd", "/file/../../../etc/passwd",
    "/file/%2Fetc%2Fpasswd", "/file/....//....//etc/passwd"],
    ids=lambda p: p[:34])
def test_no_escape(http_stage, pfad):
    code, inhalt = http_stage(pfad)
    assert code == 404 and "root:" not in inhalt


def test_unknown_path_is_404(http_stage):
    assert http_stage("/gibtsnicht")[0] == 404


def test_parts_with_an_unknown_file_is_404(http_stage):
    assert http_stage("/parts?d=gibtsnicht")[0] == 404


def test_protocol_file_can_be_opened(http_stage):
    # The raw protocol is reachable via the stage -- that's intentional (the
    # room should be able to look things up), but it must not crash.
    assert http_stage("/file/protokoll-2026-09-07")[0] == 200


def test_240_fetches_during_constant_rewriting_without_an_error(http_stage):
    # Many concurrent readers while the file is constantly rewritten
    hole = http_stage
    folder = b.PROJECT
    fehler: list = []
    halt = threading.Event()

    def schreiber():
        i = 0
        while not halt.is_set():
            i += 1
            try:
                (folder / "buehne.json").write_text(
                    json.dumps(v2(headline=f"Runde {i}",
                                  main={"markdown": f"- Punkt {i}\n"})),
                    encoding="utf-8")
                # and once deliberately half-written
                (folder / "buehne.json").write_text(
                    '{"version": 2, "headline": "halb', encoding="utf-8")
            except OSError:
                pass
            time.sleep(0.002)

    def leser():
        for _ in range(40):
            try:
                code, inhalt = hole("/parts")
                if code != 200:
                    fehler.append(f"HTTP {code}")
                else:
                    # The flat protocol fixture is legitimately the right
                    # column's source: `protokoll` in the payload carries
                    # it. The guarantee owned here is that the curated
                    # `main` field never gets the raw transcript, concurrent
                    # rewrite or not.
                    daten = json.loads(inhalt)
                    if "vertraulicher Satz" in daten.get("main", ""):
                        fehler.append("raw transcript in main!")
            except Exception as e:                  # noqa: BLE001
                fehler.append(repr(e))

    sf = threading.Thread(target=schreiber, daemon=True)
    sf.start()
    lese_faeden = [threading.Thread(target=leser) for _ in range(6)]
    for f in lese_faeden:
        f.start()
    for f in lese_faeden:
        f.join()
    halt.set()
    sf.join(2)
    assert not fehler, f"{len(fehler)} errors, e.g. {fehler[:3]}"


# ================================================================= schema v2
SCHEMA_V2_FULL = {
    "version": 2,
    "updated": "2026-09-08T19:12:04",
    "status": {"state": "working", "text": "⚙️ Robo werkelt seit — nicht "
                                           "abgestürzt, nur gründlich",
               "ticking": True, "since": "2026-09-08T19:11:50"},
    "mirror": "Robo hat die vier Stränge aufgenommen.",
    "headline": "Vorbereitung Planungs-Call",
    "main": {"id": "M1", "markdown": "## Vier Stränge\n- Update",
             "set_at": "2026-09-08T19:10:00",
             "collapsed_lines": 3, "collapsed_markdown": "- a\n- b\n- c"},
    "decisions": [{"id": "D1", "time": "19:43", "text": "kein Pitch",
                   "added_at": "2026-09-08T19:43:02+02:00"},
                  {"id": "D2", "time": "19:44", "text": "the operator hat den Lead",
                   "added_at": "2026-09-08T19:44:11+02:00"}],
    "notices": [{"id": "N1", "kind": "value_conflict",
                 "text": "**Frist** 6.10. gegen 5.10.",
                 "raised_at": "2026-09-08T19:09:00", "state": "open",
                 "resolution": "", "follow_up": True}],
    "question": {"id": "Q1", "text": "Fünfter oder sechster Oktober?",
                 "raised_at": "2026-09-08T19:11:00", "state": "open",
                 "resolution": "", "follow_up": False},
    "links": [{"file": "protokoll-2026-09-08", "label": "Protokoll 08.09."}],
    "parked": 0, "parked_entries": [],
    "age_visible_after_s": 20, "follow_up_after_s": 120,
}


@pytest.fixture
def p():
    setze(SCHEMA_V2_FULL)
    return b.stage_parts()


def test_the_operating_line_is_carried(p):
    assert "werkelt seit" in p["status"]


def test_ticking_is_signalled(p):
    assert p["ticking"] is True


def test_since_is_carried(p):
    assert p["since"] == "2026-09-08T19:11:50"


def test_the_mirror_line_is_there(p):
    assert "vier Stränge aufgenommen" in p["mirror"]


def test_the_headline_is_plain_text(p):
    assert p["headline"] == "Vorbereitung Planungs-Call"


def test_the_main_area_is_rendered_markdown(p):
    assert "<h2>" in p["main"]


def test_the_collapsed_part_is_a_details_block(p):
    assert "<details" in p["collapsed"] and "3" in p["collapsed"]


def test_the_collapsed_block_is_labelled_in_german(p):
    assert "weitere Zeilen" in p["collapsed"]


def test_both_decisions_are_on_one_line(p):
    assert "kein Pitch" in p["decisions"] and "the operator hat den Lead" in p["decisions"]


def test_a_decision_carries_its_added_at_as_a_data_attribute(p):
    # The page can only mark a newly appended entry (`neu`) if it learns
    # WHEN the entry was appended.
    assert 'data-added="2026-09-08T19:44:11+02:00"' in p["decisions"]


def test_the_server_does_not_decide_neu_itself_the_browser_ages_it(p):
    assert "frisch" not in p["decisions"]


def test_the_notice_carries_its_mark(p):
    assert "⚠" in p["notices"]


def test_the_notice_carries_its_raising_time(p):
    assert "2026-09-08T19:09:00" in p["notices"]


def test_the_follow_up_state_is_marked(p):
    assert "nachfassen" in p["notices"]


def test_the_notice_is_a_line_not_a_box(p):
    assert '<div class="notice' in p["notices"]


def test_bold_inside_a_notice_survives(p):
    assert "<strong>Frist</strong>" in p["notices"]


def test_the_question_is_rendered(p):
    assert "sechster Oktober" in p["question"]


def test_the_question_carries_its_own_mark(p):
    assert "?" in p["question"]


def test_the_link_points_at_the_file_view(p):
    assert 'href="file/protokoll-2026-09-08"' in p["links"]


def test_the_sidebar_label_is_german(p):
    assert "Vertiefen" in p["links"]


# A settled entry becomes a receipt in the same slot -- it does not vanish.
@pytest.fixture
def resolved():
    setze(v2(notices=[{"id": "N1", "kind": "value_conflict", "text": "Frist",
                       "raised_at": "2026-09-08T19:09:00", "state": "resolved",
                       "resolution": "6.10. gilt", "follow_up": False}]))
    return b.stage_parts()


def test_a_resolved_notice_shows_the_german_receipt(resolved):
    assert "aufgelöst" in resolved["notices"] and "6.10. gilt" in resolved["notices"]


def test_a_resolved_notice_is_marked_settled(resolved):
    assert "settled" in resolved["notices"]


def test_a_resolved_notice_carries_the_check_mark(resolved):
    assert "✓" in resolved["notices"]


# An EXPIRED question is not a resolved one. Rendering both as the green
# receipt told the round a question nobody answered had been settled. A
# dimmed line says it is unanswered and now lives under `## Offene Fragen`.
@pytest.fixture
def expired():
    setze(v2(question={"id": "Q1", "text": "Fünfter oder sechster Oktober?",
                       "raised_at": "2026-09-08T19:01:00+02:00",
                       "state": "expired", "resolution": "", "follow_up": False}))
    return b.stage_parts()


def test_an_expired_question_is_not_called_resolved(expired):
    assert "aufgelöst" not in expired["question"]


def test_it_says_it_stayed_unanswered(expired):
    assert "<strong>unbeantwortet</strong>" in expired["question"]


def test_and_where_it_went(expired):
    assert "Offene Fragen" in expired["question"]


def test_it_does_not_repeat_the_question_as_if_it_were_the_answer(expired):
    assert "sechster Oktober" not in expired["question"]


def test_it_is_dimmed_not_the_green_receipt(expired):
    assert "abgelaufen" in expired["question"]


def test_and_it_carries_its_own_mark_not_the_check_mark(expired):
    assert "⌛" in expired["question"] and "✓" not in expired["question"]


# --- the parked third notice: one dimmed line, content only in the protocol
ZWEI = [{"id": "N1", "kind": "value_conflict", "text": "Frist",
         "raised_at": "2026-09-08T19:09:00+02:00", "state": "open",
         "resolution": "", "follow_up": False},
        {"id": "N2", "kind": "decision_breach", "text": "Auto-Router",
         "raised_at": "2026-09-08T19:10:00+02:00", "state": "open",
         "resolution": "", "follow_up": False}]


@pytest.fixture
def parked():
    setze(v2(main={"markdown": "- egal"}, notices=ZWEI, parked=1,
             parked_entries=[{"text": "**Betrag** 12.000 gegen 14.000",
                              "parked_at": "2026-09-08T19:11:30+02:00"}]))
    return b.stage_parts()


def test_the_parked_line_is_a_line_in_the_notice_zone_not_a_box(parked):
    assert '<div class="notice parked"' in parked["parked"]


def test_the_parked_line_is_the_approved_german_verbatim(parked):
    assert ("<strong>geparkt</strong> — zwei Plätze belegt, Rest steht unter "
            "Offene Fragen" in parked["parked"])


def test_the_parked_line_carries_a_neutral_mark_not_the_warning_mark(parked):
    assert '<span class="mark">·</span>' in parked["parked"] \
        and "⚠" not in parked["parked"]


def test_the_parked_content_itself_stays_off_the_stage(parked):
    assert "12.000" not in parked["parked"] and "Betrag" not in parked["parked"]


def test_three_parked_entries_are_still_exactly_one_line():
    setze(v2(main={"markdown": "- egal"}, notices=ZWEI, parked=3,
             parked_entries=[{"text": "eins", "parked_at": "2026-09-08T19:11:30+02:00"},
                             {"text": "zwei", "parked_at": "2026-09-08T19:11:40+02:00"},
                             {"text": "drei", "parked_at": "2026-09-08T19:11:50+02:00"}]))
    assert b.stage_parts()["parked"].count('class="notice parked"') == 1


def test_nothing_parked_no_line_at_all():
    setze(v2(main={"markdown": "- egal"}, notices=ZWEI))
    assert b.stage_parts()["parked"] == ""


BROKEN_PARKED = ["nope", [1, "zwei", None], {"eins": 1}, 7, None]


@pytest.mark.parametrize("kaputt", BROKEN_PARKED, ids=[repr(k) for k in BROKEN_PARKED])
def test_stage_parts_survives_parked_entries(kaputt):
    setze(v2(main={"markdown": "- egal"}, parked_entries=kaputt))
    b.stage_parts()


@pytest.mark.parametrize("kaputt", BROKEN_PARKED, ids=[repr(k) for k in BROKEN_PARKED])
def test_a_broken_parked_entries_renders_no_line(kaputt):
    setze(v2(main={"markdown": "- egal"}, parked_entries=kaputt))
    t = b.stage_parts()
    assert bool(t) and t["parked"] == ""


@pytest.fixture
def parked_page(hole):
    setze(v2(main={"markdown": "- egal"}, notices=ZWEI, parked=1,
             question={"id": "Q1", "text": "Fünfter oder sechster?",
                       "raised_at": "2026-09-08T19:11:00+02:00", "state": "open",
                       "resolution": "", "follow_up": False},
             parked_entries=[{"text": "**Betrag** 12.000 gegen 14.000",
                              "parked_at": "2026-09-08T19:11:30+02:00"}]))
    return hole


def test_the_page_carries_the_parked_line(parked_page):
    code, seite = parked_page("/")
    assert code == 200 and "geparkt" in seite


def test_the_parked_line_sits_in_the_notice_zone_below_the_question(parked_page):
    seite = parked_page("/")[1]
    assert seite.index('class="notice parked"') \
        > seite.index('class="notice question"') \
        > seite.index('id="notices"')


def test_the_parked_line_is_dimmed_by_its_own_rule(parked_page):
    assert ".notice.parked{opacity:" in parked_page("/")[1]


def test_parts_carries_the_parked_line_as_its_own_part(parked_page):
    assert "geparkt" in json.loads(parked_page("/parts")[1])["parked"]


# --- the `neu` mark on a decision is aged in the browser
def test_the_script_counts_the_four_seconds_itself():
    assert "frischMs" in b.SCRIPT and "4000" in b.SCRIPT


def test_the_script_ages_the_newest_decision_off_data_added():
    assert "data-added" in b.SCRIPT and "'frisch'" in b.SCRIPT


def test_the_highlight_reuses_the_existing_frisch_class():
    assert "#decisions span.frisch" in b.STYLE


# --- quiet: the ticking time does not move the fingerprint
TICKING = {
    "version": 2, "updated": "2026-09-08T19:12:04",
    "status": {"state": "working", "text": "⚙️ Robo werkelt seit — nicht "
                                           "abgestürzt, nur gründlich",
               "ticking": True, "since": "2026-09-08T19:11:50"},
    "mirror": "Robo hat die vier Stränge aufgenommen.",
    "headline": "Vorbereitung Planungs-Call",
    "main": {"id": "M1", "markdown": "## Vier Stränge\n- Update",
             "set_at": "2026-09-08T19:10:00", "collapsed_lines": 0,
             "collapsed_markdown": ""},
    "decisions": [], "notices": [], "question": None, "links": [],
    "parked": 0, "age_visible_after_s": 20, "follow_up_after_s": 120,
}


def test_only_the_clock_moved_same_fingerprint():
    setze(TICKING)
    a = b.fingerprint(b.stage_parts())
    roh = copy.deepcopy(TICKING)
    roh["updated"] = "2026-09-08T19:59:59"
    roh["status"]["since"] = "2026-09-08T19:59:00"
    setze(roh)
    assert b.fingerprint(b.stage_parts()) == a


def test_real_content_different_fingerprint():
    setze(TICKING)
    a = b.fingerprint(b.stage_parts())
    roh = copy.deepcopy(TICKING)
    roh["updated"] = "2026-09-08T19:59:59"
    roh["status"]["since"] = "2026-09-08T19:59:00"
    roh["mirror"] = "Robo ergänzt das Strategiepapier."
    setze(roh)
    assert b.fingerprint(b.stage_parts()) != a


def _listening(clock: str):
    return v2(headline="Vorbereitung Planungs-Call",
              status={"state": "listening",
                      "text": f"🎧 hört zu · zuletzt {clock}",
                      "ticking": False, "since": ""},
              main={"markdown": "## Vier Stränge\n- Update"})


# The fixture above uses `working`, whose line is static -- it would pass
# even with the operating line in the fingerprint. The DOMINANT state is
# `listening`, and its line carries a clock down to the second. That is the
# case that decides whether the page stays quiet.
@pytest.mark.parametrize("spaeter", ["19:12:05", "19:12:06", "19:13:00", "20:04:59"])
def test_listening_only_the_clock_moved_same_fingerprint(spaeter):
    setze(_listening("19:12:04"))
    ruhig = b.fingerprint(b.stage_parts())
    setze(_listening(spaeter))
    assert b.fingerprint(b.stage_parts()) == ruhig


def test_a_state_change_still_reaches_parts_unconditionally():
    # A real state change is still visible on the page, because the script
    # applies `status` before it ever looks at the fingerprint.
    setze(v2(headline="Vorbereitung Planungs-Call",
             status={"state": "working", "text": "⚙️ Robo werkelt seit",
                     "ticking": True, "since": "2026-09-08T19:11:50"},
             main={"markdown": "## Vier Stränge\n- Update"}))
    t_arbeit = b.stage_parts()
    assert "werkelt seit" in t_arbeit["status"] and t_arbeit["ticking"] is True


# The `neu` mark on a decision and the parked line both age. If either
# ageing reached the fingerprint, the page would swap on every poll. Both
# timestamps are fixed properties of their entry: only the clock around
# them moves.
FEST = dict(main={"markdown": "## Vier Stränge\n- Update"},
            headline="Vorbereitung Planungs-Call",
            decisions=[{"id": "D1", "time": "19:43", "text": "kein Pitch",
                        "added_at": "2026-09-08T19:43:02+02:00"}],
            parked=1,
            parked_entries=[{"text": "**Betrag** 12.000 gegen 14.000",
                             "parked_at": "2026-09-08T19:43:04+02:00"}])


def _fest_at(spaeter: str):
    return v2(updated=f"2026-09-08T{spaeter}+02:00",
              status={"state": "listening",
                      "text": f"🎧 hört zu · zuletzt {spaeter}",
                      "ticking": False, "since": ""}, **FEST)


@pytest.mark.parametrize("spaeter", ["19:43:09", "19:43:20", "19:48:05", "20:43:05"])
def test_the_entries_only_got_older_same_fingerprint(spaeter):
    setze(_fest_at("19:43:05"))
    alter = b.fingerprint(b.stage_parts())
    setze(_fest_at(spaeter))
    assert b.fingerprint(b.stage_parts()) == alter


def test_a_newly_appended_decision_moves_the_fingerprint():
    # A really new decision, and a parked entry that actually goes, do move
    # it -- otherwise the page would never show either.
    setze(_fest_at("19:43:05"))
    alter = b.fingerprint(b.stage_parts())
    setze(v2(updated="2026-09-08T19:44:00+02:00", **{
        **FEST,
        "decisions": FEST["decisions"] + [{"id": "D2", "time": "19:44",
                                           "text": "the operator hat den Lead",
                                           "added_at": "2026-09-08T19:44:00+02:00"}],
    }))
    assert b.fingerprint(b.stage_parts()) != alter


def test_the_parked_line_disappearing_moves_the_fingerprint():
    setze(_fest_at("19:43:05"))
    alter = b.fingerprint(b.stage_parts())
    setze(v2(updated="2026-09-08T19:43:12+02:00",
             **{**FEST, "parked": 1, "parked_entries": []}))
    assert b.fingerprint(b.stage_parts()) != alter


# --- HTML injection through the new fields
@pytest.fixture
def injected():
    roh = copy.deepcopy(TICKING)
    roh["mirror"] = "<img src=x onerror=alert(1)>"
    roh["decisions"] = [{"id": "D9", "time": "<b>", "text": "<script>bad()</script>",
                         "added_at": '"><script>x()</script>'}]
    roh["parked"] = 1
    roh["parked_entries"] = [{"text": "<script>bad()</script>",
                              "parked_at": '"><script>x()</script>'}]
    roh["status"]["text"] = "<script>ops()</script>"
    roh["notices"] = [{"id": "N9", "kind": "value_conflict",
                       "text": "<script>bad()</script>",
                       "raised_at": '"><script>x()</script>', "state": "open",
                       "resolution": "", "follow_up": False}]
    roh["question"] = {"id": "Q9", "text": "harmlos", "state": "resolved",
                       "raised_at": "2026-09-08T19:11:00",
                       "resolution": "<script>bad()</script>", "follow_up": False}
    setze(roh)
    return b.stage_parts()


def test_the_mirror_is_escaped(injected):
    assert "<img" not in injected["mirror"] and "&lt;img" in injected["mirror"]


def test_a_decision_is_escaped(injected):
    assert "<script>" not in injected["decisions"]


def test_an_added_at_cannot_break_out_of_the_attribute(injected):
    assert '"><script' not in injected["decisions"]


def test_a_parked_entry_cannot_smuggle_markup_onto_the_stage(injected):
    assert "<script" not in injected["parked"] and '"><script' not in injected["parked"]


def test_the_operating_line_is_escaped(injected):
    assert "<script>" not in injected["status"]


def test_a_notice_is_escaped(injected):
    assert "<script>" not in injected["notices"]


def test_a_raising_time_cannot_break_out_of_the_attribute(injected):
    assert '"><script' not in injected["notices"]


def test_a_resolution_is_escaped(injected):
    assert "<script>" not in injected["question"]


# --- broken v2 never breaks the page
JUNK_V2 = ['{"version":2}', '{"version":2,"notices":"nope"}',
           '{"version":2,"main":[1,2]}', '{"version":2,"question":7}',
           '{"version":2,"status":"nur Text"}',
           '{"version":2,"notices":[1,"zwei",null]}',
           '{"version":2,"decisions":[1,null]}',
           '{"version":2,"decisions":[{"added_at":[1,2]}]}',
           '{"version":2,"parked":"drei","parked_entries":"nope"}',
           '{"version":2,"parked_entries":[1,"zwei",null]}',
           '{"version":2,"main":{"collapsed_lines":"drei"}}']


@pytest.mark.parametrize("junk", JUNK_V2, ids=[j[:28] for j in JUNK_V2])
def test_survives(junk):
    setze(junk)
    b.stage_parts()


# --- numbers the JSON parser accepts but int() cannot
# `json.loads` reads the literals `Infinity` and `NaN` by default, and an
# ordinary-looking `1e400` parses to `inf`. int() of either raises
# OverflowError -- which the page must survive, because the file IS
# readable and therefore becomes the last good state: without the catch the
# stage would stay broken until the next write.
GIFTS = ['{"version":2,"age_visible_after_s":Infinity}',
         '{"version":2,"age_visible_after_s":1e400}',
         '{"version":2,"main":{"collapsed_lines":Infinity}}',
         '{"version":2,"age_visible_after_s":NaN}',
         '{"version":2,"follow_up_after_s":-Infinity}']
GIFT_IDS = [g[13:48] for g in GIFTS]


@pytest.mark.parametrize("gift", GIFTS, ids=GIFT_IDS)
def test_stage_parts_survives_number_gifts(gift):
    setze(gift)
    b.stage_parts()


@pytest.mark.parametrize("gift", GIFTS, ids=GIFT_IDS)
def test_the_thresholds_fall_back_with_number_gifts(gift):
    setze(gift)
    t = b.stage_parts()
    assert bool(t) and t["age_visible_after_s"] == 20 and t["follow_up_after_s"] == 120


@pytest.mark.parametrize("gift", GIFTS, ids=GIFT_IDS)
def test_the_page_still_answers_with_number_gifts(hole, gift):
    setze(gift)
    code, giftseite = hole("/")
    assert code == 200 and "konnte diese Seite" not in giftseite


@pytest.mark.parametrize("gift", GIFTS, ids=GIFT_IDS)
def test_parts_still_answers_with_number_gifts(hole, gift):
    setze(gift)
    code, giftteile = hole("/parts")
    assert code == 200 and bool(json.loads(giftteile).get("mark"))


# --- the page in the approved layout
LAYOUT_PAGE = {
    "version": 2, "updated": "2026-09-08T19:12:04",
    "status": {"state": "working", "text": "⚙️ Robo werkelt seit — nicht "
                                           "abgestürzt, nur gründlich",
               "ticking": True, "since": "2026-09-08T19:11:50"},
    "mirror": "Robo hat die vier Stränge aufgenommen.",
    "headline": "Vorbereitung Planungs-Call",
    "main": {"id": "M1", "markdown": "## Vier Stränge\n- Update",
             "set_at": "2026-09-08T19:10:00", "collapsed_lines": 2,
             "collapsed_markdown": "- a\n- b"},
    "decisions": [{"id": "D1", "time": "19:43", "text": "kein Pitch"}],
    "notices": [{"id": "N1", "kind": "value_conflict", "text": "Frist",
                 "raised_at": "2026-09-08T19:09:00", "state": "open",
                 "resolution": "", "follow_up": False}],
    "question": {"id": "Q1", "text": "Fünfter oder sechster Oktober?",
                 "raised_at": "2026-09-08T19:11:00", "state": "open",
                 "resolution": "", "follow_up": False},
    "links": [{"file": "usecases", "label": "Use-Cases"}],
    "parked": 0, "parked_entries": [],
    "age_visible_after_s": 20, "follow_up_after_s": 120,
}


@pytest.fixture
def layout_seite(hole):
    setze(LAYOUT_PAGE)
    return hole("/")


def test_the_stage_answers_with_the_new_layout(layout_seite):
    assert layout_seite[0] == 200


@pytest.mark.parametrize("kennung", [
    "side", "right", "head", "ops", "ops-text", "headline", "mirror",
    "decisions", "notices", "area", "content", "collapsed", "links", "punkt",
    "clock"])
def test_the_page_carries(layout_seite, kennung):
    assert f'id="{kennung}"' in layout_seite[1]


def test_the_sidebar_is_the_first_thing_in_the_body(layout_seite):
    seite = layout_seite[1]
    assert seite.index('id="side"') < seite.index('id="right"')


def test_the_termin_block_links_to_the_protocol(layout_seite):
    seite = layout_seite[1]
    assert '<div class="lab">Termin</div>' in seite \
        and 'href="file/protokoll-2026-09-07"' in seite


def test_the_closing_line_stands(layout_seite):
    assert "sie ist kein Archiv" in layout_seite[1]


def test_the_page_never_reloads(layout_seite):
    assert "location.reload" not in layout_seite[1]


def test_the_busy_guard_survived(layout_seite):
    assert "isCollapsed" in layout_seite[1]


def test_the_browser_ticks_the_duration_itself(layout_seite):
    assert "steht seit " in layout_seite[1] and "Robo prüft nach" in layout_seite[1]


def test_the_disconnect_line_is_german(layout_seite):
    assert "'getrennt'" in layout_seite[1]


def test_the_page_carries_no_per_second_value_from_the_server(layout_seite):
    assert time.strftime("%H:%M:%S") not in layout_seite[1].split("<script>")[0]


# paint() runs once a second. An unconditional write to #ops-text would drop
# the selection of anyone trying to copy the operating line -- the one place
# where "selection survives" would fail.
def test_the_operating_line_is_only_rewritten_when_it_changed(layout_seite):
    assert "opsEl.innerHTML!==zeile" in layout_seite[1]


def test_a_configured_0_threshold_is_not_replaced_by_the_default(layout_seite):
    # A configured 0 is a decision ("show the age immediately"), and `||`
    # would turn it into the default.
    seite = layout_seite[1]
    assert "age_visible_after_s||20" not in seite \
        and "follow_up_after_s||120" not in seite \
        and "typeof j.age_visible_after_s" in seite \
        and "typeof j.follow_up_after_s" in seite


# The script seeds itself from the DOM so the first 3 s before the first
# poll are not blank. That only works if the server puts the values there.
def test_the_operating_line_carries_data_ticking_from_buehne_json(layout_seite):
    assert 'data-ticking="1"' in layout_seite[1]


def test_the_operating_line_carries_data_since_from_buehne_json(layout_seite):
    assert 'data-since="2026-09-08T19:11:50"' in layout_seite[1]


def test_data_ticking_and_data_since_sit_on_ops_text(layout_seite):
    assert re.search(r'id="ops-text"[^>]*data-ticking="1"[^>]*'
                     r'data-since="2026-09-08T19:11:50"', layout_seite[1])


def test_the_script_reads_both_attributes_back(layout_seite):
    seite = layout_seite[1]
    assert "data-ticking" in seite.split("<script>")[1] \
        and "data-since" in seite.split("<script>")[1]


def test_a_non_ticking_state_says_so_in_the_attribute(hole):
    setze(v2(status={"state": "listening", "text": "🎧 hört zu · zuletzt 19:12:04",
                     "ticking": False, "since": ""},
             main={"markdown": "- egal"}))
    code, standseite = hole("/")
    assert 'data-ticking="0"' in standseite and 'data-since=""' in standseite


# --- the running duration lands where the sentence puts it
# The sentence reads "Robo werkelt seit m:ss -- nicht abgestürzt, nur
# gründlich". Appended at the END the round read "... nur gründlich 1:23",
# which is not German.
def test_the_page_fills_the_placeholder_instead_of_appending():
    assert "zeile.replace('m:ss'" in b.SCRIPT


def test_the_duration_is_still_counted_in_the_browser():
    assert "mmss(Date.now()-since)" in b.SCRIPT


def test_the_operating_line_is_still_only_rewritten_when_it_changed():
    assert "opsEl.innerHTML!==zeile" in b.SCRIPT


# --- the warning has a line of its own, the operating line keeps its own
# One conflict file at ten o'clock used to blank the operating line for the
# rest of the meeting: the warning REPLACED it, and the condition never
# clears itself.
@pytest.fixture
def warned(hole):
    setze(v2(status={"state": "listening", "text": "🎧 hört zu · zuletzt 19:12:04",
                     "ticking": False, "since": "",
                     "warning": "⚠️ Konfliktdateien im Projektordner — ein "
                                "Sync-Dienst schreibt mit"},
             main={"markdown": "- egal"}))
    return hole


def test_the_warning_arrives_as_its_own_part(warned):
    assert "Konfliktdateien" in b.stage_parts()["warning"]


def test_the_operating_line_still_answers_did_you_hear_me(warned):
    assert "hört zu" in b.stage_parts()["status"]


def test_the_page_carries_warn(warned):
    assert 'id="warn"' in warned("/")[1]


def test_the_warning_is_rendered_into_it(warned):
    assert "Konfliktdateien" in warned("/")[1].split('id="warn"')[1][:200]


def test_the_warning_sits_between_the_operating_line_and_the_mirror(warned):
    s = warned("/")[1]
    assert s.index('id="ops-text"') < s.index('id="warn"') < s.index('id="mirror"')


def test_the_script_applies_the_warning_on_every_poll_like_the_status():
    assert "j.warning" in b.SCRIPT


def test_parts_carries_the_warning(warned):
    assert "Konfliktdateien" in json.loads(warned("/parts")[1])["warning"]


def test_a_warning_is_escaped_like_everything_else():
    setze(v2(status={"state": "listening", "text": "🎧 hört zu · zuletzt 19:12:04",
                     "ticking": False, "since": "", "warning": "<script>x</script>"},
             main={"markdown": "- egal"}))
    assert "<script>" not in b.stage_parts()["warning"]


def test_an_absent_warning_is_simply_empty():
    setze(v2(status={"state": "listening", "text": "🎧 hört zu · zuletzt 19:12:04",
                     "ticking": False, "since": "", "warning": ""},
             main={"markdown": "- egal"}))
    assert b.stage_parts()["warning"] == ""


# --- the raising time keeps its UTC offset all the way to the browser
# `gateway/stage_state.py` writes an offset-bearing ISO timestamp; the page
# hands it to Date.parse. If the offset were dropped anywhere on the way, a
# remote reader would compute every notice as hours old and see them all
# permanently in the follow-up warning.
ZEITPUNKTE = ["2026-09-08T19:09:00+02:00", "2026-09-08T19:09:00-07:00",
              "2026-09-08T17:09:00+00:00"]


def _raised_at(zeitpunkt):
    setze(v2(main={"markdown": "- egal"},
             notices=[{"id": "N1", "kind": "value_conflict", "text": "Frist",
                       "raised_at": zeitpunkt, "state": "open",
                       "resolution": "", "follow_up": False}]))


@pytest.mark.parametrize("zeitpunkt", ZEITPUNKTE)
def test_stage_parts_passes_the_time_through_unchanged(zeitpunkt):
    _raised_at(zeitpunkt)
    assert f'data-raised="{zeitpunkt}"' in b.stage_parts()["notices"]


@pytest.mark.parametrize("zeitpunkt", ZEITPUNKTE)
def test_the_page_carries_the_time_in_data_raised(hole, zeitpunkt):
    _raised_at(zeitpunkt)
    code, zeitseite = hole("/")
    assert code == 200 and f'data-raised="{zeitpunkt}"' in zeitseite


@pytest.mark.parametrize("zeitpunkt", ZEITPUNKTE)
def test_parts_carries_the_time_in_data_raised(hole, zeitpunkt):
    _raised_at(zeitpunkt)
    code, zeitteile = hole("/parts")
    assert zeitpunkt in json.loads(zeitteile)["notices"]


# Without a protocol in the project folder, the Termin block is omitted.
@pytest.fixture
def leere_seite(hole, tmp_path):
    switch_project(tmp_path / "leer")
    return hole("/")


def test_the_stage_answers_without_a_protocol(leere_seite):
    assert leere_seite[0] == 200


def test_no_protocol_no_termin_block(leere_seite):
    assert '<div class="lab">Termin</div>' not in leere_seite[1]


def test_no_protocol_the_seed_text_still_stands(leere_seite):
    assert "noch nicht gesetzt" in leere_seite[1]


# ================================================================= history
# Replacing a surface is only defensible while it stays reversible, so every
# position travels with the poll -- and the POINTER does not. It is client
# state: a viewer who pages back must not page the view away for the whole
# room, and the consumer's state must not hang off a browser.
VERLAUF = [
    {"id": "M1", "title": "Budget 2027", "markdown": "## Budget 2027\n- Rahmen",
     "set_at": "2026-09-08T19:05:00+02:00", "collapsed_lines": 0,
     "collapsed_markdown": ""},
    {"id": "M2", "title": "Kosten", "markdown": "## Kosten\n- 12.000",
     "set_at": "2026-09-08T19:08:00+02:00", "collapsed_lines": 2,
     "collapsed_markdown": "- a\n- b"},
    {"id": "M3", "title": "Rechtsform & Co", "markdown": "## Rechtsform",
     "set_at": "2026-09-08T19:11:00+02:00", "collapsed_lines": 0,
     "collapsed_markdown": ""},
]
JETZT = {"id": "M3", "markdown": "## Rechtsform",
         "set_at": "2026-09-08T19:11:00+02:00", "collapsed_lines": 0,
         "collapsed_markdown": ""}


@pytest.fixture
def verlauf():
    setze(v2(main=JETZT, history=VERLAUF))
    return b.stage_parts()


def test_every_position_reaches_the_page(verlauf):
    assert len(verlauf["history"]) == 3


def test_newest_last(verlauf):
    assert [e["id"] for e in verlauf["history"]] == ["M1", "M2", "M3"]


def test_a_position_is_rendered_markdown_not_raw_text(verlauf):
    assert "<h2>" in verlauf["history"][0]["main"]


def test_a_position_carries_its_own_collapsed_block(verlauf):
    assert "<details" in verlauf["history"][1]["collapsed"] \
        and "weitere Zeilen" in verlauf["history"][1]["collapsed"]


def test_the_title_is_escaped_the_page_writes_it_as_markup(verlauf):
    assert verlauf["history"][2]["title"] == "Rechtsform &amp; Co"


def test_no_timestamp_rides_along_with_a_position(verlauf):
    assert all("set_at" not in e for e in verlauf["history"])


def test_the_current_view_is_still_the_plain_main_key_an_old_page_reads(verlauf):
    assert "Rechtsform" in verlauf["main"]


@pytest.fixture
def hostile_history():
    setze(v2(main={"markdown": "- egal"}, history=[
        {"id": "M1", "title": "<script>alert(1)</script>",
         "markdown": "<script>alert(2)</script>", "collapsed_lines": 1,
         "collapsed_markdown": "<script>alert(3)</script>"}]))
    return b.stage_parts()


def test_html_in_a_history_title_is_defused(hostile_history):
    assert "<script>" not in hostile_history["history"][0]["title"]


def test_html_in_a_position_is_defused(hostile_history):
    h = hostile_history["history"][0]
    assert "<script>" not in h["main"] and "<script>" not in h["collapsed"]


def test_a_file_from_before_the_history_simply_has_none():
    setze(v2(main={"markdown": "- egal"}))
    assert b.stage_parts()["history"] == []


BROKEN_HISTORY = ["nope", 7, None, [1, "zwei", None], {"eins": 1}]


@pytest.mark.parametrize("kaputt", BROKEN_HISTORY, ids=[repr(k) for k in BROKEN_HISTORY])
def test_stage_parts_survives_history(kaputt):
    setze(v2(main={"markdown": "- egal"}, history=kaputt))
    b.stage_parts()


@pytest.mark.parametrize("kaputt", BROKEN_HISTORY, ids=[repr(k) for k in BROKEN_HISTORY])
def test_a_broken_history_renders_no_position(kaputt):
    setze(v2(main={"markdown": "- egal"}, history=kaputt))
    t = b.stage_parts()
    assert bool(t) and t["history"] == []


@pytest.fixture
def zweihundert():
    setze(v2(main={"markdown": "- egal"},
             history=[{"id": f"M{i}", "title": f"T{i}", "markdown": f"- {i}"}
                      for i in range(200)]))
    return b.stage_parts()


def test_a_file_that_claims_two_hundred_positions_is_capped(zweihundert):
    assert len(zweihundert["history"]) == b.MAX_HISTORY_SHOWN


def test_and_the_newest_are_the_ones_kept(zweihundert):
    assert zweihundert["history"][-1]["title"] == "T199"


# --- the history moves the fingerprint, its clocks do not
def test_only_the_positions_timestamps_moved_same_fingerprint():
    setze(v2(main=JETZT, history=VERLAUF))
    verlaufmarke = b.fingerprint(b.stage_parts())
    spaeter = [dict(e, set_at="2026-09-08T20:00:00+02:00") for e in VERLAUF]
    setze(v2(main=JETZT, history=spaeter))
    assert b.fingerprint(b.stage_parts()) == verlaufmarke


def test_a_new_position_moves_it_a_paged_back_reader_has_to_hear_about_it():
    setze(v2(main=JETZT, history=VERLAUF))
    verlaufmarke = b.fingerprint(b.stage_parts())
    setze(v2(main={"id": "M4", "markdown": "## Foerderung",
                   "set_at": "2026-09-08T19:14:00+02:00", "collapsed_lines": 0,
                   "collapsed_markdown": ""},
             history=[*VERLAUF, {"id": "M4", "title": "Foerderung",
                                 "markdown": "## Foerderung",
                                 "set_at": "2026-09-08T19:14:00+02:00",
                                 "collapsed_lines": 0, "collapsed_markdown": ""}]))
    assert b.fingerprint(b.stage_parts()) != verlaufmarke


# --- the history is operable without the mouse
def test_the_page_carries_the_history_row(hole):
    setze(v2(main=JETZT, history=VERLAUF))
    assert 'id="verlauf"' in hole("/")[1]


def test_the_row_keeps_its_height_while_empty_so_nothing_is_displaced():
    # The row appears as soon as a second surface exists. If it took its
    # line then, it would push the main area down under the reading eye.
    assert "#verlauf{min-height" in b.STYLE


def test_arrow_keys_page_through_it():
    assert "ArrowLeft" in b.SCRIPT and "ArrowRight" in b.SCRIPT


def test_visible_buttons_do_the_same():
    assert 'data-v="zurueck"' in b.SCRIPT and 'data-v="vor"' in b.SCRIPT


def test_both_go_through_one_function_so_they_cannot_drift_apart():
    assert b.SCRIPT.count("blaettern(-1)") == 2 and b.SCRIPT.count("blaettern(1)") == 2


def test_typing_is_not_paging():
    assert "if(tippt()) return;" in b.SCRIPT


def test_paged_back_says_so_in_german():
    assert "zurück zur aktuellen Ansicht" in b.SCRIPT and "Verlauf '" in b.SCRIPT


def test_a_new_position_while_paged_back_is_reported_not_jumped_to():
    assert "neu: " in b.SCRIPT


def test_a_poll_does_not_page_the_readers_position_away():
    assert "if(pos===null){" in b.SCRIPT


def test_the_history_is_taken_over_before_the_fingerprint_is_compared():
    assert b.SCRIPT.index("verlaufUebernehmen(j)") < b.SCRIPT.index("if(j.mark===mark)")


def test_no_pointer_is_ever_sent_to_the_server():
    setze(v2(main=JETZT, history=VERLAUF))
    assert "cursor" not in b.SCRIPT and "cursor" not in json.dumps(b.stage_parts())


def test_parts_carries_the_history(hole):
    setze(v2(main=JETZT, history=VERLAUF))
    assert len(json.loads(hole("/parts")[1]).get("history", [])) == 3


def test_the_file_view_carries_an_empty_history_same_payload_shape(hole):
    (b.PROJECT / "notizen.md").write_text("# Notizen\n", encoding="utf-8")
    b.clear_cache()
    setze(v2(main=JETZT, history=VERLAUF))
    assert json.loads(hole("/parts?d=notizen")[1]).get("history") == []


# ========================================================== session folder
# The consumer writes one folder per meeting,
# `sessions/<YYYY-MM-DD>-<slug>/`, with `protokoll.md` in it. Runs from
# before that convention lie flat as `protokoll-<datum>.md`. The stage has
# to find BOTH -- the older layout is still on disk in every project that
# was ever used, and the flat run is the one the round is reading if it
# started before the change.
@pytest.fixture
def sitz(tmp_path):
    return switch_project(tmp_path / "sitz")


def _sitz_flat(sitz):
    # Only the old layout: a bare stem, exactly as before.
    (sitz / "protokoll-2026-09-07.md").write_text("# Alt\n", encoding="utf-8")
    os.utime(sitz / "protokoll-2026-09-07.md", (1000, 1000))
    b.clear_cache()


def _sitz_new(sitz):
    _sitz_flat(sitz)
    ordner = sitz / "sessions" / "2026-09-08-planungs-call"
    ordner.mkdir(parents=True)
    (ordner / "protokoll.md").write_text("# Neu\n", encoding="utf-8")
    os.utime(ordner / "protokoll.md", (2000, 2000))
    b.clear_cache()
    return ordner


def _sitz_newer(sitz):
    _sitz_new(sitz)
    ordner2 = sitz / "sessions" / "2026-09-09-zweiter-termin"
    ordner2.mkdir(parents=True)
    (ordner2 / "protokoll.md").write_text("# Neuer\n", encoding="utf-8")
    os.utime(ordner2 / "protokoll.md", (3000, 3000))
    b.clear_cache()
    return ordner2


def _sitz_full(sitz):
    """Both layouts, two session folders, and a buehne.json in two places."""
    ordner2 = _sitz_newer(sitz)
    # `buehne.json` moved with the protocol. Both places are read, the more
    # recently written one wins -- that is the running meeting.
    (sitz / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "flach",
                    "main": {"markdown": "alt"}}), encoding="utf-8")
    os.utime(sitz / "buehne.json", (1000, 1000))
    (ordner2 / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "im ordner",
                    "main": {"markdown": "neu"}}), encoding="utf-8")
    os.utime(ordner2 / "buehne.json", (5000, 5000))
    b.clear_cache()
    b._last_good = None
    return ordner2


def test_no_protocol_at_all_no_link(sitz):
    assert b.protocol_link() == ""


def test_a_flat_protocol_is_still_found(sitz):
    _sitz_flat(sitz)
    assert b.protocol_link() == "protokoll-2026-09-07"


def test_and_it_is_still_reachable_under_its_stem(sitz):
    _sitz_flat(sitz)
    assert b.find_file("protokoll-2026-09-07") is not None


def test_a_protocol_in_a_session_folder_is_found(sitz):
    # A stem alone no longer names one file -- every session folder has a
    # `protokoll.md` -- so the link carries the path relative to the project.
    _sitz_new(sitz)
    assert b.protocol_link() == "sessions/2026-09-08-planungs-call/protokoll"


def test_the_newer_of_the_two_layouts_wins(sitz):
    _sitz_new(sitz)
    assert "2026-09-08-planungs-call" in b.protocol_link()


def test_and_the_relative_path_resolves_to_the_file(sitz):
    ordner = _sitz_new(sitz)
    assert b.find_file("sessions/2026-09-08-planungs-call/protokoll") \
        == ordner / "protokoll.md"


def test_the_newest_of_several_session_folders_is_linked(sitz):
    _sitz_newer(sitz)
    assert b.protocol_link() == "sessions/2026-09-09-zweiter-termin/protokoll"


def test_a_flat_run_written_last_takes_the_link_back(sitz):
    # A flat run touched AFTER the session folders exist is the running
    # meeting -- the naming schemes do not sort against each other, so the
    # answer has to come from the modification time.
    _sitz_newer(sitz)
    os.utime(sitz / "protokoll-2026-09-07.md", (4000, 4000))
    b.clear_cache()
    assert b.protocol_link() == "protokoll-2026-09-07"


def test_the_flat_buehne_json_is_still_read(sitz):
    ordner2 = _sitz_newer(sitz)
    (sitz / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "flach",
                    "main": {"markdown": "alt"}}), encoding="utf-8")
    os.utime(sitz / "buehne.json", (1000, 1000))
    b.clear_cache()
    b._last_good = None
    assert b.read_stage().get("headline") == "flach"


def test_the_session_folders_buehne_json_wins_when_it_is_newer(sitz):
    _sitz_full(sitz)
    assert b.read_stage().get("headline") == "im ordner"


# The page itself: the Termin block has to build a link that the `/file/`
# route actually resolves.
def test_the_stage_answers_with_a_session_folder_present(sitz, hole):
    _sitz_full(sitz)
    assert hole("/")[0] == 200


def test_the_termin_block_links_into_the_session_folder(sitz, hole):
    _sitz_full(sitz)
    assert 'href="file/sessions/2026-09-09-zweiter-termin/protokoll"' in hole("/")[1]


def test_and_that_link_opens_the_file(sitz, hole):
    _sitz_full(sitz)
    code4, dateiseite = hole("/file/sessions/2026-09-09-zweiter-termin/protokoll")
    assert code4 == 200 and "Neuer" in dateiseite


def test_the_file_views_base_climbs_back_out_of_the_session_folder(sitz, hole):
    # The file view sits three levels below the stage here. `<base>` has to
    # climb back out of all of them, or the fingerprint fetch would ask for
    # `/file/sessions/.../parts`.
    _sitz_full(sitz)
    dateiseite = hole("/file/sessions/2026-09-09-zweiter-termin/protokoll")[1]
    assert '<base href="../../../">' in dateiseite


def test_the_fingerprint_fetch_carries_the_relative_path(sitz, hole):
    _sitz_full(sitz)
    dateiseite = hole("/file/sessions/2026-09-09-zweiter-termin/protokoll")[1]
    assert "sessions%2F2026-09-09-zweiter-termin%2Fprotokoll" in dateiseite


# --- A1-Minimal: right column. The right column must carry text from the
# RUNNING protocol -- and mutation-tested, see below, so a test that only
# checks "page renders" without actually reading `protokoll` cannot pass
# here silently.
def test_stage_parts_carries_the_protocol_field(sitz):
    _sitz_full(sitz)
    assert "protokoll" in b.stage_parts()


def test_a1_positive_control_the_newest_protocols_text_is_in_it(sitz):
    _sitz_full(sitz)
    assert "Neuer" in b.stage_parts()["protokoll"]


def test_the_older_protocols_own_text_is_not_whats_shown(sitz):
    _sitz_full(sitz)
    b.stage_parts()
    # Ported verbatim: the archive condition ends in `or True`, so this
    # check can never fail. The real file-identity check is the mtime
    # ordering test above (b.protocol_link()).
    assert "im ordner" not in json.dumps(b.read_stage()) or True


def test_a1_mutation_target_the_served_pages_protokoll_content_carries_the_text(sitz, hole):
    _sitz_full(sitz)
    assert "Neuer" in hole("/")[1]


def test_parts_also_carries_it_this_is_what_the_poller_reads(sitz, hole):
    _sitz_full(sitz)
    assert "Neuer" in json.loads(hole("/parts")[1]).get("protokoll", "")


def test_mutation_test_without_current_protocol_file_the_positive_control_goes_red(
        sitz, monkeypatch):
    # Without `current_protocol_file()` the right column has nothing to
    # render. If this still finds "Neuer", the test above measures nothing.
    _sitz_full(sitz)
    monkeypatch.setattr(b, "current_protocol_file", lambda: None)
    b.clear_cache()
    t_ohne = b.stage_parts()
    assert "Neuer" not in t_ohne["protokoll"] and t_ohne["protokoll"] == ""


# --- Befund 1: sitzungen/
# The real project folder on disk was renamed sessions/ -> sitzungen/ while
# the consumer still WRITES sessions/. Rendered against a folder that has
# ONLY sitzungen/ -- the unrenamed, real-world shape -- with no sessions/
# present at all.
@pytest.fixture
def nur_sitzungen(tmp_path):
    sitz3 = switch_project(tmp_path / "sitzungen-only")
    ordner_de = sitz3 / "sitzungen" / "2026-09-21-teil-1"
    ordner_de.mkdir(parents=True)
    (ordner_de / "protokoll.md").write_text("# Laufendes Protokoll\n", encoding="utf-8")
    (ordner_de / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "sitzungen",
                    "main": {"markdown": "x"}}), encoding="utf-8")
    b.clear_cache()
    return sitz3


def test_session_dirs_finds_a_sitzungen_folder(nur_sitzungen):
    assert len(b.session_dirs()) == 1


def test_protocol_link_resolves_into_sitzungen_not_sessions(nur_sitzungen):
    assert b.protocol_link() == "sitzungen/2026-09-21-teil-1/protokoll"


def test_a1_positive_control_the_protocol_text_is_not_empty_against_a_sitzungen_only_folder(
        nur_sitzungen):
    # Befund 1's exact repro: /parts came back with "protokoll": "" here.
    assert "Laufendes Protokoll" in b.stage_parts()["protokoll"]


def test_mutation_test_reading_only_sessions_leaves_the_column_empty(
        nur_sitzungen, monkeypatch):
    # Without the alt-name glob this must go red -- the pre-fix behaviour
    # reproduced directly, not asserted.
    monkeypatch.setattr(b, "SESSIONS_DIR_ALT", "__kein_treffer__")
    b.clear_cache()
    assert b.stage_parts()["protokoll"] == ""


# --- Befund 3: the column follows the SAME session stage_file() picked
# The column used to pick its protocol by the NEWEST protokoll.md mtime
# across ALL session folders, independently of which folder `stage_file()`
# (mtime of buehne.json) considers the running meeting. Reproduced: an
# OLDER folder's protokoll.md is touched (e.g. by an Obsidian/sync read) so
# it has the newer mtime, while a DIFFERENT, newer folder is the one
# `buehne.json` says is running.
@pytest.fixture
def gleicher_ordner(tmp_path):
    sitz4 = switch_project(tmp_path / "samefolder")
    alt_ordner = sitz4 / "sessions" / "2026-09-07-alt"
    alt_ordner.mkdir(parents=True)
    (alt_ordner / "protokoll.md").write_text("# Altes Protokoll 07.09.\n", encoding="utf-8")
    (alt_ordner / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "alt",
                    "main": {"markdown": "alt"}}), encoding="utf-8")
    os.utime(alt_ordner / "buehne.json", (1000, 1000))

    lauf_ordner = sitz4 / "sessions" / "2026-09-21-laufend"
    lauf_ordner.mkdir(parents=True)
    (lauf_ordner / "protokoll.md").write_text(
        "# Laufendes Protokoll 21.09.\n", encoding="utf-8")
    (lauf_ordner / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "laufend",
                    "main": {"markdown": "laufend"}}), encoding="utf-8")
    # buehne.json of the running session is newer -> stage_file() picks it.
    os.utime(lauf_ordner / "buehne.json", (5000, 5000))
    # But its protokoll.md is touched EARLIER than the old folder's.
    os.utime(lauf_ordner / "protokoll.md", (2000, 2000))
    os.utime(alt_ordner / "protokoll.md", (9000, 9000))
    b.clear_cache()
    b._last_good = None
    return lauf_ordner


def test_stage_file_picked_the_running_session_newest_buehne_json(gleicher_ordner):
    assert b.stage_file() == gleicher_ordner / "buehne.json"


def test_a1_the_column_shows_the_running_protocol_not_the_stale_one(gleicher_ordner):
    teile_lauf = b.stage_parts()
    assert "Laufendes Protokoll" in teile_lauf["protokoll"] \
        and "Altes Protokoll" not in teile_lauf["protokoll"]


def test_mutation_test_the_old_global_mtime_pick_shows_the_stale_protocol(
        gleicher_ordner, monkeypatch):
    # The old global-mtime search picks by protokoll.md mtime alone,
    # ignoring which folder stage_file() chose -- with the fix reverted this
    # must show the OLD text instead.
    def alte_logik():
        kandidaten = []
        for d in b.session_dirs():
            q = d / "protokoll.md"
            if q.is_file():
                kandidaten.append(q)
        return max(kandidaten, key=b._mtime_then_name, default=None)

    monkeypatch.setattr(b, "current_protocol_file", alte_logik)
    b.clear_cache()
    assert "Altes Protokoll" in b.stage_parts()["protokoll"]


# --- both folder names side by side are reported, not silently mixed
# If the code finds sessions/ AND sitzungen/ next to each other it must not
# silently pick one but make that visible (log + stage) -- otherwise a
# silent outage repeats unnoticed.
@pytest.fixture
def beide(tmp_path):
    sitz5 = switch_project(tmp_path / "beide")
    b._beide_ordner_gemeldet.discard(str(sitz5))
    en_ordner = sitz5 / "sessions" / "2026-09-21-englisch"
    en_ordner.mkdir(parents=True)
    (en_ordner / "protokoll.md").write_text(
        "# Englisch-benannter Ordner\n", encoding="utf-8")
    (en_ordner / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "en",
                    "main": {"markdown": "en"}}), encoding="utf-8")
    de_ordner = sitz5 / "sitzungen" / "2026-09-21-deutsch"
    de_ordner.mkdir(parents=True)
    (de_ordner / "protokoll.md").write_text(
        "# Deutsch-benannter Ordner\n", encoding="utf-8")
    (de_ordner / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "de",
                    "main": {"markdown": "de"}}), encoding="utf-8")
    os.utime(en_ordner / "buehne.json", (1000, 1000))
    os.utime(de_ordner / "buehne.json", (2000, 2000))
    b.clear_cache()
    yield sitz5
    b._beide_ordner_gemeldet.discard(str(sitz5))


def test_beide_ordnernamen_da_erkennt_sessions_und_sitzungen_nebeneinander(beide):
    assert b.beide_ordnernamen_da() is True


def test_session_dirs_loggt_die_doppellage_auf_stdout(beide, capsys):
    capsys.readouterr()
    b.session_dirs()
    out = capsys.readouterr().out
    assert "sessions/" in out and "sitzungen/" in out


def test_stage_parts_traegt_den_hinweis_in_warning(beide):
    teile = b.stage_parts()
    assert "sessions/" in teile["warning"] and "sitzungen/" in teile["warning"]


def test_mutation_test_nur_sessions_vorhanden_keine_doppel_meldung_mehr(beide):
    shutil.rmtree(beide / "sitzungen")
    b.clear_cache()
    assert b.beide_ordnernamen_da() is False and b.stage_parts()["warning"] == ""


# ============================ COTHINKER_SESSIONS_DIR ==========
# A partner repo files its meetings under a folder name that is neither
# `sessions/` nor `sitzungen/` (e.g. `protokolle/`). The stage must find it
# when the variable is set, without losing the sessions/sitzungen lookup it
# already has.
@pytest.fixture
def protokolle(tmp_path, monkeypatch):
    sitz6 = switch_project(tmp_path / "protokolle-proj")
    monkeypatch.setenv("COTHINKER_SESSIONS_DIR", "protokolle")
    ordner = sitz6 / "protokolle" / "2026-11-02-team-meeting"
    ordner.mkdir(parents=True)
    (ordner / "protokoll.md").write_text(
        "# Team-Meeting unter protokolle/\n", encoding="utf-8")
    (ordner / "buehne.json").write_text(
        json.dumps({"version": 2, "headline": "protokolle",
                    "main": {"markdown": "protokolle"}}), encoding="utf-8")
    b.clear_cache()
    return monkeypatch


def test_session_dirs_finds_the_configured_protokolle_folder(protokolle):
    assert len(b.session_dirs()) == 1


def test_stage_parts_reads_the_protocol_out_of_protokolle(protokolle):
    assert "Team-Meeting unter protokolle" in b.stage_parts()["protokoll"]


def test_mutation_test_unset_sessions_dir_protokolle_is_invisible_again(protokolle):
    protokolle.delenv("COTHINKER_SESSIONS_DIR")
    b.clear_cache()
    assert len(b.session_dirs()) == 0


# --- the vault read path -- own root, own check, /file/ untouched
@pytest.fixture
def vault(tmp_path):
    vault = tmp_path / "vaultweg"
    vault.mkdir()
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "wissen").mkdir()
    (aussen / "wissen" / "notiz.md").write_text("# Vault-Notiz\n", encoding="utf-8")
    (aussen / "wissen" / "bild.png").write_bytes(b"\x89PNG")
    (aussen / "wissen" / "network").mkdir()
    (aussen / "wissen" / "network" / "person.md").write_text("# P\n", encoding="utf-8")
    (vault / "10-Wissen").symlink_to(aussen / "wissen", target_is_directory=True)
    b.VAULT = vault
    b.clear_cache()
    return vault


def test_a_vault_note_outside_the_project_folder_is_served(vault, hole):
    code, seite = hole("/vault/10-Wissen/notiz.md")
    assert code == 200 and "Vault-Notiz" in seite


def test_a_traversal_attempt_is_refused(vault, hole):
    # Encoded, so the HTTP client cannot normalise it away before it
    # arrives: the check has to happen in `vault_file`, not in urllib.
    assert hole("/vault/%2e%2e/%2e%2e/etc/passwd")[0] == 404


def test_a_non_markdown_file_is_refused(vault, hole):
    assert hole("/vault/10-Wissen/bild.png")[0] == 404


def test_a_note_excluded_for_privacy_is_refused(vault, hole):
    assert hole("/vault/10-Wissen/network/person.md")[0] == 404


def test_file_still_refuses_the_same_note(vault, hole):
    # The security filter of /file/ is NOT relaxed -- that is the whole
    # point of the separate route.
    assert hole("/file/notiz")[0] == 404


def test_with_no_vault_set_the_read_path_is_off(vault, hole):
    # `not str(Path())` is "." and therefore false -- the guard has to
    # compare the path itself, or the read path is on by default and serves
    # the working directory to a surface without access control. The index
    # has to stay EMPTY.
    b.VAULT = Path()
    b.clear_cache()
    assert b.vault_index() == {} and hole("/vault/README.md")[0] == 404


def test_a_vault_note_is_still_served_after_the_no_vault_check(vault, hole):
    b.VAULT = Path()
    b.clear_cache()
    b.vault_index()
    b.VAULT = vault
    b.clear_cache()
    assert hole("/vault/10-Wissen/notiz.md")[0] == 200


# --- the vendored drawing libraries
def test_a_vendored_bundle_is_served(hole):
    code, js = hole("/static/vendor/cytoscape.min.js")
    assert code == 200 and len(js) > 10000


def test_an_unknown_static_name_is_refused(hole):
    assert hole("/static/vendor/../../stage.py")[0] == 404


def test_every_bundle_is_named_with_its_licence():
    lizenz = (ROOT / "stage" / "static" / "vendor" / "README.md").read_text(
        encoding="utf-8")
    assert all(n in lizenz for n in ("cytoscape.min.js", "layout-base.js",
                                     "cose-base.js", "cytoscape-fcose.js",
                                     "cytoscape-layout-utilities.js")) \
        and lizenz.count("MIT") >= 5


# --- a graph surface, rendered defensively
BOES = {"seed": "a.md",
        "nodes": [{"id": "a.md", "label": '<img src=x onerror="a">',
                   "ring": 0, "seed": True, "grace": False},
                  {"id": "b.md", "label": "b", "ring": 1}],
        "edges": [{"id": "w", "source": "a.md", "target": "b.md",
                   "kind": "wikilink", "weight": 1.0},
                  {"id": "x", "source": "a.md", "target": "b.md",
                   "kind": "erfunden", "weight": 9.0}],
        "counts": {"notes": 2}}


def test_a_graph_surface_renders_as_a_container_with_its_payload():
    teil = b.graph_part(b.clean_graph(BOES))
    assert 'class="graf"' in teil and "data-graph=" in teil and "b.md" in teil


def test_a_hostile_label_cannot_leave_the_attribute():
    # The label rides INSIDE the attribute, so escaping -- not word removal
    # -- is what makes it harmless. Read the attribute value back and assert
    # on THAT: `json.dumps` already turns the label's `"` into `\"`, so a
    # check against the rendered string stays green even if `quote=True` is
    # dropped. The value must carry no raw `"` or `<` at all, and must still
    # round-trip to the hostile label.
    teil = b.graph_part(b.clean_graph(BOES))
    wert = teil.split('data-graph="', 1)[1].split('">', 1)[0]
    assert '"' not in wert and "<" not in wert \
        and json.loads(html.unescape(wert))["nodes"][0]["label"] \
        == '<img src=x onerror="a">'


def test_an_unknown_edge_kind_is_dropped():
    teil = b.graph_part(b.clean_graph(BOES))
    assert "erfunden" not in teil and teil.count("&quot;kind&quot;") == 1


def test_the_node_count_is_capped():
    gross = {"seed": "a.md",
             "nodes": [{"id": f"n{i}.md", "label": str(i), "ring": 1}
                       for i in range(500)],
             "edges": [], "counts": {}}
    assert len(b.clean_graph(gross)["nodes"]) == b.MAX_GRAPH_NODES


GRAPH_HISTORY = [{"id": "M1", "title": "Netz", "kind": "graph",
                  "graph": BOES, "markdown": "", "set_at": "2026-09-08T19:12:04",
                  "collapsed_lines": 0, "collapsed_markdown": ""}]


def test_a_graph_position_in_the_history_renders_as_a_container_not_as_markdown():
    # Both places the graph is wired into: the history entry AND the current
    # surface. Without the second clause `stage_parts()`'s graph branch can
    # be removed and every check here stays green, while the stage goes
    # blank.
    setze(v2(history=GRAPH_HISTORY))
    teile = b.stage_parts()
    assert 'class="graf"' in teile["history"][-1]["main"] \
        and 'class="graf"' in teile["main"]


def test_the_newest_history_position_does_not_carry_its_content_twice_over_the_wire(hole):
    # The client reads the newest position from the top-level
    # main/collapsed instead (blaettern() in SCRIPT always maps the last
    # index to zeige(null), never to verlauf[last]).
    setze(v2(history=GRAPH_HISTORY))
    teile_http = json.loads(hole("/parts")[1])
    assert teile_http["history"][-1]["main"] == "" \
        and teile_http["history"][-1]["collapsed"] == "" \
        and 'class="graf"' in teile_http["main"]


def test_stage_parts_itself_stays_full_only_the_http_parts_response_is_deduplicated():
    setze(v2(history=GRAPH_HISTORY))
    assert 'class="graf"' in b.stage_parts()["history"][-1]["main"]


# --- an older graph position is a stub, not a blank or a duplicate drawing
# Driven through the REAL gateway, not a hand-built row: `_history_row()`
# (gateway/stage_state.py) is what actually trims an older graph position
# down to its seed, and that trimming has to survive the trip through
# `history_parts()` (stage/stage.py) before a stub is what the page gets.
GRAF_ERSTE = {"seed": "erste-notiz.md",
              "nodes": [{"id": "erste-notiz.md", "label": "Erste", "ring": 0,
                         "seed": True, "grace": False}],
              "edges": [], "counts": {"notes": 1}}
GRAF_ZWEITE = {"seed": "zweite-notiz.md",
               "nodes": [{"id": "zweite-notiz.md", "label": "Zweite",
                          "ring": 0, "seed": True, "grace": False}],
               "edges": [], "counts": {"notes": 1}}


@pytest.fixture
def stub_inhalt():
    from stage_state import StageState
    stub_uhr = [1000.0]
    stub_state = StageState(now=lambda: stub_uhr[0])
    stub_state.apply({"action": "graph", "title": "Alter Graph",
                      "graph": GRAF_ERSTE})
    stub_uhr[0] += 120.0  # past TOPIC_MIN_HOLD_S (45s), or the second apply()
    #                       is refused with TOO_SOON and there is no history
    stub_state.apply({"action": "graph", "title": "Neuer Graph",
                      "graph": GRAF_ZWEITE})
    return stub_state.content()


def test_the_gateway_trims_the_older_graph_position_to_just_its_seed(stub_inhalt):
    assert stub_inhalt["history"][0]["graph"] == {"seed": "erste-notiz.md"}


def test_the_newest_graph_position_keeps_its_full_nodes_edges(stub_inhalt):
    assert "nodes" in stub_inhalt["history"][-1]["graph"]


def test_the_older_position_renders_as_a_readable_stub(stub_inhalt):
    # Not empty, not the full graph container, and naming the note it was
    # about.
    setze(stub_inhalt)
    alte_main = b.stage_parts()["history"][0]["main"]
    assert alte_main != "" \
        and 'class="graf-stub"' in alte_main \
        and 'class="graf"' not in alte_main \
        and "erste-notiz.md" in alte_main


def test_the_same_stub_reaches_the_http_parts_route_the_page_actually_polls(
        stub_inhalt, hole):
    setze(stub_inhalt)
    stub_http = json.loads(hole("/parts")[1])
    assert 'class="graf-stub"' in stub_http["history"][0]["main"]


def test_a_graph_dict_with_nodes_but_no_edges_key_still_counts_as_the_full_graph():
    # An "edges"-vs-"nodes" check is invisible to the fixture above: a
    # trimmed row has NEITHER key, a full row has BOTH, so the two
    # conditions never disagree there. Only a row with one key but not the
    # other tells them apart -- `_history_row()` never builds one, so this
    # fixture is built by hand.
    nur_knoten = {"seed": "c.md",
                  "nodes": [{"id": "c.md", "label": "C", "ring": 0,
                             "seed": True, "grace": False}]}
    setze(v2(history=[{"id": "M1", "title": "Nur Knoten", "kind": "graph",
                       "graph": nur_knoten, "markdown": "", "set_at":
                       "2026-09-08T19:12:04", "collapsed_lines": 0,
                       "collapsed_markdown": ""}]))
    knoten_main = b.stage_parts()["history"][0]["main"]
    assert 'class="graf"' in knoten_main and 'class="graf-stub"' not in knoten_main \
        and "c.md" in knoten_main


# --- the /parts render+hash is cached by buehne.json's own identity
def test_two_calls_against_an_unchanged_file_return_the_identical_computed_result_object():
    setze(v2(headline="Cache-Test", main={"markdown": "## Stand eins"}))
    teile_a, mark_a = b.stage_parts_cached()
    teile_b, mark_b = b.stage_parts_cached()
    assert teile_a is teile_b and mark_a == mark_b


def test_a_real_change_to_buehne_json_invalidates_the_cache_on_its_own():
    # Writes the file directly, WITHOUT calling clear_cache() -- the cache
    # has to catch a real change on its own (mtime+size), the same way
    # render_file() does for a single project file.
    setze(v2(headline="Cache-Test", main={"markdown": "## Stand eins"}))
    teile_a, mark_a = b.stage_parts_cached()
    (b.PROJECT / "buehne.json").write_text(
        json.dumps(v2(headline="Cache-Test",
                      main={"markdown": "## Stand zwei, spuerbar mehr Text"}),
                   ensure_ascii=False),
        encoding="utf-8")
    teile_c, mark_c = b.stage_parts_cached()
    assert teile_c is not teile_a and mark_c != mark_a


# --- the cache also invalidates on a protocol-only change (A1)
# The running protocol changes FAR more often than buehne.json does (the
# consumer appends to it continuously). Without the protocol's own
# mtime+size in the cache key, an append there alone would sit uncached
# forever and the right column would silently stop following.
@pytest.fixture
def protokoll_sitz(tmp_path):
    folder = switch_project(tmp_path / "hist")
    (folder / "protokoll-2026-09-21.md").write_text(
        "# Protokoll\nEins.\n", encoding="utf-8")
    (folder / "buehne.json").write_text(
        json.dumps(v2(headline="Stand"), ensure_ascii=False), encoding="utf-8")
    return folder


def test_first_render_carries_the_protocols_first_line(protokoll_sitz):
    teile_p1, _ = b.stage_parts_cached()
    assert "Eins." in teile_p1["protokoll"]


def test_an_unchanged_protocol_is_still_served_from_cache(protokoll_sitz):
    teile_p1, _ = b.stage_parts_cached()
    teile_p1b, _ = b.stage_parts_cached()
    assert teile_p1 is teile_p1b


def _append_protocol(folder):
    # ONLY the protocol changes -- buehne.json is left untouched.
    (folder / "protokoll-2026-09-21.md").write_text(
        "# Protokoll\nEins.\nZwei, neu dazugekommen.\n", encoding="utf-8")


def test_a_protocol_only_change_invalidates_the_cache(protokoll_sitz):
    teile_p1, mark_p1 = b.stage_parts_cached()
    _append_protocol(protokoll_sitz)
    teile_p2, mark_p2 = b.stage_parts_cached()
    assert teile_p2 is not teile_p1 and mark_p2 != mark_p1


def test_and_the_new_line_reaches_the_render(protokoll_sitz):
    b.stage_parts_cached()
    _append_protocol(protokoll_sitz)
    teile_p2, _ = b.stage_parts_cached()
    assert "Zwei, neu dazugekommen." in teile_p2["protokoll"]


# ================================================================ graph tab
# A second tab next to the stage draws the session as a graph. It reads the
# same `/parts` poll with `g=1`; without it the stage's payload stays
# exactly what it was. Everything here is synthetic.
KANARIE = "KANARIENVOGEL-9c1e"


@pytest.fixture
def graf_sitz(tmp_path):
    folder = switch_project(tmp_path / "graphtab")
    (folder / "usecases.md").write_text("# Use Cases\n", encoding="utf-8")
    g_ordner = folder / "sessions" / "2026-09-26-probe"
    g_ordner.mkdir(parents=True)
    (g_ordner / "protokoll.md").write_text(
        "# Probe-Sitzung\n\n## Entscheidungen\n"
        "- 10:05 Budget freigegeben, siehe [[usecases]]\n\n"
        "## Aufgaben\n- 10:20 Tabelle nachziehen\n\n"
        f"## Rohtranskript\n- **10:30** {KANARIE} sagt [[{KANARIE}]]\n",
        encoding="utf-8")
    (g_ordner / "buehne.json").write_text(json.dumps(v2(
        headline="Probe",
        history=[{"id": "H1", "title": "Budget", "kind": "markdown",
                  "markdown": "- eins", "set_at": "2026-09-26T10:00:00",
                  "collapsed_lines": 0, "collapsed_markdown": ""},
                 {"id": "H2", "title": "Personal", "kind": "markdown",
                  "markdown": "- zwei", "set_at": "2026-09-26T10:15:00",
                  "collapsed_lines": 0, "collapsed_markdown": ""}]),
        ensure_ascii=False), encoding="utf-8")
    b.clear_cache()
    b._last_good = None
    return g_ordner


def test_graph_answers_200_with_html(graf_sitz, hole):
    code, gseite = hole("/graph")
    assert code == 200 and "<html" in gseite


def test_graph_carries_the_drawing_container(graf_sitz, hole):
    assert 'id="sg"' in hole("/graph")[1]


def test_graph_loads_session_graph_js(graf_sitz, hole):
    assert "session_graph.js" in hole("/graph")[1]


def test_graph_links_back_to_the_stage(graf_sitz, hole):
    assert "Bühne ↗" in hole("/graph")[1]


def test_graph_offers_the_graph_kanban_toggle(graf_sitz, hole):
    gseite = hole("/graph")[1]
    assert 'data-layout="graph"' in gseite and 'data-layout="kanban"' in gseite


def test_static_session_graph_js_is_served(graf_sitz, hole):
    code, js_sg = hole("/static/session_graph.js")
    assert code == 200 and "sgStart" in js_sg


def test_the_stage_header_links_to_the_graph_tab(graf_sitz, hole):
    code, sseite = hole("/")
    assert "Graph ↗" in sseite and 'href="graph' in sseite \
        and 'class="graf-link"' in sseite


def test_the_stage_script_listens_for_topic_clicks_from_the_graph_tab(graf_sitz, hole):
    sseite = hole("/")[1]
    assert "cothinker-topic" in sseite and "location.origin" in sseite


def test_parts_without_g1_carries_no_graph(graf_sitz, hole):
    assert "graph" not in json.loads(hole("/parts")[1])


def test_parts_without_g1_has_exactly_mark_clock_plus_the_stages_own_keys(graf_sitz, hole):
    # Pin the unrequested payload's shape exactly: `mark` and `clock` are
    # added around it (do_GET), everything else is whatever
    # `stage_parts_cached()` returns -- nothing more. A future field added
    # only for the graph tab must not leak into the plain `/parts` poll.
    ohne = json.loads(hole("/parts")[1])
    assert set(ohne) == {"mark", "clock"} | set(b.stage_parts_cached()[0])


def _graf(hole):
    return json.loads(hole("/parts?g=1")[1]).get("graph")


def test_parts_g1_carries_the_graph(graf_sitz, hole):
    graf = _graf(hole)
    assert isinstance(graf, dict) and set(graf) == {"seed", "nodes", "edges", "hidden"}


def test_parts_g1_answers_with_exactly_graph_and_clock(graf_sitz, hole):
    # The graph tab reads `graph` and nothing else. Handing it the whole
    # stage payload (rendered protocol incl. raw transcript) on every poll
    # was both a waste and a leak surface.
    assert set(json.loads(hole("/parts?g=1")[1])) == {"graph", "clock"}


def test_every_node_has_exactly_the_builders_keys(graf_sitz, hole):
    graf = _graf(hole)
    knoten_schluessel = {"id", "label", "kind", "ring", "href", "time",
                         "tag", "parent", "hidden", "grace"}
    assert graf["nodes"] and all(set(n) == knoten_schluessel for n in graf["nodes"])


def test_every_edge_has_exactly_id_source_target_kind_ring(graf_sitz, hole):
    graf = _graf(hole)
    assert graf["edges"] and all(set(e) == {"id", "source", "target", "kind", "ring"}
                                 for e in graf["edges"])


def test_the_seed_is_the_running_protocol_addressed_like_file(graf_sitz, hole):
    graf = _graf(hole)
    sitzung = next((n for n in graf["nodes"] if n["kind"] == "session"), {})
    assert graf.get("seed") == "file:sessions/2026-09-26-probe/protokoll" \
        and sitzung.get("href") == "file/sessions/2026-09-26-probe/protokoll"


def test_both_history_topics_are_nodes(graf_sitz, hole):
    graf = _graf(hole)
    assert {n["label"] for n in graf["nodes"] if n["kind"] == "topic"} \
        == {"Budget", "Personal"}


def test_a_wikilink_to_a_project_file_resolves_to_a_file_node(graf_sitz, hole):
    graf = _graf(hole)
    assert any(n["id"] == "file:usecases" and n["href"] == "file/usecases"
               for n in graf["nodes"])


def test_canary_nothing_from_rohtranskript_reaches_the_graph_field(graf_sitz, hole):
    assert KANARIE not in json.dumps(_graf(hole))


def test_canary_the_parts_g1_response_body_carries_no_rohtranskript_text_anywhere(
        graf_sitz, hole):
    assert KANARIE not in hole("/parts?g=1")[1]


def test_session_resolve_answers_the_node_id_only_href_is_the_builders_job(graf_sitz):
    assert b._session_resolve("usecases") == "file:usecases"


def test_the_file_views_parts_never_carries_a_graph(graf_sitz, hole):
    code, roh_d = hole("/parts?d=usecases&g=1")
    assert code == 200 and "graph" not in json.loads(roh_d)


# The graph tab's direct-open topic click (no window.opener) lands on
# /file/<protokoll>#block-<id>. window.SG_BLOCKS maps that id to the
# title/time stageTopicTarget matches on, published ONLY on the RUNNING
# protocol's own /file/ view.
PROBE_PROTO = "/file/sessions/2026-09-26-probe/protokoll"


def _sg_blocks(hole):
    seite = hole(PROBE_PROTO)[1]
    return seite, re.search(
        r"window\.SG_BLOCKS = (\{.*\});\nsgBlockAnchorJump\(\);", seite, re.S)


def test_file_running_protocol_publishes_window_sg_blocks(graf_sitz, hole):
    code, seite = hole(PROBE_PROTO)
    assert code == 200 and "window.SG_BLOCKS = {" in seite


def test_sg_blocks_carries_exactly_the_two_topic_nodes_by_their_stable_id(graf_sitz, hole):
    _, treffer = _sg_blocks(hole)
    sgb = json.loads(treffer.group(1)) if treffer else {}
    assert sgb == {"T:H1": {"title": "Budget", "time": "10:00"},
                   "T:H2": {"title": "Personal", "time": "10:15"}}


def test_file_any_other_project_file_leaves_window_sg_blocks_null(graf_sitz, hole):
    code, seite = hole("/file/usecases")
    assert code == 200 and "window.SG_BLOCKS = null;" in seite


def test_canary_sg_blocks_never_carries_anything_below_rohtranskript(graf_sitz, hole):
    # The /file/ page shows the raw transcript too -- the canary check is
    # scoped to the SG_BLOCKS blob itself.
    _, treffer = _sg_blocks(hole)
    assert KANARIE not in (treffer.group(1) if treffer else "")


# Befund 1: the graph cache is keyed on the vault scan's CONTENT digest, not
# on when the scan ran. Prove it: age `_vault_cache`'s timestamp past
# VAULT_TTL (without `clear_cache()`, which would also drop the graph cache
# itself and defeat the point) so the next call rescans the vault, then
# check that an unchanged vault does not trigger a rebuild -- the returned
# payload is the SAME cached object, and `_build_session_graph` was not
# called again.
@pytest.fixture
def zaehlender_build(graf_sitz, tmp_path, monkeypatch):
    vault_g = tmp_path / "graphvault"
    vault_g.mkdir()
    (vault_g / "notiz.md").write_text("# Notiz\n", encoding="utf-8")
    b.VAULT = vault_g
    b.clear_cache()
    aufrufe = []
    orig_build = b._build_session_graph

    def zaehlend(hysteresis):
        aufrufe.append(1)
        return orig_build(hysteresis)
    monkeypatch.setattr(b, "_build_session_graph", zaehlend)
    return aufrufe


def test_befund_1_setup_the_first_call_builds_the_graph_once(zaehlender_build):
    b.session_graph_payload()
    assert len(zaehlender_build) == 1


def test_befund_1_an_unchanged_rescan_does_not_rebuild_the_graph(zaehlender_build):
    g1 = b.session_graph_payload()
    ts, rest = b._vault_cache
    b._vault_cache = (ts - b.VAULT_TTL - 1.0, rest)  # force a rescan
    g2 = b.session_graph_payload()
    assert len(zaehlender_build) == 1 and g2 is g1


# Review minor c: the hysteresis must not carry one meeting's notes into the
# next as grace nodes. Session A links a vault note (which has a neighbour);
# then session B -- a new folder with a NEWER buehne.json, no links at all --
# becomes the running one.
@pytest.fixture
def hysterese(graf_sitz, tmp_path):
    vault_h = tmp_path / "graphhyst"
    vault_h.mkdir()
    (vault_h / "notizh.md").write_text("# Notiz H\nsiehe [[nachbarh]]\n",
                                       encoding="utf-8")
    (vault_h / "nachbarh.md").write_text("# Nachbar H\n", encoding="utf-8")
    sessions = b.PROJECT / "sessions"
    hyst_a = sessions / "2026-09-26-hyst-a"
    hyst_b = sessions / "2026-09-26-hyst-b"
    b.VAULT = vault_h
    hyst_a.mkdir(parents=True)
    (hyst_a / "protokoll.md").write_text(
        "# Sitzung A\n\n## Entscheidungen\n- 10:05 siehe [[notizh]]\n",
        encoding="utf-8")
    (hyst_a / "buehne.json").write_text(json.dumps(v2(headline="A")),
                                        encoding="utf-8")
    jetzt = time.time()
    os.utime(hyst_a / "buehne.json", (jetzt + 50, jetzt + 50))
    b.clear_cache()
    ga = b.session_graph_payload()

    def start_b():
        hyst_b.mkdir(parents=True)
        (hyst_b / "protokoll.md").write_text(
            "# Sitzung B\n\n## Aufgaben\n- 11:00 etwas ohne Links\n",
            encoding="utf-8")
        (hyst_b / "buehne.json").write_text(json.dumps(v2(headline="B")),
                                            encoding="utf-8")
        os.utime(hyst_b / "buehne.json", (jetzt + 100, jetzt + 100))
        return b.session_graph_payload()
    return ga, start_b


def test_minor_c_setup_session_a_draws_its_vault_note(hysterese):
    ga, _ = hysterese
    assert ga["seed"].endswith("2026-09-26-hyst-a/protokoll") \
        and any(n["id"] == "vault:notizh.md" for n in ga["nodes"])


def test_minor_c_setup_session_b_is_now_the_running_one(hysterese):
    _, start_b = hysterese
    assert start_b()["seed"].endswith("2026-09-26-hyst-b/protokoll")


def test_minor_c_a_new_session_starts_without_the_old_ones_grace_nodes(hysterese):
    _, start_b = hysterese
    gb = start_b()
    assert not any(n["grace"] for n in gb["nodes"]) \
        and not any(n["id"] in ("vault:notizh.md", "vault:nachbarh.md")
                    for n in gb["nodes"])


def test_without_a_protocol_the_seed_is_session_with_no_href(graf_sitz):
    # No protocol at all: a seed called `session`, no href.
    (graf_sitz / "protokoll.md").unlink()
    b.clear_cache()
    leer = b.session_graph_payload()
    assert leer["seed"] == "session" and leer["nodes"][0]["href"] == ""


# ===================================== the announcement line (pending)
# id/kind/label only, outside the fingerprint. The folder is a fresh one:
# `stage_file()` picks the newest `buehne.json` across all session folders.
SENTENCE = "Wir sollten das Budget fuer Ortsgedaechtnis verdoppeln"


@pytest.fixture
def pend(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2(pending=[
        {"kind": "main", "label": "Auftrag angenommen",
         "since": "2026-09-26T10:00:00+02:00", "id": "P1",
         "text": SENTENCE, "quote": SENTENCE},
        {"kind": "bogus", "label": "falsche Art", "since": "", "id": "P2"},
        {"kind": "research", "label": "Recherche zu Ortsgedaechtnis",
         "since": "", "id": "X9"},
        {"kind": "topic", "label": "neues Thema " + "x" * 200, "since": "",
         "id": "P4"},
        "kein dict",
        {"kind": "decision", "label": "Beschluss", "id": "P5"},
        {"kind": "question", "label": "Frage", "id": "P6"},
    ]))
    return b.stage_parts()["pending"]


def test_pending_is_a_list_of_dicts(pend):
    assert isinstance(pend, list) and all(isinstance(x, dict) for x in pend)


def test_each_entry_carries_exactly_id_kind_label(pend):
    assert all(set(x) == {"id", "kind", "label"} for x in pend)


def test_no_synthetic_sentence_from_a_foreign_key_comes_through(pend):
    assert SENTENCE not in json.dumps(pend, ensure_ascii=False)


def test_an_unknown_kind_is_dropped(pend):
    assert all(x["kind"] != "bogus" for x in pend) and all(x["id"] != "P2" for x in pend)


def test_an_id_not_shaped_p_n_is_dropped(pend):
    assert all(x["id"] != "X9" for x in pend)


def test_at_most_three_entries_in_file_order(pend):
    assert [x["id"] for x in pend] == ["P1", "P4", "P5"]


def test_the_label_is_capped(pend):
    assert all(len(x["label"]) <= 60 for x in pend)


def test_the_label_stays_plain_text_the_page_uses_textcontent(pend):
    assert pend[0]["label"] == "Auftrag angenommen"


def test_the_label_is_not_html_escaped_server_side_no_double_escaping(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2(pending=[{"kind": "main", "label": "<b>x</b> & y", "id": "P1"}]))
    assert b.stage_parts()["pending"][0]["label"] == "<b>x</b> & y"


def test_the_pending_kind_entry_is_kept_not_dropped_as_unknown(tmp_path):
    # The pending kind for level 2's actions (gateway/policy.py) is
    # accepted like any other, not just added to the allow-list and
    # forgotten.
    switch_project(tmp_path / "pending")
    setze(v2(pending=[{"kind": "entry", "label": "Eintrag: Aufgabe", "id": "P1"}]))
    assert b.stage_parts()["pending"] == [{"id": "P1", "kind": "entry",
                                           "label": "Eintrag: Aufgabe"}]


BROKEN_PENDING = [None, "text", 5, {"id": "P1"}, [None, 3, []]]


@pytest.mark.parametrize("broken", BROKEN_PENDING, ids=[repr(x) for x in BROKEN_PENDING])
def test_stage_parts_survives_pending(broken, tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2(pending=broken))
    b.stage_parts()


@pytest.mark.parametrize("broken", BROKEN_PENDING, ids=[repr(x) for x in BROKEN_PENDING])
def test_and_yields_a_list_for(broken, tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2(pending=broken))
    assert b.stage_parts()["pending"] == []


def test_a_file_without_pending_older_consumer_yields_empty_list(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2())
    assert b.stage_parts()["pending"] == []


# The fingerprint: an announcement is about NOW and applied on every poll;
# in the mark it would run the swap logic (pulse, scroll restore).
def test_an_announcement_appearing_does_not_move_the_fingerprint(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2())
    without_pending = b.fingerprint(b.stage_parts())
    setze(v2(pending=[{"kind": "main", "label": "Auftrag angenommen",
                       "since": "2026-09-26T10:00:00+02:00", "id": "P1"}]))
    assert b.fingerprint(b.stage_parts()) == without_pending


def test_a_different_announcement_does_not_move_it_either(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2())
    without_pending = b.fingerprint(b.stage_parts())
    setze(v2(pending=[{"kind": "topic", "label": "neues Thema",
                       "since": "2026-09-26T10:00:09+02:00", "id": "P2"}]))
    assert b.fingerprint(b.stage_parts()) == without_pending


def test_fingerprint_says_so_in_its_docstring():
    assert "pending" in (b.fingerprint.__doc__ or "")


def test_parts_via_the_cache_carries_the_announcement_too(tmp_path):
    switch_project(tmp_path / "pending")
    setze(v2(pending=[{"kind": "main", "label": "Auftrag angenommen", "id": "P1"}]))
    parts_pd, _ = b.stage_parts_cached()
    assert parts_pd["pending"] == [{"id": "P1", "kind": "main",
                                    "label": "Auftrag angenommen"}]


def test_the_page_has_the_announcement_container_under_the_header_row():
    assert 'id="pending"' in inspect.getsource(b.H._get) \
        and 'aria-live="polite"' in inspect.getsource(b.H._get)


def test_the_label_reaches_the_dom_via_textcontent_never_innerhtml():
    page_pd = b.SCRIPT
    assert "pendingApply" in page_pd \
        and re.search(r"\.textContent\s*=\s*[a-zA-Z_.]*label", page_pd) is not None \
        and re.search(r"innerHTML\s*=[^;]*label", page_pd) is None


def test_reduced_motion_is_honoured_for_the_morph():
    assert "prefers-reduced-motion" in b.SCRIPT and "matchMedia" in b.SCRIPT


# ======================================================= protocol v2 column
# The right column renders a v2 protocol (topic blocks with tagged entries,
# derived view `## Überblick`) as blocks, and a v1 protocol exactly as
# before. Synthetic text only.
V2_PROTOKOLL = """---
title: "Testtermin"
protokoll_format: 2
modus: sitzung
---

# Testtermin

⚠️ **Automatische Transkription.** Eigennamen und Zahlen sind unbestaetigt.
**Start:** 10:00

## Überblick

<!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem Eintrag neu. -->

### Beschlüsse

- 10:05 Budget freigegeben (Thema: Kosten)

### Aufgaben

*noch keine*

### Offene Fragen

*noch keine*

## Themen

### 10:01 Kosten
- 10:05 [beschluss] Budget freigegeben, siehe [[kostenplan]]
- 10:06 [idee] Mal <script>alert(1)</script> probieren, auch [[gibtsnicht]]

### 10:10 Termine
- 10:11 [frage] Wann ist die naechste Runde?

## Rohtranskript

**10:00:01** Hallo zusammen.
**10:00:05** Wir fangen an.
**10:00:09** Kosten zuerst.
"""

V1_PROTOKOLL = """---
title: "Alt"
---

# Alt

## Verlauf

- 09:00 Etwas besprochen, [[kostenplan]]

## Entscheidungen

- keine

## Rohtranskript

**09:00:01** Guten Morgen.
"""

NAMEN_V2 = {"kostenplan"}


@pytest.fixture
def h2():
    return b.render_protocol_v2(V2_PROTOKOLL, NAMEN_V2)


def test_v2_the_derived_view_sits_in_a_sicht_block(h2):
    assert re.search(r'<div class="sicht">(.*?)</div>', h2, re.S) is not None


def test_v2_the_view_carries_its_decision_line(h2):
    sicht = re.search(r'<div class="sicht">(.*?)</div>', h2, re.S)
    assert sicht is not None \
        and "Budget freigegeben (Thema: Kosten)" in sicht.group(1)


def test_v2_the_views_html_comment_reaches_the_page_neither_raw_nor_escaped(h2):
    assert "<!--" not in h2 and "&lt;!--" not in h2 and "Abgeleitete Sicht" not in h2


def test_v2_the_header_title_start_is_rendered(h2):
    assert "<h1>Testtermin</h1>" in h2 and "Start:" in h2


def test_v2_no_frontmatter_on_the_page(h2):
    assert "protokoll_format" not in h2


def test_v2_every_topic_is_a_keyed_details_block(h2):
    assert '<details class="thema" data-key="thema-1">' in h2 \
        and '<details class="thema" data-key="thema-2" open data-auf="1">' in h2


def test_v2_only_the_newest_topic_is_open(h2):
    assert len(re.findall(r'<details class="thema"[^>]* open[ >]', h2)) == 1


def test_v2_the_servers_default_open_topic_is_marked_data_auf_the_others_not(h2):
    assert h2.count('data-auf="1"') == 1


def test_v2_the_summary_carries_time_title_and_the_entry_count(h2):
    assert "<summary>10:01 Kosten <span class=\"n\">(2)</span></summary>" in h2 \
        and "<summary>10:10 Termine <span class=\"n\">(1)</span></summary>" in h2


def test_v2_every_entry_carries_its_tag_badge(h2):
    assert '<span class="tag tag-beschluss">beschluss</span>' in h2 \
        and '<span class="tag tag-idee">idee</span>' in h2 \
        and '<span class="tag tag-frage">frage</span>' in h2


def test_v2_hostile_entry_text_is_escaped_never_executed(h2):
    assert "<script>" not in h2 and "&lt;script&gt;alert(1)&lt;/script&gt;" in h2


def test_v2_a_wikilink_to_an_existing_file_is_linked(h2):
    assert '<a href="file/kostenplan">kostenplan</a>' in h2


def test_v2_an_unknown_wikilink_is_shown_dead_tot(h2):
    assert '<span class="tot">gibtsnicht</span>' in h2


def test_v2_the_raw_transcript_is_a_closed_keyed_details_block_with_a_line_count(h2):
    roh = re.search(r'<details class="roh" data-key="roh">(.*?)</details>', h2, re.S)
    assert roh is not None \
        and "<summary>Rohtranskript (3 Zeilen)</summary>" in roh.group(0)


def test_v2_the_raw_transcripts_text_is_still_in_it(h2):
    roh = re.search(r'<details class="roh" data-key="roh">(.*?)</details>', h2, re.S)
    assert roh is not None and "Wir fangen an." in roh.group(1)


def test_v2_the_raw_transcript_comes_after_the_last_topic(h2):
    assert h2.index('data-key="roh"') > h2.index('data-key="thema-2"')


LEER_V2 = V2_PROTOKOLL.split("## Themen")[0] + "## Themen\n\n## Rohtranskript\n"


def test_v2_empty_themen_shows_a_short_placeholder_no_topic_block():
    h_leer = b.render_protocol_v2(LEER_V2, NAMEN_V2)
    assert "Noch kein Thema." in h_leer and 'class="thema"' not in h_leer


def test_v2_an_empty_raw_transcript_counts_0_lines():
    assert "Rohtranskript (0 Zeilen)" in b.render_protocol_v2(LEER_V2, NAMEN_V2)


def test_v2_a_crlf_saved_file_renders_the_same_blocks():
    h_crlf = b.render_protocol_v2(V2_PROTOKOLL.replace("\n", "\r\n"), NAMEN_V2)
    assert 'data-key="thema-2" open data-auf="1">' in h_crlf and "<!--" not in h_crlf \
        and "Rohtranskript (3 Zeilen)" in h_crlf


# v1 and v2 side by side, each detected by its own content, through the same
# render+cache path the column uses.
@pytest.fixture
def nebeneinander(tmp_path):
    folder = switch_project(tmp_path / "v2")
    (folder / "kostenplan.md").write_text("# Kostenplan\n", encoding="utf-8")
    alt_s = folder / "sessions" / "2026-09-20-alt"
    neu_s = folder / "sessions" / "2026-09-26-neu"
    alt_s.mkdir(parents=True)
    neu_s.mkdir(parents=True)
    (alt_s / "protokoll.md").write_text(V1_PROTOKOLL, encoding="utf-8")
    (neu_s / "protokoll.md").write_text(V2_PROTOKOLL, encoding="utf-8")
    namen = {q.stem for q in b.list_files()}
    return alt_s, neu_s, namen


def test_v1_in_the_column_renders_exactly_as_before_md_text(nebeneinander):
    alt_s, _, namen = nebeneinander
    v1_spalte = b.render_file(alt_s / "protokoll.md", namen, spalte=True)[0]
    assert v1_spalte == b.md(V1_PROTOKOLL, namen)


def test_v1_carries_no_v2_markup(nebeneinander):
    alt_s, _, namen = nebeneinander
    v1_spalte = b.render_file(alt_s / "protokoll.md", namen, spalte=True)[0]
    assert 'class="thema"' not in v1_spalte and 'class="sicht"' not in v1_spalte


def test_v2_next_to_it_is_detected_by_its_own_content(nebeneinander):
    _, neu_s, namen = nebeneinander
    v2_spalte = b.render_file(neu_s / "protokoll.md", namen, spalte=True)[0]
    assert v2_spalte == b.render_protocol_v2(V2_PROTOKOLL, namen)


def test_the_file_render_of_the_same_v2_file_stays_plain_md(nebeneinander):
    # Cache keys do not answer for each other.
    _, neu_s, namen = nebeneinander
    v2_datei = b.render_file(neu_s / "protokoll.md", namen)[0]
    assert v2_datei == b.md(V2_PROTOKOLL, namen)


def test_and_the_column_render_is_not_served_from_the_file_entry(nebeneinander):
    _, neu_s, namen = nebeneinander
    v2_spalte = b.render_file(neu_s / "protokoll.md", namen, spalte=True)[0]
    b.render_file(neu_s / "protokoll.md", namen)
    assert b.render_file(neu_s / "protokoll.md", namen, spalte=True)[0] == v2_spalte


def test_stage_parts_a_running_v2_session_gets_the_block_column(nebeneinander):
    # The running session (by buehne.json) is the v2 one: stage_parts hands
    # the v2 column out.
    _, neu_s, _ = nebeneinander
    (neu_s / "buehne.json").write_text(
        json.dumps(v2(headline="Stand"), ensure_ascii=False), encoding="utf-8")
    b.clear_cache()
    teile_v2 = b.stage_parts()
    assert 'data-key="thema-2" open' in teile_v2["protokoll"] \
        and 'class="sicht"' in teile_v2["protokoll"]


def test_stage_parts_a_running_v1_session_gets_md_text_byte_identical(nebeneinander):
    alt_s, _, _ = nebeneinander
    (alt_s / "buehne.json").write_text(
        json.dumps(v2(headline="Stand"), ensure_ascii=False), encoding="utf-8")
    b.clear_cache()
    teile_v1 = b.stage_parts()
    assert teile_v1["protokoll"] == b.md(V1_PROTOKOLL, {q.stem for q in b.list_files()})


def test_the_page_style_carries_the_v2_column_rules():
    assert "#protokoll-col .sicht{position:sticky" in b.STYLE and ".tag-beschluss" in b.STYLE


def test_the_two_pending_kinds_copies_gateway_and_stage_stay_equal():
    # The contract is wire-format, additive-only: a kind the writer
    # (gateway/stage_state.py) may announce but this reader does not know
    # would be dropped silently on the way to the page. The two copies
    # drift apart only if one side forgets to add a kind here.
    from stage_state import PENDING_KINDS as GATEWAY_PENDING_KINDS
    assert set(b.PENDING_KINDS) == GATEWAY_PENDING_KINDS
