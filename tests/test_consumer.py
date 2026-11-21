# CoThinker — tests of the consumer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of the consumer: SSE parsing, protocol writing, batching, the
stage writer, the live path against a fake worker, and the prompts.

Each scenario is a module-scoped fixture (`sec_NN`) that runs once and
records the outcome of every check it makes; each check is one test case
named after it. No real agent runs: the agent is a function or the fake
worker (tests/fake_worker.py).
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway import consumer as k  # noqa: E402


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
_NO_GITIGNORE = pytest.mark.xfail(strict=True, reason="this repository ships no .gitignore")


@pytest.fixture(scope="module")
def sec_00():
    """Scenario: SSE parsing"""
    global _REC
    _REC = rec = _Recorder()
    global Path, atexit, block, datetime, ev, k, os, re, roh, shutil, subprocess, sys, tempfile, threading, time
    import atexit
    import os
    import re
    import shutil
    import subprocess
    import sys
    import tempfile
    import threading
    import time
    from datetime import datetime
    from pathlib import Path






    from gateway import consumer as k  # noqa: E402


    def block(text: str, wake_word: bool = False, reason: str = "pause"):
        """A block, as the block builder would deliver it."""
        return k.Block(sentences=[text], start=0.0, end=1.0, reason=reason,
                       wake_word=wake_word)


    # ====================================================================== SSE
    try:
        print("SSE parsing")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 1 — the normal case: one event per line, closed with a blank line
    try:
        roh = [b"data: {\"type\": \"final\", \"text\": \"Hallo\"}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1, "simple event", ev == [{"type": "final", "text": "Hallo"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 2 — multi-line data field (SSE allows this; pretty-printed JSON)
    try:
        roh = [b"data: {\n", b'data:   "type": "final",\n', b'data:   "text": "Zwei"\n',
               b"data: }\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(2, "multi-line data field", ev == [{"type": "final", "text": "Zwei"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 3 — CRLF line endings
    try:
        roh = [b"data: {\"type\": \"final\", \"text\": \"CRLF\"}\r\n", b"\r\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(3, "CRLF line endings", ev == [{"type": "final", "text": "CRLF"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 4 — comments, event names and ids are skipped
    try:
        roh = [b": keep-alive\n", b"event: message\n", b"id: 17\n",
               b"retry: 3000\n", b"data: {\"type\": \"final\", \"text\": \"X\"}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(4, "comment/event/id/retry skipped", ev == [{"type": "final", "text": "X"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 5 — broken JSON doesn't tear down the stream
    try:
        roh = [b"data: {kaputt\n", b"\n",
               b"data: {\"type\": \"final\", \"text\": \"danach\"}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(5, "broken JSON skipped", ev == [{"type": "final", "text": "danach"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 6 — JSON that isn't an object (list, number, null)
    try:
        roh = [b"data: [1,2,3]\n", b"\n", b"data: null\n", b"\n", b"data: 42\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(6, "non-objects discarded", ev == [], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 7 — an event without a trailing blank line at the end of the stream isn't lost
    try:
        roh = [b"data: {\"type\": \"final\", \"text\": \"letztes\"}\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(7, "event at end of stream", ev == [{"type": "final", "text": "letztes"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 8 — `data:` without a space after it
    try:
        roh = [b"data:{\"type\":\"final\",\"text\":\"eng\"}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(8, "data without a space", ev == [{"type": "final", "text": "eng"}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 9 — invalid bytes don't break anything
    try:
        roh = [b"data: {\"type\": \"final\", \"text\": \"kaputt \xff\xfe\"}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(9, "invalid bytes replaced", len(ev) == 1 and ev[0]["type"] == "final", repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 10 — two events in the same buffer, correctly separated
    try:
        roh = [b"data: {\"n\": 1}\n", b"\n", b"data: {\"n\": 2}\n", b"\n"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ev = list(k.parse_sse(roh))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(10, "two events separated", ev == [{"n": 1}, {"n": 2}], repr(ev))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_01(sec_00):
    """Scenario: the session folder and its slug"""
    global _REC
    _REC = rec = _Recorder()
    global alt, de, lang, orte, orte_de, sess


    # ========================================================== session folder
    # One folder per meeting: `sessions/<YYYY-MM-DD>-<slug>/`. A project folder
    # collects meetings over months, and flat their files lie interleaved
    # between the project's own documents.
    try:
        print("\nthe session folder and its slug")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(11, "a plain title becomes a plain slug",
              k.slugify("Garden Call") == "garden-call", k.slugify("Garden Call"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(12, "umlauts are spelled out, not stripped",
              k.slugify("Bühne für Öffentlichkeit") == "buehne-fuer-oeffentlichkeit",
              k.slugify("Bühne für Öffentlichkeit"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(13, "the sharp s becomes ss", k.slugify("Straße") == "strasse",
              k.slugify("Straße"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(14, "an accent is decomposed away", k.slugify("Café Résumé") == "cafe-resume",
              k.slugify("Café Résumé"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # A title comes from the command line and lands in a PATH. A slash in it
    # would silently create a folder inside a folder, and `..` would leave the
    # project entirely.
    try:
        _chk(15, "a slash cannot open a second path level",
              "/" not in k.slugify("Termin 1/2 mit ../oben"),
              k.slugify("Termin 1/2 mit ../oben"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(16, "punctuation and quotes collapse to single dashes",
              k.slugify('Robo: "Wie weiter?" — Teil 2!') == "robo-wie-weiter-teil-2",
              k.slugify('Robo: "Wie weiter?" — Teil 2!'))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(17, "no leading or trailing dash", k.slugify("--- Rand ---") == "rand",
              k.slugify("--- Rand ---"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(18, "emoji leave nothing behind but the words",
              k.slugify("🎧 Mitschrift 🎤") == "mitschrift", k.slugify("🎧 Mitschrift 🎤"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The empty result is the dangerous one: it would produce `2026-09-08-`,
    # a folder whose name ends in the separator.
    try:
        _chk(19, "a title of pure punctuation falls back", k.slugify("!!! ???") == "termin",
              k.slugify("!!! ???"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(20, "an empty title falls back", k.slugify("") == "termin", k.slugify(""))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(21, "the fallback can be chosen by the caller",
              k.slugify("", "projektname") == "projektname")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(22, "None does not raise", k.slugify(None) == "termin", k.slugify(None))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        lang = k.slugify("Ein sehr langer Titel der weit ueber die Grenze "
                         "hinausgeht und immer weiter laeuft")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(23, "a long title is shortened", len(lang) <= k.MAX_SLUG_CHARS, f"{len(lang)}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(24, "and not cut mid-word", not lang.endswith("-") and lang.startswith("ein-sehr"),
              lang)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        sess = Path(_mkdtemp(prefix="cothinker-sess-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, sess, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        orte = k.session_paths(sess, "2026-09-08", "Garden Call")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(25, "a fresh run gets its own dated folder",
              orte["folder"] == sess / "sessions" / "2026-09-08-garden-call",
              str(orte["folder"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(26, "the protocol drops the date suffix inside it",
              orte["protocol"].name == "protokoll.md", orte["protocol"].name)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # A project that already keeps its meetings in the German `sitzungen/`
    # must not get a second, English folder beside it. The stage reads both
    # names, so a split would leave one round's material in two places and
    # the meeting would silently miss whatever landed in the folder the
    # stage did not pick -- exactly what happened on 2026-09-21, when the
    # stage showed nothing because the consumer had written elsewhere.
    try:
        de = Path(_mkdtemp(prefix="cothinker-de-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, de, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (de / "sitzungen").mkdir()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        orte_de = k.session_paths(de, "2026-09-08", "Garden Call")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(27, "an existing sitzungen/ is used instead of creating sessions/",
              orte_de["folder"] == de / "sitzungen" / "2026-09-08-garden-call",
              str(orte_de["folder"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(28, "and no second folder is invented beside it",
              not (de / "sessions").exists(), str(list(de.iterdir())))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(29, "so do the proposals", orte["proposals"].name == "vorschlag.jsonl",
              orte["proposals"].name)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(30, "and the event trace", orte["events"].name == "stage-events.jsonl",
              orte["events"].name)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(31, "all three land in the same folder",
              {p.parent for p in (orte["protocol"], orte["proposals"], orte["events"])}
              == {orte["folder"]})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(32, "a fresh run is not the flat case", orte["flat"] is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(33, "without a title the fallback names the folder",
              k.session_paths(sess, "2026-09-08", None,
                              fallback="testprojekt")["folder"].name
              == "2026-09-08-testprojekt")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # BACKWARDS COMPATIBLE. A meeting that started before this change wrote
    # `protokoll-<datum>.md` flat into the project folder. Standing a fresh
    # session folder next to it mid-meeting would tear the run in two: an
    # empty protocol while the round reads the old one, and the proposal
    # offsets restarting from zero.
    try:
        (sess / "protokoll-2026-09-08.md").write_text("## Rohtranskript\n",
                                                      encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        alt = k.session_paths(sess, "2026-09-08", "Garden Call")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(34, "a flat run of the same day is continued where it lies",
              alt["protocol"] == sess / "protokoll-2026-09-08.md", str(alt["protocol"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(35, "its proposal file keeps the flat, dated name",
              alt["proposals"] == sess / "vorschlag-2026-09-08.jsonl",
              str(alt["proposals"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(36, "and so does its event trace",
              alt["events"] == sess / "stage-events-2026-09-08.jsonl",
              str(alt["events"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(37, "the flat case says so", alt["flat"] is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(38, "no session folder is created beside it",
              not (sess / "sessions" / "2026-09-08-garden-call").exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # YESTERDAY's flat protocol is not this meeting. It must not pull today's
    # run back into the old layout.
    try:
        _chk(39, "a flat protocol from another day does not capture today's run",
              k.session_paths(sess, "2026-09-09", "Garden Call")["flat"] is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # An explicit --file names the protocol in both layouts.
    try:
        _chk(40, "--file is honoured in the flat case",
              k.session_paths(sess, "2026-09-08", None,
                              file="protokoll-2026-09-08.md")["flat"] is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(41, "--file names the protocol in the session folder too",
              k.session_paths(sess, "2026-09-09", "Test",
                              file="eigen.md")["protocol"].name == "eigen.md")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_02(sec_01):
    """Scenario: COTHINKER_SESSIONS_DIR configures the folder name for new sessions"""
    global _REC
    _REC = rec = _Recorder()
    global besteht, konfig, old_sessions_env, orte_besteht, orte_konfig

    # a partner repo that files its meetings under a folder name
    # that is neither `sessions/` nor `sitzungen/` (e.g. `protokolle/`) sets
    # COTHINKER_SESSIONS_DIR once; a fresh run must land there, not in
    # `sessions/`.
    try:
        print("\nCOTHINKER_SESSIONS_DIR configures the folder name for new sessions")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        old_sessions_env = os.environ.pop("COTHINKER_SESSIONS_DIR", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            konfig = Path(_mkdtemp(prefix="cothinker-sessions-env-"))
            os.environ["COTHINKER_SESSIONS_DIR"] = "protokolle"
            orte_konfig = k.session_paths(konfig, "2026-11-02", "Team-Meeting")
            _chk(42, "a fresh run lands in the configured folder",
                  orte_konfig["folder"] == konfig / "protokolle"
                                         / "2026-11-02-team-meeting",
                  str(orte_konfig["folder"]))
            _chk(43, "no sessions/ is created beside it",
                  not (konfig / "sessions").exists(), str(list(konfig.iterdir())))

            # An existing sessions/ or sitzungen/ still wins over the configured
            # name -- the lookup list is extended, not replaced.
            besteht = Path(_mkdtemp(prefix="cothinker-sessions-env-alt-"))
            (besteht / "sitzungen").mkdir()
            orte_besteht = k.session_paths(besteht, "2026-11-02", "Team-Meeting")
            _chk(44, "an existing sitzungen/ still wins over the configured name",
                  orte_besteht["folder"] == besteht / "sitzungen"
                                           / "2026-11-02-team-meeting",
                  str(orte_besteht["folder"]))
        finally:
            os.environ.pop("COTHINKER_SESSIONS_DIR", None)
            if old_sessions_env is not None:
                os.environ["COTHINKER_SESSIONS_DIR"] = old_sessions_env
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(45, "unset, the default folder name is unchanged",
              k.configured_sessions_dir() == "sessions", k.configured_sessions_dir())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_03(sec_02):
    """Scenario: Protocol"""
    global _REC
    _REC = rec = _Recorder()
    global behalten, danach, inhalt, n, pfad, prot, prot2, r, s, skript, tmp, zeilen


    # ================================================================ Protocol
    try:
        print("\nProtocol")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        tmp = Path(_mkdtemp(prefix="cothinker-test-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        pfad = tmp / "protokoll-test.md"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        pfad.write_text("## Rohtranskript\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot = k.Protocol(pfad, tmp)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 11 — umlauts and special characters end up as UTF-8 in the file
    try:
        prot.append_raw("Der Förderantrag über 12.000 € — Grüße 🎧", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        inhalt = pfad.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(46, "Unicode in the protocol", "12.000 €" in inhalt and "🎧" in inhalt)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 12 — line breaks in the recognized text don't break the list
    try:
        prot.append_raw("Erste Zeile\nZweite Zeile\r\nDritte", "left")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        zeilen = [z for z in pfad.read_text(encoding="utf-8").splitlines() if z.startswith("- **")]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(47, "one final = one line", len(zeilen) == 2, f"{len(zeilen)} lines")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(48, "line break becomes a space",
              any("Erste Zeile Zweite Zeile Dritte" in z for z in zeilen), repr(zeilen[-1]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 13 — a heading in the spoken text must not create structure
    try:
        prot.append_raw("## Entscheidungen sind wichtig", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(49, "no heading created from the transcript",
              not any(z.startswith("## Entscheidungen sind")
                      for z in pfad.read_text(encoding="utf-8").splitlines()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 14 — speaker labels
    try:
        prot.append_raw("Mit Sprecher", "links")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(50, "speaker is set in italics",
              "*links:* Mit Sprecher" in pfad.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 15 — writes even when the environment can't do UTF-8
    try:
        skript = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from gateway.consumer import Protocol
from pathlib import Path
p = Path({str(tmp / 'locale-test.md')!r})
p.write_text("## Rohtranskript\\n", encoding="utf-8")
Protocol(p, p.parent).append_raw("Gr\\u00fc\\u00dfe aus dem \\u20ac-Raum", None)
print("ok")
"""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        r = subprocess.run([sys.executable, "-c", skript], capture_output=True, text=True,
                           env=dict(os.environ, LC_ALL="C", LANG="C",
                                    PYTHONCOERCECLOCALE="0", PYTHONUTF8="0"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(51, "writes even under the C locale", r.returncode == 0, r.stderr.strip()[-200:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if r.returncode == 0:
            _chk(52, "content correct under the C locale",
                  "€-Raum" in (tmp / "locale-test.md").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 16 — lost raw lines are detected and restored
    try:
        prot2 = k.Protocol(tmp / "verlust.md", tmp)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (tmp / "verlust.md").write_text("## Rohtranskript\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for s in ["Satz eins", "Satz zwei", "Satz drei"]:
            prot2.append_raw(s, None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # A foreign process (the agent) rewrites the whole file and loses two
    # lines in the process — exactly the case from part D.
    try:
        behalten = [z for z in (tmp / "verlust.md").read_text(encoding="utf-8").splitlines()
                    if "Satz eins" in z]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (tmp / "verlust.md").write_text("## Verlauf\n- irgendwas\n\n## Rohtranskript\n"
                                        + "\n".join(behalten) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        n = prot2.restore_missing()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(53, "loss detected", n == 2, f"{n} restored")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        danach = (tmp / "verlust.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(54, "Satz zwei is back", "Satz zwei" in danach)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(55, "Satz drei is back", "Satz drei" in danach)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(56, "Satz eins not duplicated", danach.count("Satz eins") == 1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(57, "second pass restores nothing", prot2.restore_missing() == 0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 17 — conflict files (Nextcloud sync) are reported
    try:
        (tmp / "protokoll-test (Conflicted copy 2026-09-07).md").write_text("x", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(58, "conflict file detected", len(prot.conflict_files()) == 1,
              repr([p.name for p in prot.conflict_files()]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_04(sec_03):
    """Scenario: Git"""
    global _REC
    _REC = rec = _Recorder()
    global _zeit, alt_log, dauer, erfolg, g, gdatei, gprot, gprot2, grepo, meldungen, t0


    # ======================================================================= git
    try:
        print("\nGit")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        grepo = Path(_mkdtemp(prefix="cothinker-git-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, grepo, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def g(*args):
        return subprocess.run(["git", *args], cwd=grepo, capture_output=True, text=True)


    try:
        g("init", "-q")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        g("config", "user.email", "test@example.invalid")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        g("config", "user.name", "Test")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gdatei = grepo / "protokoll.md"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gdatei.write_text("erste Zeile\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gprot = k.Protocol(gdatei, grepo)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 18 — local vault without a remote: commit, but don't pointlessly
    #      pull and push (this used to fail on every commit)
    try:
        meldungen = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        alt_log = k.log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: meldungen.append(" ".join(str(x) for x in a))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        erfolg = gprot._git_run("Testcommit")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(59, "without a remote: success", erfolg is True, str(erfolg))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(60, "without a remote: no error message", not meldungen, repr(meldungen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(61, "committed", "Testcommit" in g("log", "--oneline").stdout,
              g("log", "--oneline").stdout)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 19 — "nothing to commit" is not an error and is not reported
    try:
        meldungen.clear()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: meldungen.append(" ".join(str(x) for x in a))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        erfolg = gprot._git_run("Nochmal ohne Aenderung")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(62, "empty commit is not an error", erfolg is True, repr(meldungen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(63, "empty commit reports nothing", not meldungen, repr(meldungen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 20 — a failing sync is reported instead of swallowed
    try:
        g("remote", "add", "origin", str(grepo / "gibtsnicht.git"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gdatei.write_text("zweite Zeile\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gprot2 = k.Protocol(gdatei, grepo)          # fresh look at the remote
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        meldungen.clear()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: meldungen.append(" ".join(str(x) for x in a))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        erfolg = gprot2._git_run("Mit kaputter Fernstelle")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(64, "broken remote reports failure", erfolg is False, str(erfolg))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(65, "failure is logged",
              any("git" in m for m in meldungen), repr(meldungen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(66, "still committed locally",
              "Mit kaputter Fernstelle" in g("log", "--oneline").stdout)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 21 — git doesn't block the SSE reader (runs in the background)
    import time as _zeit  # noqa: E402

    try:
        k.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gdatei.write_text("dritte Zeile\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t0 = _zeit.monotonic()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gprot2.git("Hintergrund")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        dauer = _zeit.monotonic() - t0
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(67, "git doesn't block the reader", dauer < 0.2, f"{dauer:.2f}s")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gprot2.git_wait(30)          # the background thread reports the error; stay quiet here
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_05(sec_04):
    """Scenario: Batching and backlog"""
    global _REC
    _REC = rec = _Recorder()
    global Block, _, _count_run, _slow_normal, _tracking_run, auftraege, b, b2, chunks, consumer, deadline, dt, gesamt_chars, gesehen, gesehen2, i, normal, normal_go, normal_hold, normal_release, normal_seen, normal_started, order_done, orders_seen, restart_backlog, scheitert_einmal, seen_lock, stop2, t0, t_calls, t_normal, t_normal2, t_order, timeline, v


    # ================================================================ RunQueue
    try:
        print("\nBatching and backlog")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: None          # don't print the queue's own log lines
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 21 — without a backlog, one block stays one block
    try:
        v = k.RunQueue(run=lambda b: True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Ein einzelner Abschnitt mit genug Text."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(68, "single block unchanged", b is not None and b.text.startswith("Ein einzelner"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(69, "queue empty afterwards", v.next_batch(0) is None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 22 — on a backlog, waiting blocks are merged into ONE run
    try:
        v = k.RunQueue(run=lambda b: True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(6):
            v.enqueue(block(f"Abschnitt Nummer {i} mit ausreichend Text darin."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(70, "backlog gets merged",
              b is not None and all(f"Abschnitt Nummer {i}" in b.text for i in range(6)),
              (b.text[:80] if b else ""))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(71, "reason = batch", b is not None and b.reason == "batch", b.reason if b else "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(72, "queue empty afterwards", v.next_batch(0) is None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 23 — order is preserved as spoken
    try:
        _chk(73, "order preserved",
              b is not None and b.text.index("Nummer 0") < b.text.index("Nummer 5"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 24 — an order jumps the queue and is NEVER mixed with anything else
    try:
        v = k.RunQueue(run=lambda b: True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Vorher gesprochener Abschnitt, ganz normal."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Robo, fasse das bitte zusammen.", wake_word=True, reason="wake"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Danach gesprochener Abschnitt, auch normal."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(74, "order comes first", b is not None and b.wake_word, b.text[:50] if b else "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(75, "order not mixed in", b is not None and "Vorher" not in b.text)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b2 = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(76, "rest merged afterwards",
              b2 is not None and "Vorher" in b2.text and "Danach" in b2.text,
              b2.text[:80] if b2 else "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 24b — frequent orders must not starve the transcript
    try:
        v = k.RunQueue(run=lambda b: True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(50):
            v.enqueue(block(f"Robo, Auftrag Nummer {i}, bitte ausfuehren.",
                            wake_word=True, reason="wake"))
            v.enqueue(block(f"Ganz normal gesprochener Abschnitt Nummer {i}."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        auftraege = normal = 0
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for _ in range(200):
            b = v.next_batch(0)
            if b is None:
                break
            if b.wake_word:
                auftraege += 1
            else:
                normal += 1
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(77, "all orders processed", auftraege == 50, str(auftraege))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(78, "transcript doesn't starve next to the orders", normal >= 1, str(normal))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(79, "queue runs empty", v.backlog() == 0, str(v.backlog()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 25 — merging has an upper bound
    try:
        v = k.RunQueue(run=lambda b: True, max_chars=300)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(20):
            v.enqueue(block(f"Block {i} " + "x" * 100))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(80, "cap applies", b is not None and b.chars <= 300 + 120, b.chars if b else 0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(81, "rest stays in the queue", v.next_batch(0) is not None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 25b — a catch-up batch after a restart is chunked to the default
    # cap, not processed as one big turn (in this version the default
    # is still 12000; a later version lowers it to 2000)
    try:
        _chk(82, "the default cap is 2000, not the old 12000", k.MAX_BATCH_CHARS == 2000,
              k.MAX_BATCH_CHARS)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1082, "the default cap is 12000 in this version", k.MAX_BATCH_CHARS == 12000,
              k.MAX_BATCH_CHARS)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v = k.RunQueue(run=lambda b: True)            # no explicit max_chars: default
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        restart_backlog = [block(f"Nachholblock {i} " + "x" * 90) for i in range(150)]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gesamt_chars = sum(b.chars for b in restart_backlog)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for b in restart_backlog:
            v.enqueue(b)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(83, "the simulated backlog is well past the old 12000 cap (~7.5k shape "
             "from the card)", gesamt_chars > 7000, gesamt_chars)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        chunks = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        while v.backlog():
            chunks.append(v.next_batch(0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(84, "the backlog took more than one turn to drain -- it was chunked, "
             "not run as a single oversized turn", len(chunks) > 1, len(chunks))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(85, "every chunk stays within the cap (plus one block's own overhead)",
              all(c.chars <= k.MAX_BATCH_CHARS + 120 for c in chunks),
              [c.chars for c in chunks])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(86, "nothing from the backlog was lost across the chunks",
              sum(c.chars for c in chunks) >= gesamt_chars, None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 26 — the queue doesn't grow unbounded: old backlog gets merged
    try:
        v = k.RunQueue(run=lambda b: True, max_chars=100000)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(500):
            v.enqueue(block(f"Sehr viel geredet, Abschnitt {i}."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(87, "backlog reported", v.backlog() == 500, str(v.backlog()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = v.next_batch(0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(88, "an hour of nonstop talking = one run", v.backlog() == 0, str(v.backlog()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(89, "nothing lost", b is not None
              and all(f"Abschnitt {i}." in b.text for i in range(0, 500, 37)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 27 — if the agent run fails, the block is NOT lost
    try:
        gesehen = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def scheitert_einmal(b):
        gesehen.append(b.text)
        return len(gesehen) > 1          # the first run fails


    try:
        v = k.RunQueue(run=scheitert_einmal)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.process(block("Der wichtige Beschluss von zwoelftausend Euro."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(90, "first run failed", len(gesehen) == 1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.process(block("Und weiter geht es im Text."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(91, "lost block carries into the next run",
              "zwoelftausend" in gesehen[-1], gesehen[-1][:80])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(92, "carry buffer empty after success", not v.carry_text())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 28 — the carry buffer doesn't grow unbounded on persistent failure
    try:
        v = k.RunQueue(run=lambda b: False, carry_max=500)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(50):
            v.process(block(f"Abschnitt {i}: " + "y" * 200))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(93, "carry buffer capped", len(v.carry_text()) <= 600,
              str(len(v.carry_text())))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(94, "the newest survives", "Abschnitt 49" in v.carry_text())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 29 — a failed order stays an order
    try:
        v = k.RunQueue(run=lambda b: False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.process(block("Robo, mach das.", wake_word=True, reason="wake"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gesehen2 = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.run = lambda b: (gesehen2.append(b), True)[1]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.process(block("Ganz normaler Abschnitt danach."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(95, "order flag survives the failure",
              gesehen2 and gesehen2[0].wake_word)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # ---------------------------------------------- second worker thread
    # change: a second worker thread handles ONLY order blocks, so an
    # order no longer has to wait for the single thread's next_batch() loop to
    # come back around after a long-running normal turn. next_call() and
    # next_normal_batch() each touch only their own deque; neither needs
    # `_last_was_call` any more, since starvation-by-interleaving can now only
    # happen on the shared `_run_lock` below, not on which deque gets looked at.

    # 30 — an order is DEQUEUED immediately even while a normal turn is
    # in flight on the other thread. This is the actual 55s-queue-wait fix:
    # in the old single-thread design nothing even LOOKS at `_calls` until the
    # running process() call returns -- see work_loop()'s loop. Here the order
    # thread's next_call() is independent of whatever next_normal_batch()'s
    # caller is doing.
    try:
        normal_started = threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        normal_release = threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _slow_normal(b):
        normal_started.set()
        normal_release.wait(5)
        return True


    try:
        v = k.RunQueue(run=_slow_normal)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Ganz normaler, langer Abschnitt."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stop_30 = threading.Event()
        t_normal = threading.Thread(target=v.work_loop, args=(stop_30,))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t_normal.start()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(96, "normal turn is in flight", normal_started.wait(1))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        t0 = time.monotonic()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        v.enqueue(block("Robo, sofort bitte.", wake_word=True, reason="wake"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        # this version has ONE work loop: it looks at the queue again only
        # after process() of the running normal turn returned
        while time.monotonic() - t0 < 0.3 and v.backlog() > 0:
            time.sleep(0.01)
        dt = time.monotonic() - t0
        pending_97 = v.backlog()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(97, "order dequeued immediately, not after the normal turn",
              pending_97 == 0 and dt < 0.2, f"{dt:.3f}s, backlog {pending_97}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1097, "order stays queued until the normal turn has returned (one work loop)",
              pending_97 == 1 and not normal_release.is_set(), f"backlog {pending_97}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        normal_release.set()
        stop_30.set()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t_normal.join(5)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 31 — this version has ONE worker loop: process() calls never overlap.
    # While a normal turn is in flight, an order is accepted into the queue but
    # its run() only starts after the normal turn returned.
    timeline = []
    normal_hold = threading.Event()
    normal_go = threading.Event()

    def _tracking_run(b):
        timeline.append((b.wake_word, "start", time.monotonic()))
        normal_hold.set()
        if not b.wake_word:
            normal_go.wait(5)
        timeline.append((b.wake_word, "end", time.monotonic()))
        return True

    try:
        v = k.RunQueue(run=_tracking_run)
        v.enqueue(block("Normaler Abschnitt."))
        stop1 = threading.Event()
        t_normal = threading.Thread(target=v.work_loop, args=(stop1,))
        t_normal.start()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(98, "normal run started", normal_hold.wait(1))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        v.enqueue(block("Robo, jetzt.", wake_word=True, reason="wake"))
        b_pending = v.backlog()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(99, "order dequeued while normal still running", b_pending == 0,
              f"backlog {b_pending}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1099, "order is still in the queue while the normal turn runs (one work loop)",
              b_pending == 1, f"backlog {b_pending}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        time.sleep(0.2)
        _chk(100, "order's run() has NOT started yet -- blocked on the shared run lock",
              len(timeline) == 1 and hasattr(v, "_run_lock"), f"{len(timeline)} runs")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(1100, "order's run() has NOT started yet -- it waits behind the normal turn in the queue",
              len(timeline) == 1 and v.backlog() == 1, f"{len(timeline)} runs")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        normal_go.set()
        deadline1 = time.monotonic() + 5
        while time.monotonic() < deadline1 and len(timeline) < 4:
            time.sleep(0.02)
        order_done_ok = len(timeline) == 4
        stop1.set()
        t_normal.join(5)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(101, "order's run() starts right after, never overlapping", order_done_ok)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(102, "no two runs overlapped (normal ended before order started)",
              len(timeline) == 4 and timeline[1][1] == "end"
              and timeline[2][1] == "start" and timeline[1][2] <= timeline[2][2],
              str(timeline))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # 32 — two-thread mode: a flood of orders still doesn't starve the
    # transcript, and nothing deadlocks running both work loops concurrently.
    try:
        orders_seen = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        normal_seen = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        seen_lock = threading.Lock()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _count_run(b):
        with seen_lock:
            (orders_seen if b.wake_word else normal_seen).append(b.text)
        return True


    try:
        v = k.RunQueue(run=_count_run)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for i in range(30):
            v.enqueue(block(f"Robo, Auftrag {i}.", wake_word=True, reason="wake"))
            v.enqueue(block(f"Normaler Abschnitt {i}."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        stop2 = threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t_calls = threading.Thread(target=v.work_loop, args=(stop2,))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t_calls.start()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        deadline = time.monotonic() + 5
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        while time.monotonic() < deadline and v.backlog() > 0:
            time.sleep(0.02)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stop2.set()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        t_calls.join(5)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(103, "all 30 orders processed", len(orders_seen) == 30, str(len(orders_seen)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(104, "all 30 normal blocks processed (merging into fewer runs is fine)",
              sum(t.count("Normaler Abschnitt") for t in normal_seen) == 30,
              str(normal_seen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(105, "queue runs empty", v.backlog() == 0, str(v.backlog()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # ================================================================== prompt
    # The prompt is the contract with the agent. It is checked by asserting on
    # the generated string — never by running an agent.
    try:
        consumer = k
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        Block = k.Block
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_06(sec_05):
    """Scenario: the prompt describes the new contract"""
    global _REC
    _REC = rec = _Recorder()
    global StageState, b, p, st

    try:
        print("\nthe prompt describes the new contract")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    from stage_state import StageState  # noqa: E402
    try:
        st = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st.apply({"action": "notice", "kind": "value_conflict",
                  "text": "**Frist** 6.10. gegen 5.10."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b = Block(sentences=["Also der sechste Oktober gilt."], reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p = consumer.build_prompt(Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"),
                                  b, st, backlog_note="", order_note="")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(106, "the proposal file is named", "vorschlag.jsonl" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(107, "buehne.json is NOT written by the agent any more",
              "buehne.json" not in p, "the prompt still mentions buehne.json")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(108, "the actions are listed",
              all(a in p for a in ('"mirror"', '"decision"', '"notice"',
                                   '"question"', '"resolve"', '"main"')))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(109, "the quote requirement is explicit",
              "woertlich" in p.lower() or "wörtlich" in p.lower())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # N1: following the conversation is the normal case. An agent that is
    # never told about `topic` can only ever leave the surface standing.
    try:
        _chk(110, "the automatic topic change is offered", '"topic"' in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(111, "it is named as the way that needs no order",
              "topic" in p.split("Was wohin gehoert")[1], p[-1500:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(112, "the prompt no longer forbids what N1 asks for",
              "Ungefragt tauschst du die Flaeche nie aus" not in p,
              "the old blanket ban is still in the prompt")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The pointer is client state, so a spoken "show the one before" cannot
    # move it. It is an order like any other and runs the existing path.
    try:
        _chk(113, "a request for an earlier surface is routed to the call-out path",
              "davor" in p and "main" in p.split("davor")[1][:200], p[-1500:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(114, "the open notice is offered with its identity", "N1" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(115, "the open question is offered with its identity", "Q1" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(116, "the notice text is included", "6.10." in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(117, "both notice kinds are explained",
              "value_conflict" in p and "decision_breach" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(118, "no third trigger is invented", "dreimal" not in p.lower())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(119, "the chat ban survives", "Chat" in p and "Telegram" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(120, "append, do not rebuild, survives", "ANBAUEN STATT UMBAUEN" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(121, "nothing new = nothing written survives",
              "NICHTS NEUES" in p or "nichts schreiben" in p.lower())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(122, "the raw transcript stays off limits", "Rohtranskript" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # The meeting's own background material lives in the project folder
    # (Leitfaden, earlier protocol, notes). The agent is told where it is, so a
    # question about the state of things can be answered from the record instead
    # of from guesswork -- but bounded: only when the section demands it, only
    # the one document, and read-only. An unbounded "read the folder" would cost
    # the round the whole latency budget it does not have, and a second writer
    # in that folder is exactly the .conflictN case.
    try:
        _chk(123, "the project folder is named", "PROJEKTORDNER" in p and "/tmp" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(124, "the folder is derived from the protocol path, so they cannot drift",
              "PROJEKTORDNER: /tmp" in p, p[:400])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(125, "reading it is bounded to what the section demands",
              "HINTERGRUND" in p and "gezielt" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(126, "and it is read-only", "Du liest dort NUR" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_07(sec_06):
    """Scenario: the open-state block only carries what is really open"""
    global _REC
    _REC = rec = _Recorder()
    global st_leer, st_offen

    try:
        print("\nthe open-state block only carries what is really open")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_leer = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(127, "nothing open, nothing said", consumer.open_state_block(st_leer) == "",
              repr(consumer.open_state_block(st_leer)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(128, "an empty state adds no heading to the prompt",
              "OFFEN AUF DER BUEHNE" not in consumer.build_prompt(
                  Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b, st_leer))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_offen = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_offen.apply({"action": "notice", "kind": "value_conflict",
                        "text": "**Frist** 6.10. gegen 5.10."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_offen.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(129, "what is open is offered with its identity",
              "N1" in consumer.open_state_block(st_offen)
              and "Q1" in consumer.open_state_block(st_offen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # A resolved entry is still on the stage for the linger time, but the agent
    # must not be asked to resolve it a second time — `resolve` would come back
    # "not open" and the run would be wasted.
    try:
        st_offen.resolve("N1", "der 6.10. gilt")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_offen.resolve("Q1", "der 6.10. gilt")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(130, "a resolved notice drops out of the open-state block",
              "N1" not in consumer.open_state_block(st_offen),
              repr(consumer.open_state_block(st_offen)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(131, "a resolved question drops out too",
              "Q1" not in consumer.open_state_block(st_offen),
              repr(consumer.open_state_block(st_offen)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(132, "with everything resolved the block is empty again",
              consumer.open_state_block(st_offen) == "",
              repr(consumer.open_state_block(st_offen)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_08(sec_07):
    """Scenario: the follow-up supplement rides along on the next run"""
    global _REC
    _REC = rec = _Recorder()
    global p_late, p_quiet, st2, st3, ticks

    try:
        print("\nthe follow-up supplement rides along on the next run")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st2 = StageState(now=lambda: 0.0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st2.apply({"action": "notice", "kind": "value_conflict", "text": "steht"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_quiet = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b, st2,
                                        backlog_note="", order_note="")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(133, "no follow-up without age", "NACHFASSEN" not in p_quiet)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        ticks = [0.0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st3 = StageState(now=lambda: ticks[0])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st3.apply({"action": "notice", "kind": "value_conflict", "text": "steht"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ticks[0] = 200.0
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st3.tick()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_late = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b, st3,
                                      backlog_note="", order_note="")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(134, "the follow-up instruction is in the prompt", "NACHFASSEN" in p_late)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(135, "it names the identity", "N1" in p_late)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_09(sec_08):
    """Scenario: the situational notes still work"""
    global _REC
    _REC = rec = _Recorder()
    global name, p_batch, p_klammern, p_order, text

    try:
        print("\nthe situational notes still work")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_batch = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b, st,
                                        backlog_note=consumer.BACKLOG_NOTE, order_note="")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(136, "batch run names the backlog", "RUECKSTAND" in p_batch)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(137, "a normal block does not", "RUECKSTAND" not in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_order = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b, st,
                                        backlog_note="", order_note=consumer.ORDER_NOTE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(138, "an order run gets the triad", "EINHEIT" in p_order)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # An order swaps the main area, and the main area is only swapped with the
    # instruction verbatim — so the run that is most likely to need `quote` must
    # be the one that names it.
    try:
        _chk(139, "an order run names the quote requirement", "quote" in p_order)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for name, text in (("normal", p), ("batch", p_batch), ("order", p_order)):
            _chk(140, f"{name}: chat ban present", "Chat" in text and "Telegram" in text)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # Spoken text is foreign text: curly braces in it must not blow up the
    # template. The transcript is a VALUE in format(), never part of it.
    try:
        p_klammern = consumer.build_prompt(
            Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
            Block(sentences=['Er sagte {"stand": "x"} dazu.'], reason="pause"), st)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(141, "curly braces in the transcript survive the template",
              '{"stand": "x"}' in p_klammern, p_klammern[-200:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # A backlog run is the one under time pressure — it must not be the one that
    # still talks in the retired v1 vocabulary (`spiegel`, `stand`, `frage`).
    try:
        _chk(142, "batch run uses the current field names",
              all(n in p_batch for n in ("mirror", "headline", "markdown")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(143, "batch run does not use the retired field names",
              not any(n in p_batch for n in ('"spiegel"', "spiegel", '"stand"',
                                             "`frage`")), p_batch[:200])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_10(sec_09):
    """Scenario: the rules the counter-check routes to actually exist"""
    global _REC
    _REC = rec = _Recorder()

    try:
        print("\nthe rules the counter-check routes to actually exist")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(144, "the counter-check routes a value conflict to a notice",
              "value_conflict" in p.split("GEGENPRUEFEN")[1])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(145, "the counter-check no longer names a `frage` action",
              "`frage`" not in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(146, "the counter-check keeps both values",
              "beiden Werten" in p.split("GEGENPRUEFEN")[1])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(147, "the counter-check still asks about rebuilding",
              "nur angebaut" in p.split("GEGENPRUEFEN")[1])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(148, "links are restricted to files that exist",
              "wirklich gibt" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(149, "the main area states its budget",
              "15 Zeilen" in p and ("Archiv" in p or "Volltext" in p))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(150, "`none` is explained where it is listed",
              "nichts Neues" in p.split('"action":"none"')[1][:200])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(151, "the non-executable order names its route",
              "sagst du das in `mirror`" in p)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_11(sec_10):
    """Scenario: B5: the static prompt is sent once per worker session, not per turn"""
    global _REC
    _REC = rec = _Recorder()
    global p_first, p_later

    try:
        print("\nB5: the static prompt is sent once per worker session, not per turn")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The whole point of B5: turn 1 of a session carries the full contract
    # (paths, action schema, the rules), turn 2+ carries only what changed.
    # Checked by asserting on the generated string, exactly like the rest of
    # this section -- never by running an agent.
    try:
        p_first = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b, st,
            backlog_note="", order_note="", first_turn=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_later = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b, st,
            backlog_note="", order_note="", first_turn=False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(152, "first_turn defaults to True (every untouched caller keeps working)",
              consumer.build_prompt(
                  Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b, st,
                  backlog_note="", order_note="")
              == p_first)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # POSITIVKONTROLLE: without B5 (first_turn always True) this exact check
    # fails, because the second call would return the identical full prompt.
    try:
        _chk(153, "turn 1 still carries the instruction (PROTOKOLL path)",
              "PROTOKOLL:  /tmp/protokoll.md" in p_first, p_first[:200])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(154, "turn 1 still carries the action schema",
              '"mirror"' in p_first and '"decision"' in p_first)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(155, "turn 1 still carries the three rules",
              "ANBAUEN STATT UMBAUEN" in p_first)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(156, "turn 2+ does NOT repeat the instruction",
              "PROTOKOLL:" not in p_later, p_later)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(157, "turn 2+ does NOT repeat the action schema",
              '"mirror"' not in p_later and '"decision"' not in p_later, p_later)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(158, "turn 2+ does NOT repeat the three rules",
              "ANBAUEN STATT UMBAUEN" not in p_later, p_later)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # What MUST still be there every turn: the spoken block and the
    # situational state -- these are genuinely new information, not a copy.
    try:
        _chk(159, "turn 2+ still carries the spoken block",
              "Also der sechste Oktober gilt." in p_later, p_later)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(160, "turn 2+ still carries the open notice/question identities",
              "N1" in p_later and "Q1" in p_later, p_later)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(161, "turn 2+ is drastically smaller than turn 1",
              len(p_later) < len(p_first) * 0.3,
              f"turn1={len(p_first)} turn2={len(p_later)}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_12(sec_11):
    """Scenario: the old one-shot path is gone"""
    global _REC
    _REC = rec = _Recorder()

    try:
        print("\nthe old one-shot path is gone")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(162, "agent_lauf is gone", not hasattr(consumer, "agent_lauf"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(163, "GEDAECHTNIS is gone", not hasattr(consumer, "GEDAECHTNIS"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(164, "gedaechtnis_text is gone", not hasattr(consumer, "gedaechtnis_text"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(165, "sitzung.py is gone",
              not (Path(consumer.__file__).parent / "sitzung.py").exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_13(sec_12):
    """Scenario: an order counts as answered only when the SURFACE answered it"""
    global _REC
    _REC = rec = _Recorder()
    global _events

    try:
        print("\nan order counts as answered only when the SURFACE answered it")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Section 7 counts three outcomes of a call-out. Since the automatic topic
    # change, the surface moves on its own -- so "the id changed" stopped being
    # evidence that the order was answered, and an id comparison alone would
    # leave a `topic` that overtook an order with NO event at all: not
    # `call_answered` (that is the call-out path) and not `call_unanswered`
    # either. That is a hole in exactly the measure the core motivation names
    # as its withdrawal signal.
    try:
        _chk(166, "a call-out that replaced the surface counts as answered",
              consumer.answered_the_call("M2", "M1", "zeig die Kosten") is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(167, "a surface that did not move is not an answer",
              consumer.answered_the_call("M1", "M1", "zeig die Kosten") is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(168, "a MOVED surface without a quote is not an answer either -- "
              "that is the topic change, not the order",
              consumer.answered_the_call("M2", "M1", "") is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(169, "and that case is named in the trace as the topic change",
              consumer.unanswered_kind("M2", "M1", "m", "m", "h", "h") == "topic")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(170, "an explanation in the mirror line is still named",
              consumer.unanswered_kind("M1", "M1", "neu", "alt", "h", "h") == "mirror")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(171, "a new headline is still named",
              consumer.unanswered_kind("M1", "M1", "m", "m", "neu", "alt")
              == "headline")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(172, "silence is still silence",
              consumer.unanswered_kind("M1", "M1", "m", "m", "h", "h") == "none")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Every one of them has to pass the trace's own filter -- a kind that is
    # not a token is dropped, and the measurement would be gone.
    import shared.events as _events  # noqa: E402
    try:
        _chk(173, "every kind is a token the trace will actually write",
              all(_events.is_token(k) for k in
                  ("topic", "mirror", "headline", "none")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_14(sec_13):
    """Scenario: session ids survive the path guard"""
    global _REC
    _REC = rec = _Recorder()
    global sid

    try:
        print("\nsession ids survive the path guard")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        sid = consumer.make_session_id("garden-planning-call",
                                       datetime(2026, 9, 8, 10, 0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(174, "no slash", "/" not in sid and "\\" not in sid, sid)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(175, "no parent traversal", ".." not in sid, sid)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(176, "not a drive letter", not (len(sid) > 1 and sid[1] == ":"), sid)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(177, "stable for the same meeting",
              sid == consumer.make_session_id("garden-planning-call",
                                              datetime(2026, 9, 8, 10, 0)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(178, "different per meeting",
              sid != consumer.make_session_id("garden-planning-call",
                                              datetime(2026, 9, 8, 11, 0)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_15(sec_14):
    """Scenario: the consumer is the only writer of buehne.json"""
    global _REC
    _REC = rec = _Recorder()
    global _json, d, raw, st, writer

    try:
        print("\nthe consumer is the only writer of buehne.json")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import json as _json  # noqa: E402
    try:
        d = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer = consumer.StageWriter(d, st, log=lambda *a: None,
                                      proposals=d / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer.set_status("listening", at="19:11:52")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        raw = _json.loads((d / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(179, "schema v2", raw["version"] == 2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(180, "the operating line is in the file", "hört zu" in raw["status"]["text"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(181, "the content fields are there",
              {"mirror", "headline", "main", "decisions", "notices",
               "question", "links"} <= set(raw))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The page counts the running duration from `since`. A naive timestamp is
    # read there as the VIEWER's local time -- and this round meets online, so
    # a reader one timezone away would be shown a duration off by an hour.
    try:
        _chk(182, "since carries a UTC offset",
              datetime.fromisoformat(raw["status"]["since"]).utcoffset() is not None,
              raw["status"]["since"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        st.apply({"action": "mirror", "text": "Robo hört mit."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        raw = _json.loads((d / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(183, "the mirror reached the file", raw["mirror"] == "Robo hört mit.")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(184, "no leftover temp file", not (d / "buehne.json.tmp").exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_16(sec_15):
    """Scenario: no write without a change -- the vault folder is synced"""
    global _REC
    _REC = rec = _Recorder()
    global before

    try:
        print("\nno write without a change -- the vault folder is synced")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        before = (d / "buehne.json").stat().st_mtime_ns
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(185, "a second write with nothing changed does nothing",
              writer.write() is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(186, "the file was not touched",
              (d / "buehne.json").stat().st_mtime_ns == before)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer.set_status("working")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(187, "a status change does write", writer.write() is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st.apply({"action": "decision", "time": "19:43", "text": "kein Pitch"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(188, "a content change does write", writer.write() is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(189, "and immediately settles again", writer.write() is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_17(sec_16):
    """Scenario: proposals reach the stage through the consumer"""
    global _REC
    _REC = rec = _Recorder()
    global discarded, raw

    try:
        print("\nproposals reach the stage through the consumer")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (d / "vorschlag.jsonl").write_text(
            '{"action":"decision","time":"19:43","text":"kein Pitch"}\n'
            'broken\n', encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        discarded = writer.ingest(d / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Two: one applied directly above, one out of the proposal file.
    try:
        _chk(190, "the decision arrived", len(st.decisions) == 2, str(len(st.decisions)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(191, "the broken line was counted", discarded == 1, str(discarded))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        raw = _json.loads((d / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The warning gets a line of its OWN. It used to replace the operating
    # line, and a condition that does not clear itself then cost the round the
    # acknowledgement for the rest of the meeting.
    try:
        _chk(192, "the warning is in the stage file",
              "unlesbar" in raw["status"]["warning"], str(raw["status"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(193, "and the operating line still answers 'did you hear me'",
              "hört zu" in raw["status"]["text"] or "werkelt" in raw["status"]["text"],
              raw["status"]["text"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_18(sec_17):
    """Scenario: three threads write the stage, the file stays readable"""
    global _REC
    _REC = rec = _Recorder()
    global _hammer, _threading, f, faeden, kaputt, st_p, writer_p

    try:
        print("\nthree threads write the stage, the file stays readable")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The ticker, the SSE reader and the agent thread all call write(). They
    # share ONE temporary file name, so without a lock two writes can
    # interleave inside it and os.replace() would then publish the wreck.
    import threading as _threading  # noqa: E402

    try:
        st_p = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer_p = consumer.StageWriter(d, st_p, log=lambda *a: None,
                                        proposals=d / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        kaputt = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _hammer(nr: int) -> None:
        for i in range(40):
            st_p.apply({"action": "mirror", "text": f"Faden {nr} Runde {i} "
                                                    + "x" * 400})
            try:
                writer_p.write()
                _json.loads((d / "buehne.json").read_text(encoding="utf-8"))
            except Exception as e:            # noqa: BLE001
                kaputt.append(repr(e))


    try:
        faeden = [_threading.Thread(target=_hammer, args=(nr,)) for nr in range(8)]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for f in faeden:
            f.start()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for f in faeden:
            f.join(30)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(194, "no half-written stage under concurrent writers", not kaputt,
              "; ".join(kaputt[:3]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(195, "no leftover temp file after the storm",
              not (d / "buehne.json.tmp").exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_19(sec_18):
    """Scenario: the stage is written again when the file vanishes underneath us"""
    global _REC
    _REC = rec = _Recorder()
    global raw, writer_v


    try:
        print("\nthe stage is written again when the file vanishes underneath us")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # At rest the digest never changes, so the change guard alone would keep
    # the consumer from ever writing again once a sync service has renamed
    # `buehne.json` aside -- the exact scenario this whole design exists to
    # survive. The page would then serve a stale or foreign state for the
    # rest of the meeting.
    try:
        writer_v = consumer.StageWriter(d, st, log=lambda *a: None,
                                        proposals=d / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(196, "the first write happens", writer_v.write() is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(197, "nothing changed, nothing written", writer_v.write() is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (d / "buehne.json").rename(d / "buehne.json.conflict1")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(198, "a missing stage file is written again", writer_v.write() is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(199, "and it settles again", writer_v.write() is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (d / "buehne.json").write_text('{"version": 0, "mirror": "fremd"}',
                                       encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(200, "a foreign version is replaced", writer_v.write() is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        raw = _json.loads((d / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(201, "our own state is back in the file", raw["version"] == 2,
              str(raw)[:120])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(202, "and it settles again after the replacement",
              writer_v.write() is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (d / "buehne.json.conflict1").unlink()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_20(sec_19):
    """Scenario: yesterday's proposals do not land on today's stage"""
    global _REC
    _REC = rec = _Recorder()
    global alt, d_alt, fh, st_alt, writer_alt


    try:
        print("\nyesterday's proposals do not land on today's stage")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The proposal file is per meeting, and the offset starts at the END of
    # whatever is already there: a restart mid-meeting must not re-apply
    # everything the stage already shows, and the previous meeting's file
    # must not be replayed at all -- its question would hold the single
    # question slot for ten minutes while today's real notices get parked.
    try:
        d_alt = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_alt, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        alt = d_alt / "vorschlag-2026-09-07.jsonl"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        alt.write_text('{"action":"mirror","text":"Stand von gestern."}\n'
                       '{"action":"question","text":"Frage von gestern?"}\n'
                       '{"action":"decision","time":"19:43","text":"gestern beschlossen"}\n',
                       encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_alt = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer_alt = consumer.StageWriter(d_alt, st_alt, log=lambda *a: None,
                                          proposals=alt)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(203, "a fresh writer reads nothing that was already there",
              writer_alt.ingest() == 0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(204, "no stale mirror", st_alt.mirror == "", repr(st_alt.mirror))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(205, "no stale question", st_alt.question is None, repr(st_alt.question))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(206, "no stale decision", st_alt.decisions == [], repr(st_alt.decisions))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        with alt.open("a", encoding="utf-8") as fh:
            fh.write('{"action":"mirror","text":"Robo hört mit."}\n')
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        writer_alt.ingest()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(207, "what the agent appends afterwards does arrive",
              st_alt.mirror == "Robo hört mit.", repr(st_alt.mirror))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(208, "the ingest defaults to the writer's own proposal file",
              writer_alt.proposals == alt, str(writer_alt.proposals))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_21(sec_20):
    """Scenario: tick() and apply() from two threads at once"""
    global _REC
    _REC = rec = _Recorder()
    global RUNDEN, STAPEL, _race, accepted, eintrag, f, geraeumt, race_errors, race_events, rennen, schaltintervall, schranke, st_race, uhr, verstaut


    try:
        print("\ntick() and apply() from two threads at once")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # tick() runs in the agent thread AND in the ticker at 2 Hz. Both sweep
    # the same settled entries, and the loser of the race got a ValueError
    # out of list.remove(): the ticker swallowed it, the agent thread did
    # not -- there a SUCCESSFUL turn was recorded as failed, its block was
    # sent to the agent a second time (duplicating protocol entries) and the
    # stage stayed stuck on "working".
    #
    # The two threads meet at a barrier with a full sweep waiting for them,
    # and the interpreter is asked to switch threads as often as it can: the
    # window is a handful of bytecodes wide, and at the default switch
    # interval a sweep runs to completion between two switches and the bug
    # never shows.
    try:
        uhr = [0.0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_race = StageState(now=lambda: uhr[0])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        race_errors = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        race_events = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        accepted = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        RUNDEN, STAPEL = 60, 120
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schranke = _threading.Barrier(2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schaltintervall = sys.getswitchinterval()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        sys.setswitchinterval(1e-6)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _race(anwendend: bool) -> None:
        try:
            for runde in range(RUNDEN):
                if anwendend:
                    # Two of them find a free slot, the rest are parked --
                    # and the parked entries are swept by the same tick().
                    for i in range(STAPEL):
                        if st_race.apply({"action": "notice",
                                          "kind": "value_conflict",
                                          "text": f"Meldung {runde}-{i}"}) == "":
                            accepted.append(f"Meldung {runde}-{i}")
                    for eintrag in st_race.open_entries():
                        st_race.resolve(eintrag.id, "geklaert")
                    uhr[0] += 100.0            # everything is ripe for the sweep
                schranke.wait(30)
                race_events.extend(st_race.tick())
                st_race.content()
                schranke.wait(30)
        except Exception as e:                # noqa: BLE001
            race_errors.append(repr(e))
            schranke.abort()


    try:
        rennen = [_threading.Thread(target=_race, args=(True,)),
                  _threading.Thread(target=_race, args=(False,))]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for f in rennen:
            f.start()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for f in rennen:
            f.join(120)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        sys.setswitchinterval(schaltintervall)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for eintrag in st_race.open_entries():          # settle whatever is left
            st_race.resolve(eintrag.id, "geklaert")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        uhr[0] += 100.0
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        race_events.extend(st_race.tick())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(209, "no exception from concurrent tick()/apply()", not race_errors,
              "; ".join(race_errors[:3]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        geraeumt = [e["id"] for e in race_events if e["event"] == "notice_cleared"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(210, "no notice was swept twice", len(geraeumt) == len(set(geraeumt)),
              f"{len(geraeumt)} events, {len(set(geraeumt))} identities")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(211, "no notice was lost", sorted(geraeumt, key=lambda i: int(i[1:]))
              == [f"N{i}" for i in range(1, len(accepted) + 1)],
              f"{len(geraeumt)} cleared of {len(accepted)} accepted")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        verstaut = sum(1 for e in race_events if e["event"] == "parked_cleared")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(212, "no parked entry was lost", verstaut == st_race.parked,
              f"{verstaut} cleared of {st_race.parked} parked")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(213, "the state ends empty",
              not st_race.notices and not st_race.parked_entries,
              f"{len(st_race.notices)} notices, {len(st_race.parked_entries)} parked")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_22(sec_21):
    """Scenario: a poisoned proposal value damages nothing"""
    global _REC
    _REC = rec = _Recorder()
    global d_gift, fh, gift_datei, st_gift, w_gift


    # ======================= C1: one unencodable string may not take anything down
    # `json.loads` accepts lone surrogates, and a truncated emoji from a
    # language model is a routine artefact. Such a value used to reach the
    # state and then raise `UnicodeEncodeError` in write() -- in the SSE
    # reader, whose enclosing `except` read that as a dropped stream and went
    # into a reconnect backoff of up to 30 s, losing everything spoken during
    # it from the raw transcript.
    try:
        print("\na poisoned proposal value damages nothing")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_gift = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_gift, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_gift = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gift_datei = d_gift / "vorschlag-2026-09-08.jsonl"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gift_datei.touch()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_gift = consumer.StageWriter(d_gift, st_gift, log=lambda *a: None,
                                      proposals=gift_datei)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Written as the agent writes it: the escape is ASCII in the file, the
    # lone surrogate only appears after json.loads.
    try:
        with gift_datei.open("a", encoding="utf-8") as fh:
            fh.write('{"action":"mirror","text":"Robo \\ud83d hat verstanden"}\n'
                     '{"action":"decision","time":"19:43","text":"kein Pitch"}\n')
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk_ne(214, "ingesting a lone surrogate does not raise", w_gift.ingest)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(215, "the readable part reached the stage",
              "hat verstanden" in st_gift.mirror, repr(st_gift.mirror))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(216, "the value in the state can be encoded",
              st_gift.mirror.encode("utf-8", "strict") is not None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(217, "the proposal AFTER the poisoned one is still applied",
              [d.text for d in st_gift.decisions] == ["kein Pitch"],
              str(st_gift.decisions))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk_ne(218, "writing the stage does not raise either", w_gift.write)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(219, "the stage file is valid JSON",
              _json.loads((d_gift / "buehne.json").read_text(encoding="utf-8"))["mirror"]
              == st_gift.mirror)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The turn AFTER the poisoned one: prompt building and the next ingest
    # both used to die on the value sitting in the state.
    try:
        _chk_ne(220, "a later prompt is built from the poisoned state",
                           consumer.build_prompt, Path("/tmp/p.md"), gift_datei,
                           block("Weiter geht es."), st_gift)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        with gift_datei.open("a", encoding="utf-8") as fh:
            fh.write('{"action":"headline","text":"Punkt 3"}\n')
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(221, "a subsequent turn still reaches the stage",
              w_gift.ingest() == 0 and st_gift.headline == "Punkt 3",
              repr(st_gift.headline))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_23(sec_22):
    """Scenario: a stage that cannot be written does not take the caller down"""
    global _REC
    _REC = rec = _Recorder()
    global d_weg, d_weg_basis, geschrieben, st_weg, w_weg

    try:
        print("\na stage that cannot be written does not take the caller down")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The same path as a full disk or a read-only mount.
    try:
        d_weg_basis = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_weg_basis, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_weg = d_weg_basis / "gibt-es-nicht"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_weg = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_weg = consumer.StageWriter(d_weg, st_weg, log=lambda *a: None,
                                     proposals=d_weg / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_weg.apply({"action": "mirror", "text": "Robo hört mit."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        geschrieben = _chk_ne(222, "write() to an impossible path does not raise",
                                         w_weg.write)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(223, "and it reports that nothing was written", geschrieben is False,
              str(geschrieben))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(224, "it keeps trying instead of assuming the content is on disk",
              w_weg.write() is False and not d_weg.exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_24(sec_23):
    """Scenario: a main area is only replaced with a quote that was really spoken"""
    global _REC
    _REC = rec = _Recorder()
    global GESAGT, d_zitat, fh, st_zitat, w_zitat, zitat_datei


    # ============================ I1: the call-out quote is checked against speech
    try:
        print("\na main area is only replaced with a quote that was really spoken")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_zitat = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_zitat, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_zitat = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        zitat_datei = d_zitat / "vorschlag-2026-09-08.jsonl"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        zitat_datei.touch()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zitat = consumer.StageWriter(d_zitat, st_zitat, log=lambda *a: None,
                                       proposals=zitat_datei)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        GESAGT = "Wir machen weiter. Robo, zeig uns bitte die vier Straenge."
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        with zitat_datei.open("a", encoding="utf-8") as fh:
            fh.write('{"action":"main","markdown":"## Vier Straenge",'
                     '"quote":"zeig uns bitte die vier Straenge"}\n')
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zitat.ingest(spoken=GESAGT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(225, "the verified call-out replaces the main area",
              st_zitat.main.markdown == "## Vier Straenge", st_zitat.main.markdown)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        with zitat_datei.open("a", encoding="utf-8") as fh:
            fh.write('{"action":"main","markdown":"## Ungefragt",'
                     '"quote":"Robo, tausch bitte die Flaeche aus"}\n')
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zitat.ingest(spoken="Und dann reden wir ueber das Budget.")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(226, "an instruction nobody gave does not buy the main area",
              st_zitat.main.markdown == "## Vier Straenge", st_zitat.main.markdown)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_25(sec_24):
    """Scenario: the prompt names what has to go under `## Offene Fragen`"""
    global _REC
    _REC = rec = _Recorder()
    global p_park, st_park, text


    # ============================== I3: nothing falls off the stage unrecorded
    try:
        print("\nthe prompt names what has to go under `## Offene Fragen`")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_park = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for text in ("erste Meldung", "zweite Meldung", "**Termin** Montag gegen Dienstag"):
            st_park.apply({"action": "notice", "kind": "value_conflict", "text": text})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_park = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                       block("Weiter im Text."), st_park)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(227, "the parked entry is handed to the agent",
              "**Termin** Montag gegen Dienstag" in p_park, p_park[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(228, "it is named as still to be recorded",
              "NOCH NICHT IM PROTOKOLL" in p_park and "Offene Fragen" in p_park,
              p_park[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(229, "the prompt supplement uses the prompt's ASCII transliteration",
              "Buehne" in p_park.split("NOCH NICHT IM PROTOKOLL")[1][:200],
              p_park[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_park.mark_recorded(st_park.unrecorded())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(230, "once a run was told, the next prompt is quiet again",
              "NOCH NICHT IM PROTOKOLL" not in consumer.build_prompt(
                  Path("/tmp/p.md"), Path("/tmp/v.jsonl"), block("Weiter."), st_park))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_26(sec_25):
    """Scenario: the operating line keeps answering while something is wrong"""
    global _REC
    _REC = rec = _Recorder()
    global d_zwei, st_zwei, stand_zwei, w_zwei


    # =============== I5/I6: the warning and the failure keep their own line
    try:
        print("\nthe operating line keeps answering while something is wrong")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_zwei = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_zwei, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_zwei = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei = consumer.StageWriter(d_zwei, st_zwei, log=lambda *a: None,
                                      proposals=d_zwei / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.warn(consumer.warning_text("conflicts"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.set_status("listening", at="19:11:52")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stand_zwei = _json.loads((d_zwei / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(231, "the warning is on its own line",
              "Konfliktdateien" in stand_zwei["status"]["warning"],
              str(stand_zwei["status"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(232, "the acknowledgement still stands beside it",
              stand_zwei["status"]["text"] == "🎧 hört zu · zuletzt 19:11:52",
              stand_zwei["status"]["text"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # I6: a failed run is visible without costing the acknowledgement, and the
    # acknowledgement keeps moving while the failure stands.
    try:
        w_zwei.clear_warning()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.set_status("listening", at="19:12:00", failed=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stand_zwei = _json.loads((d_zwei / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(233, "the failed run is visible", "verschluckt" in stand_zwei["status"]["warning"],
              str(stand_zwei["status"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(234, "and the acknowledgement moved with it",
              stand_zwei["status"]["text"] == "🎧 hört zu · zuletzt 19:12:00",
              stand_zwei["status"]["text"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.set_status("working", failed=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_zwei.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stand_zwei = _json.loads((d_zwei / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(235, "a running turn is shown while the last failure still stands",
              stand_zwei["status"]["state"] == "working"
              and "verschluckt" in stand_zwei["status"]["warning"],
              str(stand_zwei["status"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(236, "a real warning outranks the failure on that line",
              (w_zwei.warn(consumer.warning_text("conflicts")), w_zwei.write(),
               "Konfliktdateien" in _json.loads(
                   (d_zwei / "buehne.json").read_text(encoding="utf-8")
               )["status"]["warning"])[2])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(237, "only the named warning is withdrawn",
              (w_zwei.clear_warning(consumer.warning_text("stream")),
               w_zwei.warning() == consumer.warning_text("conflicts"))[1],
              w_zwei.warning())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_27(sec_26):
    """Scenario: a protocol that cannot be written is not a foreign write"""
    global _REC
    _REC = rec = _Recorder()
    global d_prot, gemeldet, gemeldet2, prot_ro, prot_weg, schreibgeschuetzt, zurueck


    # ================================ I8: a protocol write failure says what it is
    try:
        print("\na protocol that cannot be written is not a foreign write")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gemeldet = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_prot = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_prot, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_weg = k.Protocol(d_prot / "gibt-es-nicht" / "protokoll.md", d_prot,
                              report=gemeldet.append)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_weg.append_raw("Ein Satz, der nirgends landet.")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(238, "a failed write is reported as failed", gemeldet == [False], str(gemeldet))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(239, "restore_missing() does not claim a restoration that failed",
              prot_weg.restore_missing() == 0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # The realistic shape: the file is readable, the append is not possible.
    try:
        if os.geteuid() != 0:
            schreibgeschuetzt = d_prot / "readonly.md"
            schreibgeschuetzt.write_text("## Rohtranskript\n", encoding="utf-8")
            gemeldet2 = []
            prot_ro = k.Protocol(schreibgeschuetzt, d_prot, report=gemeldet2.append)
            k.log = lambda *a: None
            prot_ro.append_raw("Satz eins")
            prot_ro.append_raw("Satz zwei")
            schreibgeschuetzt.write_text("## Rohtranskript\n", encoding="utf-8")
            os.chmod(schreibgeschuetzt, 0o444)
            gemeldet2.clear()
            zurueck = prot_ro.restore_missing()
            k.log = alt_log
            os.chmod(schreibgeschuetzt, 0o644)
            _chk(240, "nothing is reported as restored when the write back failed",
                  zurueck == 0, str(zurueck))
            _chk(241, "the failure is reported instead", gemeldet2 == [False], str(gemeldet2))
            gemeldet2.clear()
            k.log = lambda *a: None
            prot_ro.append_raw("Und jetzt geht es wieder.")
            k.log = alt_log
            _chk(242, "a write that works again is reported as working",
                  gemeldet2 == [True], str(gemeldet2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_28(sec_27):
    """Scenario: the proposal file is covered by both guards"""
    global _REC
    _REC = rec = _Recorder()
    global IGNORE, WACHT, _blockt, _m


    # ============================= I10: the confidential artefacts are covered
    try:
        print("\nthe proposal file is covered by both guards")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _gi = Path(consumer.__file__).parents[1] / ".gitignore"
        IGNORE = _gi.read_text(encoding="utf-8") if _gi.exists() else ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(243, "the proposal file is git-ignored", "vorschlag-*.jsonl" in IGNORE, IGNORE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(244, "the event trace is git-ignored too", "stage-events-*.jsonl" in IGNORE,
              IGNORE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(245, "the timing trace is git-ignored too", "timing.jsonl" in IGNORE, IGNORE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        WACHT = (Path(consumer.__file__).parents[1]
                 / "scripts" / "pre-commit-leak-guard.sh").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _m = re.search(r"^VERBOTEN='(.+)'$", WACHT, re.MULTILINE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(246, "the leak guard has a forbidden-path pattern", _m is not None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if _m:
            def _blockt(name: str) -> bool:
                return subprocess.run(["grep", "-qEi", _m.group(1)],
                                      input=name, text=True).returncode == 0
            _chk(247, "the leak guard blocks the proposal file",
                  _blockt("vorschlag-2026-09-08.jsonl"))
            _chk(248, "... also in a subfolder",
                  _blockt("entities/x/projekte/y/vorschlag-2026-09-08.jsonl"))
            _chk(249, "the protocol stays blocked", _blockt("protokoll-2026-09-08.md"))
            _chk(250, "an ordinary source file is not blocked",
                  not _blockt("gateway/consumer.py"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_29(sec_28):
    """Scenario: a restart takes the running meeting's stage back over"""
    global _REC
    _REC = rec = _Recorder()
    global d_leer, d_neu, heute_tag, st_gestern, st_kaputt, st_leer2, st_nachher, st_vorher, w_gestern, w_kaputt, w_leer2, w_nachher, w_vorher, zusammenfassung


    # ======================== I11: a restart keeps the stage and the identities
    try:
        print("\na restart takes the running meeting's stage back over")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d_neu = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_neu, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_vorher = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_vorher = consumer.StageWriter(d_neu, st_vorher, log=lambda *a: None,
                                        proposals=d_neu / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_vorher.apply({"action": "mirror", "text": "Robo hat das Budget verstanden."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_vorher.apply({"action": "decision", "time": "19:43", "text": "kein Pitch"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_vorher.apply({"action": "notice", "kind": "value_conflict",
                         "text": "**Frist** 6.10. gegen 5.10."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_vorher.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_vorher.set_status("listening", at="19:11:52")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_vorher.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        heute_tag = datetime.now().date().isoformat()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        st_nachher = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_nachher = consumer.StageWriter(d_neu, st_nachher, log=lambda *a: None,
                                         proposals=d_neu / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        zusammenfassung = consumer.restore_stage(w_nachher, st_nachher, heute_tag,
                                                 log=lambda *a: None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(251, "the restart restores the surface", zusammenfassung is not None
              and st_nachher.mirror == "Robo hat das Budget verstanden.",
              repr(st_nachher.mirror))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(252, "the decision is still there",
              [d.text for d in st_nachher.decisions] == ["kein Pitch"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(253, "the notice is still there and still open",
              len(st_nachher.notices) == 1 and st_nachher.notices[0].state == "open")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(254, "the question is still there", st_nachher.question is not None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(255, "no identity is handed out a second time",
              st_nachher.apply({"action": "notice", "kind": "value_conflict",
                                "text": "neu"}) == ""
              and st_nachher.notices[-1].id == "N2",
              str([n.id for n in st_nachher.notices]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(256, "nothing is warned about after a clean restore", w_nachher.warning() == "",
              w_nachher.warning())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        st_gestern = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_gestern = consumer.StageWriter(d_neu, st_gestern, log=lambda *a: None,
                                         proposals=d_neu / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(257, "a stage from another day is not taken over",
              consumer.restore_stage(w_gestern, st_gestern, "2026-01-01",
                                     log=lambda *a: None) is None
              and st_gestern.mirror == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(258, "and that is not an alarm", w_gestern.warning() == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        (d_neu / "buehne.json").write_text("{kaputt", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_kaputt = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_kaputt = consumer.StageWriter(d_neu, st_kaputt, log=lambda *a: None,
                                        proposals=d_neu / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(259, "an unreadable stage file is not restored silently",
              consumer.restore_stage(w_kaputt, st_kaputt, heute_tag,
                                     log=lambda *a: None) is None
              and "Bühne" in w_kaputt.warning(), w_kaputt.warning())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        d_leer = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, d_leer, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_leer2 = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_leer2 = consumer.StageWriter(d_leer, st_leer2, log=lambda *a: None,
                                       proposals=d_leer / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(260, "a first start has nothing to restore and says nothing",
              consumer.restore_stage(w_leer2, st_leer2, heute_tag,
                                     log=lambda *a: None) is None
              and w_leer2.warning() == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_30(sec_29):
    """Scenario: the live path: block -> worker -> proposals -> stage"""
    global _REC
    _REC = rec = _Recorder()
    global BaseHTTPRequestHandler, LIVE_TAKT, REPO, SAETZE, ThreadingHTTPServer, WORKER_SRC, _block, _live, _roh_live, _signal, arbeitet, ausgabe, beginn, buehne, frist, gesendete_prompts, heute, lauf, p, port, projekt, prompt_log, protokolle, schluss, schluss_wache, srv, stand, statusverlauf, statuswache, stt_server, turn1, turn2, turns_bisher, turns_gesehen, vault, vault_bauen, waehrend, workerdir, workerskript


    # ================================================== the live path, end to end
    # The one thing only the running consumer proves: a block reaches the
    # worker, the worker's answer comes back, the proposals are read and the
    # stage is written -- by the consumer, from its own state. The worker is
    # the fake (tests/fake_worker.py); no Hermes is imported anywhere.
    try:
        print("\nthe live path: block -> worker -> proposals -> stage")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    import signal as _signal  # noqa: E402
    from http.server import (BaseHTTPRequestHandler,  # noqa: E402
                             ThreadingHTTPServer)

    try:
        REPO = Path(__file__).resolve().parents[1]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        SAETZE = ["Wir haben insgesamt zwoelftausend Euro fuer das Projekt.",
                  "Die Frist fuer den Antrag ist der sechste Oktober.",
                  "Und der Bericht geht dann Ende Oktober raus."]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The third sentence lands WHILE the agent is thinking. That is the case
    # that used to wipe the working line off the stage.
    try:
        LIVE_TAKT = [(SAETZE[0], 0.05), (SAETZE[1], 1.0), (SAETZE[2], 0.3)]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # A worker that appends its proposals DURING the turn, into the path the
    # PROMPT names it -- not a file that was already lying there. That
    # distinction is the whole point: a proposal file written before the
    # start proves nothing about the run, and it is how a replayed file from
    # the previous meeting stayed unnoticed. It also has to sleep, so the
    # stage can be caught mid-turn showing "⚙️ Robo werkelt".
    try:
        WORKER_SRC = '''\
import argparse, json, re, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--session-id", required=True)
ap.add_argument("--delay", type=float, default=0.0)
ap.add_argument("--restart", action="store_true")
a = ap.parse_args()


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
    sys.stdout.flush()


send({"type": "ready", "session_id": a.session_id})
first = True
turn_no = 0
vorschlaege_pfad = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("type") != "turn":
        continue
    turn_no += 1
    time.sleep(a.delay)
    treffer = re.search(r"^VORSCHLAEGE: (.+)$", msg.get("prompt") or "",
                        re.MULTILINE)
    if treffer:
        # B5: this line is only in the FULL prompt (turn 1). A real
        # agent has the path from the conversation history after that;
        # this fake worker mimics the same thing with a variable instead
        # of re-parsing a line that later turns deliberately omit.
        vorschlaege_pfad = treffer.group(1).strip()
    if vorschlaege_pfad:
        # B5: log every prompt this fake worker actually received, so
        # the test can assert turn 1 carries the full contract and
        # turn 2 does not -- exactly what a real worker session sees.
        log_pfad = vorschlaege_pfad.rsplit(".", 1)[0] + ".prompts.log"
        with open(log_pfad, "a", encoding="utf-8") as lg:
            lg.write(f"===TURN {turn_no}===\\n" + (msg.get("prompt") or "")
                     + "\\n===END===\\n")
    if first and vorschlaege_pfad:
        first = False
        # The decision lands before any topic (implicit `Beginn` block),
        # then an accepted topic opens `### HH:MM Budget und Fristen` and
        # the tagged entry lands in it. `--restart`: the second run on the
        # same, already existing v2 file appends one more entry.
        if a.restart:
            aktionen = ({"action": "tag_entry", "tag": "notiz",
                         "text": "nach dem Neustart weiter"},)
        else:
            aktionen = (
                {"action": "mirror", "text": "Robo hat das Budget verstanden."},
                {"action": "decision", "time": "19:43",
                 "text": "zwoelftausend Euro stehen"},
                {"action": "topic", "title": "Budget und Fristen",
                 "markdown": "## Budget und Fristen"},
                {"action": "tag_entry", "tag": "aufgabe",
                 "text": "Antrag bis sechsten Oktober"})
        with open(vorschlaege_pfad, "a", encoding="utf-8") as fh:
            for aktion in aktionen:
                fh.write(json.dumps(aktion, ensure_ascii=False) + "\\n")
            fh.flush()
    send({"type": "result", "id": msg.get("id"), "ok": True, "failed": False,
          "partial": False, "final": "OK \\u2014 mitgeschrieben", "error": None})
'''
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def stt_server(takt):
        """A fake STT server: `takt` is [(sentence, pause after it), …].

    The pauses matter: whether an utterance arrives WHILE a turn runs is
    the difference between a stage that keeps saying "Robo werkelt" and
    one that overwrites it with "hört zu".
    """

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
                    for satz, pause in takt:
                        self.wfile.write(
                            f"data: {_json.dumps({'recognizer_id': 'left', 'type': 'final', 'text': satz})}\n\n"
                            .encode())
                        self.wfile.flush()
                        _zeit.sleep(pause)
                    while True:                 # then keep the stream open
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        _zeit.sleep(0.3)
                except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _STT)
        server.daemon_threads = True
        _threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]


    def vault_bauen(prefix: str) -> tuple[Path, Path]:
        tmp = Path(_mkdtemp(prefix=prefix))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        projekt = tmp / "entities" / "testentity" / "projekte" / "testprojekt"
        projekt.mkdir(parents=True)
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "Test"]):
            subprocess.run(["git", *args], cwd=tmp, capture_output=True)
        return tmp, projekt


    def statuswache(datei: Path, stop: _threading.Event) -> list:
        """Every distinct operating line the stage showed, with the moment.

    Entries are `(t, state, text, since, ticking)`; `t` is monotonic, so
    the test can ask what the stage said WHILE the agent was thinking.
    """
        gesehen: list = []

        def lauf() -> None:
            while not stop.is_set():
                try:
                    s = _json.loads(datei.read_text(encoding="utf-8")).get("status")
                except (OSError, ValueError, AttributeError):
                    s = None
                if isinstance(s, dict):
                    eintrag = (s.get("state"), s.get("text"), s.get("since"),
                               s.get("ticking"))
                    if not gesehen or gesehen[-1][1:] != eintrag:
                        gesehen.append((_zeit.monotonic(), *eintrag))
                _zeit.sleep(0.05)

        _threading.Thread(target=lauf, daemon=True).start()
        return gesehen


    try:
        srv, port = stt_server(LIVE_TAKT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        vault, projekt = vault_bauen("cothinker-live-")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The worker script sits in a directory WITH A SPACE: the command line is
    # split with shlex, so a quoted path stays one argument. Split on
    # whitespace it would become two, and the worker would never start.
    try:
        workerdir = vault / "agent bin"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        workerdir.mkdir()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        workerskript = workerdir / "fake worker.py"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        workerskript.write_text(WORKER_SRC, encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # One folder per meeting. No `--title` is passed, so the folder is named
    # after the project (`gateway/consumer.py::session_paths`).
    try:
        lauf = projekt / "sessions" / f"{datetime.now().date().isoformat()}-testprojekt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        buehne = lauf / "buehne.json"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schluss_wache = _threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        statusverlauf = statuswache(buehne, schluss_wache)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p = subprocess.Popen(
            [sys.executable, str(REPO / "gateway" / "consumer.py"),
             "--stt", f"http://127.0.0.1:{port}", "--project", "testprojekt",
             "--pause", "0.4", "--retry-delay", "0.2", "--commit-every", "100000",
             "--check-every", "0", "--worker-command",
             f'"{sys.executable}" "{workerskript}" --delay 2.0'],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ, COTHINKER_ENTITIES=str(vault / "entities"),
                     PYTHONUNBUFFERED="1"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        stand = {}
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        frist = _zeit.monotonic() + 60
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prompt_log = lauf / "vorschlag.prompts.log"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        while _zeit.monotonic() < frist:
            if buehne.exists():
                try:
                    stand = _json.loads(buehne.read_text(encoding="utf-8"))
                except ValueError:
                    stand = {}
                # B5: wait for the SECOND turn too, not just the first decision --
                # the third sentence (SAETZE[2]) is what a delta-only turn 2 has
                # to carry, and it only lands after the fake worker's second
                # "ready for input" cycle.
                turns_bisher = (prompt_log.read_text(encoding="utf-8").count("===TURN ")
                                if prompt_log.exists() else 0)
                if (any(e.get("text") == "zwoelftausend Euro stehen"
                        for e in stand.get("decisions", []))
                        and turns_bisher >= 2):
                    break
            if p.poll() is not None:
                break
            _zeit.sleep(0.2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(261, "the consumer wrote the stage at all", buehne.exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(262, "the stage is schema v2", stand.get("version") == 2, str(stand)[:120])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(263, "the proposal reached the stage through a real run",
              any(e.get("text") == "zwoelftausend Euro stehen"
                  for e in stand.get("decisions", [])), str(stand.get("decisions")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(264, "the consumer assigned the identity, not the agent",
              all(e.get("id") for e in stand.get("decisions", [])),
              str(stand.get("decisions")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(265, "the mirror came from the same file",
              stand.get("mirror") == "Robo hat das Budget verstanden.",
              repr(stand.get("mirror")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(266, "the worker started from a path containing a space",
              stand.get("mirror") == "Robo hat das Budget verstanden.",
              repr(stand.get("mirror")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # The proposal file no longer carries the date in its NAME -- the session
    # folder does. What the date protected against still has to hold: the
    # first run of the next meeting must not replay this one's file, and that
    # is now guaranteed by the folder, one per meeting.
    try:
        heute = datetime.now().date().isoformat()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(267, "the proposal file lies in the meeting's own folder",
              (lauf / "vorschlag.jsonl").exists(),
              str(sorted(x.name for x in lauf.iterdir()) if lauf.is_dir() else
                  sorted(x.name for x in projekt.iterdir())))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(268, "the folder is dated, so the next meeting gets its own",
              lauf.name.startswith(heute), lauf.name)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(269, "no shared vorschlag.jsonl is left behind in the project folder",
              not (projekt / "vorschlag.jsonl").exists())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # B5: the fake worker logged every prompt it actually received (see
    # WORKER_SRC above). Three sentences with pauses long enough to cut
    # separate blocks (LIVE_TAKT) mean at least two turns ran in this one
    # worker session -- the real thing B5 changes.
    try:
        prompt_log = lauf / "vorschlag.prompts.log"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gesendete_prompts = (prompt_log.read_text(encoding="utf-8")
                             if prompt_log.exists() else "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        turns_gesehen = gesendete_prompts.count("===TURN ")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(270, "at least two turns ran in this worker session",
              turns_gesehen >= 2, f"{turns_gesehen} turns: {gesendete_prompts[:200]}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if turns_gesehen >= 2:
            turn1 = gesendete_prompts.split("===TURN 1===\n", 1)[1].split(
                "\n===END===", 1)[0]
            turn2 = gesendete_prompts.split("===TURN 2===\n", 1)[1].split(
                "\n===END===", 1)[0]
            _chk(271, "turn 1 carries the full instruction",
                  "PROTOKOLL:" in turn1 and '"mirror"' in turn1, turn1[:200])
            _chk(272, "turn 2 does NOT repeat the instruction",
                  "PROTOKOLL:" not in turn2, turn2)
            _chk(273, "turn 2 does NOT repeat the action schema",
                  '"mirror"' not in turn2, turn2)
            _chk(274, "turn 2 still carries the spoken block (new information)",
                  any(s in turn2 for s in SAETZE[1:]), turn2)
            _chk(275, "turn 2 is drastically smaller than turn 1",
                  len(turn2) < len(turn1) * 0.3,
                  f"turn1={len(turn1)} turn2={len(turn2)}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p.send_signal(_signal.SIGINT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            ausgabe = p.communicate(timeout=60)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            ausgabe = p.communicate()[0]
            _chk(277, "consumer exits on Ctrl-C", False, "timeout")
        else:
            _chk(276, "consumer exits on Ctrl-C", p.returncode == 0,
                  f"return value {p.returncode}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        srv.shutdown()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schluss_wache.set()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # FIX 2: the block is popped BEFORE the run, so the queue cannot report a
    # turn in flight. Without a separate signal the listening refresh of every
    # utterance overwrote "⚙️ Robo werkelt" with "🎧 hört zu" -- the round lost
    # exactly the signal that tells a thinking agent from a crashed one, and
    # `ticking` never reached the page, so the duration never ran.
    try:
        arbeitet = [e for e in statusverlauf if e[1] == "working"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(278, "the round sees that a turn is running", bool(arbeitet),
              str(statusverlauf[-4:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(279, "the working line asks the page to count the duration",
              all(e[4] is True for e in arbeitet), str(arbeitet[:3]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The third sentence arrives about half a second into a two-second turn.
    # Its acknowledgement used to overwrite the working line; now the
    # precedence in status_line.pick() keeps the turn on top.
    try:
        if arbeitet:
            beginn = arbeitet[0][0]
            waehrend = [e for e in statusverlauf if beginn <= e[0] <= beginn + 1.5]
            _chk(280, "an utterance during the turn does not wipe the working line",
                  all(e[1] == "working" for e in waehrend),
                  str([(round(e[0] - beginn, 2), e[1]) for e in waehrend]))
            _chk(281, "the duration is counted from ONE moment per turn",
                  len({e[3] for e in waehrend}) == 1, str({e[3] for e in waehrend}))
            _chk(282, "the listening line comes back after the turn",
                  any(e[1] == "listening"
                      for e in statusverlauf[statusverlauf.index(arbeitet[-1]) + 1:]),
                  str(statusverlauf[-3:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(283, "the worker answered", "run failed" not in ausgabe, ausgabe[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(284, "no unhandled exception", "Traceback" not in ausgabe, ausgabe[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(285, "the transitional 'no proposals are read' note is gone",
              "nothing reads it" not in ausgabe, ausgabe[:400])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schluss = _json.loads(buehne.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(286, "the meeting ends on the listening line",
              "hört zu" in schluss.get("status", {}).get("text", ""),
              str(schluss.get("status")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        protokolle = list(lauf.glob("protokoll.md"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(287, "the raw transcript belongs to the consumer alone",
              bool(protokolle) and all(
                  s in protokolle[0].read_text(encoding="utf-8") for s in SAETZE),
              str(protokolle))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _live = protokolle[0].read_text(encoding="utf-8") if protokolle else ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(288, "a fresh meeting writes protocol v2",
              "## Themen" in _live and "protokoll_format: 2" in _live, _live[:400])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(289, "the accepted decision was filed by the consumer as beschluss",
              "- 19:43 [beschluss] zwoelftausend Euro stehen\n" in _live
              and "- 19:43 zwoelftausend Euro stehen (Thema: Beginn)" in _live,
              _live[:1500])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _block = re.search(r"^### (\d\d:\d\d) Budget und Fristen\n"
                           r"- (\d\d:\d\d) \[aufgabe\] Antrag bis sechsten Oktober\n",
                           _live, re.M)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(290, "the accepted topic opened `### HH:MM Titel`, the tag_entry landed in it",
              _block is not None, _live[:2000])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(291, "topic block and entry sit between ## Themen and ## Rohtranskript",
              _block is not None
              and _live.index("## Themen") < _block.start() < _live.index("## Rohtranskript"),
              _live[:2000])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _roh_live = _live[_live.index("## Rohtranskript"):] if "## Rohtranskript" in _live else ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(292, "## Rohtranskript is intact: one heading, every sentence, in order",
              _live.count("## Rohtranskript") == 1
              and [z.split(":* ", 1)[-1] for z in _roh_live.splitlines()
                   if z.startswith("- **")] == SAETZE
              and all(_roh_live.find(SAETZE[i]) < _roh_live.find(SAETZE[i + 1])
                      for i in range(len(SAETZE) - 1)), _roh_live)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_31(sec_30):
    """Scenario: restart on an existing v2 protocol: it keeps appending"""
    global _REC
    _REC = rec = _Recorder()
    global NEUSTART_SATZ, _nach, _roh_nach, _themen_nachher, _themen_vorher, ausgabe_r, frist_r, p_r, port_r, srv_r

    # A restart on the SAME, already existing v2 file: it is continued, not
    # re-created, and new entries are appended below what is there.
    try:
        print("\nrestart on an existing v2 protocol: it keeps appending")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        NEUSTART_SATZ = "Nach dem Neustart geht die Besprechung einfach weiter."
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        srv_r, port_r = stt_server([(NEUSTART_SATZ, 0.2)])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_r = subprocess.Popen(
            [sys.executable, str(REPO / "gateway" / "consumer.py"),
             "--stt", f"http://127.0.0.1:{port_r}", "--project", "testprojekt",
             "--mode", "arbeit",
             "--pause", "0.4", "--retry-delay", "0.2", "--commit-every", "100000",
             "--check-every", "0", "--worker-command",
             f'"{sys.executable}" "{workerskript}" --delay 0.2 --restart'],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ, COTHINKER_ENTITIES=str(vault / "entities"),
                     PYTHONUNBUFFERED="1"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _nach = ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        frist_r = _zeit.monotonic() + 60
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        while _zeit.monotonic() < frist_r and p_r.poll() is None:
            try:
                _nach = protokolle[0].read_text(encoding="utf-8")
            except (OSError, IndexError):
                _nach = ""
            if "[notiz] nach dem Neustart weiter" in _nach and NEUSTART_SATZ in _nach:
                break
            _zeit.sleep(0.2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_r.send_signal(_signal.SIGINT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            ausgabe_r = p_r.communicate(timeout=60)[0]
        except subprocess.TimeoutExpired:
            p_r.kill()
            ausgabe_r = p_r.communicate()[0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        srv_r.shutdown()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _nach = protokolle[0].read_text(encoding="utf-8") if protokolle else ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(293, "the restart continues the file (appending, format 2)",
              "appending to:" in ausgabe_r and "protocol format: 2" in ausgabe_r,
              ausgabe_r[-600:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(294, "the file's modus wins over the restart's --mode flag",
              "created as sitzung" in ausgabe_r and "### Beschlüsse" in _nach
              and "### Ideen" not in _nach, ausgabe_r[-600:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _themen_vorher = _live[_live.index("## Themen"):_live.index("## Rohtranskript")].rstrip("\n")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _themen_nachher = _nach[_nach.index("## Themen"):_nach.index("## Rohtranskript")]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(295, "the topic blocks from before the restart are unchanged, a prefix",
              _themen_nachher.startswith(_themen_vorher), _themen_nachher)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(296, "the new entry is appended after them",
              re.search(r"\[aufgabe\] Antrag bis sechsten Oktober\n"
                        r"(- .*\n)*- \d\d:\d\d \[notiz\] nach dem Neustart weiter\n",
                        _themen_nachher) is not None, _themen_nachher)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(297, "the decision is still filed once after the restart",
              _nach.count("[beschluss] zwoelftausend Euro stehen") == 1, _themen_nachher)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _roh_nach = _nach[_nach.index("## Rohtranskript"):]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(298, "the raw section keeps the old lines and appends the new sentence",
              _roh_nach.startswith(_roh_live.rstrip("\n")) and NEUSTART_SATZ in _roh_nach
              and _nach.count("## Rohtranskript") == 1, _roh_nach)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(299, "the restart raised no unhandled exception",
              "Traceback" not in ausgabe_r, ausgabe_r[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if "--zeigen" in sys.argv:
            print(ausgabe)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_32(sec_31):
    """Scenario: without an agent: the acknowledgement keeps running, the conflict warning follows the facts"""
    global _REC
    _REC = rec = _Recorder()
    global TROCKEN, _lines2, _trace2, ausgabe2, buehne2, konfliktdatei, lauf2, mit_warnung, noch_da, ohne_warnung, p2, port2, projekt2, srv2, vault2, verlauf2, wache2_stop, warte_auf, zuhoeren


    # ============================== the acknowledgement without an agent, and
    # ============================== a conflict file that appears mid-meeting
    # Two things only a second run shows: under --dry-agent (and --no-agent)
    # no worker ever lives, and the acknowledgement used to be gated on
    # `worker.alive()` -- it froze for the whole run, in exactly the mode
    # where it is the only thing the stage can offer. And conflict files are
    # the realistic case of appearing DURING the meeting, while the check ran
    # once at startup and the first successful turn cleared the warning
    # although it was still true.
    try:
        print("\nwithout an agent: the acknowledgement keeps running, "
              "the conflict warning follows the facts")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # The second sentence is a call-out: the dry run answers nothing, so it
    # is the one live path that shows a `main` announcement start AND end.
    try:
        TROCKEN = ["Erster Satz fuer den Trockenlauf, lang genug zum Schneiden.",
                   "Robo, fasse den Trockenlauf bitte kurz zusammen.",
                   "Dritter Satz fuer den Trockenlauf, damit es weitergeht.",
                   "Vierter Satz fuer den Trockenlauf, zum guten Schluss."]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        srv2, port2 = stt_server([(satz, 1.1) for satz in TROCKEN])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vault2, projekt2 = vault_bauen("cothinker-dry-")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        lauf2 = projekt2 / "sessions" / f"{datetime.now().date().isoformat()}-testprojekt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        buehne2 = lauf2 / "buehne.json"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        wache2_stop = _threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        verlauf2 = statuswache(buehne2, wache2_stop)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p2 = subprocess.Popen(
            [sys.executable, str(REPO / "gateway" / "consumer.py"),
             "--stt", f"http://127.0.0.1:{port2}", "--project", "testprojekt",
             "--dry-agent", "--pause", "0.4", "--retry-delay", "0.2",
             "--commit-every", "100000", "--check-every", "1"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ, COTHINKER_ENTITIES=str(vault2 / "entities"),
                     PYTHONUNBUFFERED="1"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def warte_auf(pruefung, frist_s: float) -> dict:
        """Poll the stage until `pruefung(stand)` holds, or time is up."""
        ende = _zeit.monotonic() + frist_s
        letzter: dict = {}
        while _zeit.monotonic() < ende and p2.poll() is None:
            try:
                letzter = _json.loads(buehne2.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                letzter = {}
            if letzter and pruefung(letzter):
                return letzter
            _zeit.sleep(0.1)
        return letzter


    # A sync service drops a conflict file into the folder while the meeting
    # is running -- nobody restarts the consumer for that.
    try:
        warte_auf(lambda s: bool(s.get("status")), 20)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        konfliktdatei = projekt2 / "notiz.conflict1.md"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        konfliktdatei.write_text("fremde Fassung\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        mit_warnung = warte_auf(
            lambda s: "Konfliktdateien" in s.get("status", {}).get("warning", ""), 20)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(300, "a conflict file appearing mid-meeting is announced",
              "Konfliktdateien" in mit_warnung.get("status", {}).get("warning", ""),
              str(mit_warnung.get("status")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # I5: the warning must not cost the three answers the operating line exists
    # for. One conflict file used to blank it for the whole meeting.
    try:
        # a turn may be in flight when the warning first shows ("werkelt");
        # wait until the warning and the idle line are on the stage together
        mit_warnung = warte_auf(
            lambda s: "Konfliktdateien" in s.get("status", {}).get("warning", "")
            and "hört zu" in s.get("status", {}).get("text", ""), 20)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(301, "the operating line survives the warning",
              "hört zu" in mit_warnung.get("status", {}).get("text", "")
              and "Konfliktdateien" in mit_warnung.get("status", {}).get("warning", ""),
              str(mit_warnung.get("status")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # Turns keep succeeding while the file is still there. A successful turn
    # clears the warning -- and the re-check has to raise it again, because
    # the sync service did not stop writing just because a run went well.
    try:
        _zeit.sleep(2.5)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        noch_da = _json.loads(buehne2.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(302, "the warning survives a successful turn while it is still true",
              "Konfliktdateien" in noch_da.get("status", {}).get("warning", ""),
              str(noch_da.get("status")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        konfliktdatei.unlink()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        ohne_warnung = warte_auf(
            lambda s: "Konfliktdateien" not in s.get("status", {}).get("warning", ""),
            20)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(303, "a warning that is no longer true is withdrawn",
              "Konfliktdateien" not in ohne_warnung.get("status", {}).get("warning", ""),
              str(ohne_warnung.get("status")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p2.send_signal(_signal.SIGINT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            ausgabe2 = p2.communicate(timeout=60)[0]
        except subprocess.TimeoutExpired:
            p2.kill()
            ausgabe2 = p2.communicate()[0]
            _chk(305, "the dry run exits on Ctrl-C", False, "timeout")
        else:
            _chk(304, "the dry run exits on Ctrl-C", p2.returncode == 0,
                  f"return value {p2.returncode}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        srv2.shutdown()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        wache2_stop.set()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        zuhoeren = [e[2] for e in verlauf2 if e[1] == "listening"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(306, "the acknowledgement keeps moving without any worker",
              len(set(zuhoeren)) >= 2, str(sorted(set(zuhoeren))[:3]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(307, "turns really ran in the dry run", ausgabe2.count("agent (") >= 2,
              ausgabe2[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(308, "no unhandled exception in the dry run", "Traceback" not in ausgabe2,
              ausgabe2[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # The call-out was announced the moment its block closed, and the dry run
    # -- which answers nothing -- ended the announcement as unanswered.
    try:
        _trace2 = lauf2 / "stage-events.jsonl"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lines2 = ([_json.loads(z) for z in _trace2.read_text(encoding="utf-8")
                     .splitlines()] if _trace2.exists() else [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(309, "a call-out announces a main entry in the trace",
              any(z["event"] == "pending_start" and z.get("kind") == "main"
                  for z in _lines2),
              str([z["event"] for z in _lines2][:20]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(310, "an unanswered call-out ends its announcement as unanswered",
              any(z["event"] == "pending_end" and z.get("kind") == "main"
                  and z.get("outcome") == "unanswered"
                  and isinstance(z.get("duration_s"), (int, float))
                  for z in _lines2),
              str([z for z in _lines2 if z["event"].startswith("pending")]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if "--zeigen" in sys.argv:
            print(ausgabe2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_33(sec_32):
    """Scenario: a dropped speech-to-text stream is announced and withdrawn"""
    global _REC
    _REC = rec = _Recorder()
    global ausgabe3, buehne3, flaky_stt, frist3, lauf3, p3, port3, projekt3, srv3, vault3, verbindungen, wache3_stop, warnverlauf, warnwache


    # ============================ I7: a dropped stream is visible to the round
    # It was logged and retried with backoff while the stage kept showing
    # "hört zu" with a frozen timestamp -- speech was being lost and the
    # surface said everything was fine.
    try:
        print("\na dropped speech-to-text stream is announced and withdrawn")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        verbindungen = [0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def flaky_stt():
        """Drops the FIRST connection at once, serves every one after it."""

        class _Flaky(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != "/events":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                nummer = verbindungen[0]
                verbindungen[0] += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if nummer == 0:
                    return                      # dropped before a single event
                try:
                    while True:
                        self.wfile.write(
                            f"data: {_json.dumps({'type': 'final', 'text': 'Der Antrag ist raus, sagt the content lead.'})}\n\n"
                            .encode())
                        self.wfile.flush()
                        _zeit.sleep(0.4)
                except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Flaky)
        server.daemon_threads = True
        _threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]


    def warnwache(datei: Path, stop: _threading.Event) -> list:
        """Every distinct pair of (trouble line, operating line) shown."""
        gesehen: list = []

        def lauf() -> None:
            while not stop.is_set():
                try:
                    s = _json.loads(datei.read_text(encoding="utf-8")).get("status")
                except (OSError, ValueError, AttributeError):
                    s = None
                if isinstance(s, dict):
                    eintrag = (s.get("warning", ""), s.get("text", ""))
                    if not gesehen or gesehen[-1] != eintrag:
                        gesehen.append(eintrag)
                _zeit.sleep(0.05)

        _threading.Thread(target=lauf, daemon=True).start()
        return gesehen


    try:
        srv3, port3 = flaky_stt()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vault3, projekt3 = vault_bauen("cothinker-drop-")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        lauf3 = projekt3 / "sessions" / f"{datetime.now().date().isoformat()}-testprojekt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        buehne3 = lauf3 / "buehne.json"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        wache3_stop = _threading.Event()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        warnverlauf = warnwache(buehne3, wache3_stop)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p3 = subprocess.Popen(
            [sys.executable, str(REPO / "gateway" / "consumer.py"),
             "--stt", f"http://127.0.0.1:{port3}", "--project", "testprojekt",
             "--no-agent", "--pause", "0.4", "--retry-delay", "1.0",
             "--commit-every", "100000", "--check-every", "0"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ, COTHINKER_ENTITIES=str(vault3 / "entities"),
                     PYTHONUNBUFFERED="1"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        frist3 = _zeit.monotonic() + 30
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        while _zeit.monotonic() < frist3 and p3.poll() is None:
            if (any("abgerissen" in w for w, _ in warnverlauf)
                    and warnverlauf[-1][0] == "" and verbindungen[0] > 1):
                break
            _zeit.sleep(0.1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p3.send_signal(_signal.SIGINT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            ausgabe3 = p3.communicate(timeout=60)[0]
        except subprocess.TimeoutExpired:
            p3.kill()
            ausgabe3 = p3.communicate()[0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        srv3.shutdown()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        wache3_stop.set()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(311, "the dropped stream reaches the stage",
              any("abgerissen" in w for w, _ in warnverlauf), str(warnverlauf[:4]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(312, "the acknowledgement is not blanked by it",
              all(t for w, t in warnverlauf if "abgerissen" in w), str(warnverlauf[:4]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(313, "the warning is withdrawn once the stream is back",
              bool(warnverlauf) and warnverlauf[-1][0] == "", str(warnverlauf[-3:]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(314, "the reader kept reading", verbindungen[0] > 1, str(verbindungen[0]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(315, "no unhandled exception while the stream dropped",
              "Traceback" not in ausgabe3, ausgabe3[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        if "--zeigen" in sys.argv:
            print(ausgabe3)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_34(sec_33):
    """Scenario: the graph action: the consumer builds, the state only holds"""
    global _REC
    _REC = rec = _Recorder()
    global _P, _tf, _v, aktion, fehler, schreiber, zustand

    try:
        print("the graph action: the consumer builds, the state only holds")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import tempfile as _tf                                            # noqa: E402
    from pathlib import Path as _P                                    # noqa: E402

    try:
        _v = _P(_mkdtemp(prefix="cothinker-cgraph-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, _v, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (_v / "sitzung").mkdir()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Stem is deliberately slug-UNSTABLE (slugify("01_attractor") == "01-attractor",
    # verified against gateway.consumer.slugify) -- a slug-stable stem like "saat"
    # cannot tell whether build_graph_action() actually passes the raw candidate
    # through to find_or_create_seed() (I2): raw_name and the slugified file_name
    # would be the same string, so a caller that drops raw_name entirely would
    # still pass this test. See review, run 111.
    try:
        (_v / "01_attractor.md").write_text("---\ntags: [t]\n---\n[[ziel]]\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (_v / "ziel.md").write_text("---\ntags: [t]\n---\nx\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        zustand = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        schreiber = consumer.StageWriter(_v / "sitzung", zustand, log=lambda *a: None,
                                         proposals=_v / "sitzung" / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(316, "without a vault a graph action is refused, not guessed",
              schreiber.build_graph_action({"action": "graph", "title": "T",
                                            "seed": "01_attractor"}) == "no vault",
              "a graph appeared without a configured vault")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        schreiber.attach_vault(_v, session_dir=_v / "sitzung", datum="2026-09-15",
                               teilnehmende="the operator")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        aktion = {"action": "graph", "title": "Thema", "seed": "01_attractor"}
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        fehler = schreiber.build_graph_action(aktion)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(317, "with a vault the payload is filled in by the consumer",
              fehler == "" and aktion["graph"]["seed"] == "01_attractor.md"
              and len(aktion["graph"]["nodes"]) == 2, f"{fehler} {aktion.get('graph')}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(318, "the state takes it and the surface is a graph",
              zustand.apply(aktion) == "" and zustand.main.kind == "graph",
              zustand.main.kind)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(319, "the trace reports the graph, with numbers only",
              consumer.applied_event(zustand, aktion)
              == {"event": "graph_changed", "id": zustand.main.id, "count": 2},
              str(consumer.applied_event(zustand, aktion)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_35(sec_34):
    """Scenario: an announcement is written immediately, and traced without text"""
    global _REC
    _REC = rec = _Recorder()
    global _clock_ann, _dir_ann, _ends, _file_ann, _lines_ann, _pid, _start, _state_ann, _trace_ann, _writer_ann

    # The announcement reaches the file AT ONCE -- the acceptance criterion is
    # under 500 ms, and the ticker alone would cost up to half of that -- and
    # its trace lines survive the trace's own filter without a word of text.
    try:
        print("\nan announcement is written immediately, and traced without text")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _dir_ann = _P(_mkdtemp(prefix="cothinker-pending-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, _dir_ann, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _clock_ann = [5000.0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _state_ann = StageState(now=lambda: _clock_ann[0])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _trace_ann = _events.EventLog(_dir_ann / "stage-events.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _writer_ann = consumer.StageWriter(_dir_ann, _state_ann, log=lambda *a: None,
                                      proposals=_dir_ann / "vorschlag.jsonl",
                                      events=_trace_ann)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _writer_ann.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pid = _writer_ann.announce("main", "Auftrag angenommen")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _file_ann = _json.loads((_dir_ann / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(320, "announce returns the id", _pid == "P1", _pid)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(321, "the pending entry is in buehne.json right after the call",
              _file_ann.get("pending") == [
                  {"kind": "main", "label": "Auftrag angenommen",
                   "since": _state_ann.pending[0].since_label, "id": "P1"}],
              str(_file_ann.get("pending")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(322, "an unknown kind announces nothing and writes nothing new",
              _writer_ann.announce("vibes", "x") == "" and len(_state_ann.pending) == 1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _clock_ann[0] += 4.2
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _state_ann.settle(_pid, "unanswered")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _trace_ann.record_all(_state_ann.tick())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _lines_ann = [_json.loads(z) for z in (_dir_ann / "stage-events.jsonl")
                       .read_text(encoding="utf-8").splitlines()]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _start = [z for z in _lines_ann if z["event"] == "pending_start"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ends = [z for z in _lines_ann if z["event"] == "pending_end"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(323, "pending_start reached the trace with id and kind",
              len(_start) == 1 and _start[0].get("id") == "P1"
              and _start[0].get("kind") == "main", str(_start))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(324, "pending_end reached the trace with outcome and duration",
              len(_ends) == 1 and _ends[0].get("outcome") == "unanswered"
              and _ends[0].get("duration_s") == 4.2, str(_ends))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(325, "the trace carries no label",
              "Auftrag" not in (_dir_ann / "stage-events.jsonl").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_36(sec_35):
    """Scenario: a call-out's announcement names the WHAT, the trace stays text-free"""
    global _REC
    _REC = rec = _Recorder()
    global _blk_cz, _blk_cz2, _dir_cz, _file_cz, _file_cz2, _labels_cz, _labels_cz2, _pid_cz, _state_cz, _trace_cz, _writer_cz

    # MM-L: a call-out's announcement names the WHAT -- "Zuruf:" plus at most
    # three content words of the block, the wake word dropped. The canary is
    # the block's first content word: on the stage, never in the trace.
    try:
        print("\na call-out's announcement names the WHAT, the trace stays text-free")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _dir_cz = _P(_mkdtemp(prefix="cothinker-callout-label-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _state_cz = StageState(now=lambda: 7000.0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _writer_cz = consumer.StageWriter(
            _dir_cz, _state_cz, log=lambda *a: None,
            proposals=_dir_cz / "vorschlag.jsonl",
            events=_events.EventLog(_dir_cz / "stage-events.jsonl"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _writer_cz.write()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _blk_cz = consumer.Block(
            sentences=["Robo, Zwiebelkuchenkanarienvogel fuer den Basar bestellen",
                       "und bitte gleich die Liste der Helfer schicken."],
            start=10.0, end=14.0, reason="wake", wake_word=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pid_cz = consumer.CallOutAnnouncements().announce(_writer_cz, _blk_cz)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _file_cz = _json.loads((_dir_cz / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _labels_cz = [p.get("label") for p in _file_cz.get("pending", [])]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(326, "the call-out's label is Zuruf: plus <= 3 content words",
              _pid_cz != "" and _labels_cz == [
                  "Zuruf: Zwiebelkuchenkanarienvogel fuer Basar"], str(_labels_cz))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(327, "the call-out's label is not the block",
              _blk_cz.text not in str(_labels_cz))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _trace_cz = (_dir_cz / "stage-events.jsonl").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(328, "the call-out canary is on the stage but not in the trace",
              "pending_start" in _trace_cz
              and "Zwiebelkuchenkanarienvogel" not in _trace_cz)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _blk_cz2 = consumer.Block(
            sentences=["Der Gartenverein diskutiert die Satzung schon seit einer Stunde.",
                       "Dann kommt noch das Sommerfest dazu.",
                       "Robo, zeig uns die Liste der Beetpaten."],
            start=30.0, end=60.0, reason="wake", wake_word=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        consumer.CallOutAnnouncements().announce(_writer_cz, _blk_cz2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _file_cz2 = _json.loads((_dir_cz / "buehne.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _labels_cz2 = [p.get("label") for p in _file_cz2.get("pending", [])]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(329, "a wake word in the LAST sentence: the label names the order, "
              "not the earlier talk",
              "Zuruf: zeig Liste Beetpaten" in _labels_cz2
              and not any("Gartenverein" in str(x) for x in _labels_cz2[-1:]),
              str(_labels_cz2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(330, "a call-out block without content words keeps the old label",
              consumer.CallOutAnnouncements().announce(
                  _writer_cz, consumer.Block(sentences=["Robo, ähm."], start=20.0,
                                             end=21.0, wake_word=True)) != ""
              and _state_cz.pending[-1].label == "Auftrag angenommen")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_37(sec_36):
    """Scenario: a call-out run settles its own announcement, never a waiting one"""
    global _REC
    _REC = rec = _Recorder()
    global _behaviour, _blk, _c_blk, _call, _clock_co, _co, _co2, _d_co, _e_blk, _events_co, _outcomes, _outcomes2, _p1, _p2, _p_c, _p_d, _p_e, _q_co, _q_co2, _run, _run2, _st_co, _st_co2, _w_co, _w_co2

    # The real sequence: `block_done()` announces BEFORE the enqueue, and the
    # RunQueue hands out orders one at a time. A second call-out waiting in the
    # queue has its announcement standing while the first one runs -- and the
    # first run must end only its OWN. The run function below does exactly
    # what `run_turn()` does with it: take the block's id, settle it after.
    try:
        print("\na call-out run settles its own announcement, never a waiting one")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _call(nr: int) -> consumer.Block:
        """A call-out block with its own sentence times, as the builder cuts it."""
        return consumer.Block(sentences=[f"Robo, Auftrag {nr} bitte."],
                              start=100.0 * nr, end=100.0 * nr + 2.0,
                              reason="wake", wake_word=True)


    try:
        _clock_co = [0.0]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_co = StageState(now=lambda: _clock_co[0])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _d_co = _P(_mkdtemp(prefix="cothinker-callout-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, _d_co, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _w_co = consumer.StageWriter(_d_co, _st_co, log=lambda *a: None,
                                     proposals=_d_co / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _co = consumer.CallOutAnnouncements()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _outcomes = []            # (outcome, the block's own id) per run
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _behaviour = {"answered": True, "ok": True, "during": None, "run_s": 90.0}
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _events_co = []            # every event the state reported
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _run(b) -> bool:
        own = _co.take(b) if b.wake_word else ""
        _clock_co[0] += _behaviour["run_s"]     # 90 s outlives the 60 s timeout
        _events_co.extend(_st_co.tick())
        if _behaviour["during"] is not None:  # a call-out closes DURING the run
            _co.announce(_w_co, _behaviour["during"])
            _q_co.enqueue(_behaviour["during"])
            _behaviour["during"] = None
        _outcomes.append((consumer.settle_call_out(
            _st_co, own, answered=_behaviour["answered"],
            ok=_behaviour["ok"]), own))
        _events_co.extend(_st_co.tick())
        return _behaviour["ok"]


    try:
        _q_co = consumer.RunQueue(run=_run)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # Two call-outs closed back to back: both announced, both queued. The
    # queue hands out the first alone (orders are never merged), and when its
    # run is over the second is still waiting -- so is its announcement.
    try:
        _st_co2 = StageState(now=lambda: 0.0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _w_co2 = consumer.StageWriter(_d_co, _st_co2, log=lambda *a: None,
                                      proposals=_d_co / "vorschlag.jsonl")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _co2 = consumer.CallOutAnnouncements()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _outcomes2 = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _run2(b) -> bool:
        _outcomes2.append(consumer.settle_call_out(
            _st_co2, _co2.take(b), answered=True, ok=True))
        return True


    try:
        _q_co2 = consumer.RunQueue(run=_run2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for _blk in (_call(1), _call(2)):
            _co2.announce(_w_co2, _blk)
            _q_co2.enqueue(_blk)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _p1, _p2 = (p.id for p in _st_co2.pending)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co2.process(_q_co2.next_batch(timeout=0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(331, "the first call-out's run ends only its own announcement",
              [p.id for p in _st_co2.pending] == [_p2] and _outcomes2 == ["arrived"]
              and _q_co2.backlog() == 1,
              f"{_st_co2.pending} {_outcomes2} backlog {_q_co2.backlog()}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co2.process(_q_co2.next_batch(timeout=0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(332, "the second call-out's run ends the second",
              _st_co2.pending == [], str(_st_co2.pending))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # The reviewer's timing: A's announcement times out during A's run, B
    # closes during it -- A's answer must not end B's.
    try:
        _c_blk = _call(3)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _co.announce(_w_co, _c_blk)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co.enqueue(_c_blk)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _p_c = _st_co.pending[-1].id
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _behaviour["during"] = _call(4)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co.process(_q_co.next_batch(timeout=0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _p_d = _st_co.pending[-1].id if _st_co.pending else ""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(333, "a call-out announced during a run survives that run's answer",
              _p_d and _p_d != _p_c and [p.id for p in _st_co.pending] == [_p_d],
              f"{_st_co.pending} {_outcomes}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    # A failed run: `failed`, and the carry-over wraps the NEXT call-out in a
    # new Block -- whose announcement is still found by its sentence times.
    try:
        _behaviour.update(answered=False, ok=False, run_s=5.0)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co.process(_q_co.next_batch(timeout=0))       # D's run fails
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(334, "a failed run ends its own announcement as failed",
              _outcomes[-1] == ("failed", _p_d) and _st_co.pending == []
              and {"event": "pending_end", "id": _p_d, "kind": "main",
                   "outcome": "failed", "duration_s": 5.0} in _events_co,
              f"{_outcomes} {[e for e in _events_co if e.get('id') == _p_d]}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _behaviour.update(answered=False, ok=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _e_blk = _call(5)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _co.announce(_w_co, _e_blk)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co.enqueue(_e_blk)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _p_e = _st_co.pending[-1].id
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_co.process(_q_co.next_batch(timeout=0))       # carried into E's run
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(335, "a call-out wrapped with carried-over text still finds its own",
              _outcomes[-1] == ("unanswered", _p_e) and _st_co.pending == [],
              f"{_outcomes} {_st_co.pending}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(336, "every outcome is a token the trace will write",
              all(_events.is_token(t) for t in ("arrived", "unanswered", "failed",
                                                "discarded", "displaced")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_38(sec_37):
    """Scenario: protocol v2: the consumer is the only writer of protokoll.md"""
    global _REC
    _REC = rec = _Recorder()
    global _HEAD, _agenda_datei, _gelesen, _gespleisst, _kaputt, _pd, _v2, kopf, kopf_ablauf, kopf_arbeit, p_alt, p_neu, prot_v1, prot_v2, spur, v2_datei, v2_schreiber, vorschlagen

    # ======================= protocol v2: the consumer files the tagged entries
    try:
        print("\nprotocol v2: the consumer is the only writer of protokoll.md")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import shared.minutes_format as _pd  # noqa: E402

    try:
        _v2 = Path(_mkdtemp(prefix="cothinker-v2-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        atexit.register(shutil.rmtree, _v2, ignore_errors=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _HEAD = dict(title="Synthetischer Termin", entity="beispiel", day="2026-09-26",
                     pslug="beispiel", participants="A, B", start="10:00")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


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


    try:
        kopf = consumer.protocol_header(**_HEAD, mode="sitzung")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(337, "a new protocol is v2", _pd.is_v2(kopf) and "protokoll_format: 2" in kopf
              and "modus: sitzung" in kopf, kopf[:300])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(338, "the sitzung header is HEADER_V2 verbatim",
              kopf == consumer.HEADER_V2.format(**_HEAD, mode="sitzung"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(339, "and its view is exactly what render_view gives an empty protocol",
              _pd.splice(kopf, view=_pd.render_view([], "sitzung"), append="") == kopf)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        kopf_arbeit = consumer.protocol_header(**_HEAD, mode="arbeit")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(340, "another mode gets its own sections from the start",
              "### Ideen" in kopf_arbeit and "### Beschlüsse" not in kopf_arbeit
              and "modus: arbeit" in kopf_arbeit, kopf_arbeit[:600])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        kopf_ablauf = consumer.protocol_header(
            **_HEAD, mode="arbeit", agenda=["Budget", "# Bewässerung", "   "])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(341, "an agenda becomes ## Ablauf right above ## Überblick",
              "**Start:** 10:00\n\n## Ablauf\n\n- 1. Budget\n- 2. Bewässerung\n\n"
              "## Überblick\n" in kopf_ablauf, kopf_ablauf[:900])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(342, "a protocol with an agenda is still v2", _pd.is_v2(kopf_ablauf))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _gespleisst = _pd.splice(kopf_ablauf, view=_pd.render_view([], "arbeit"),
                                 append="### 10:05 Budget\n- 10:05 [idee] erfunden\n")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(343, "splice keeps the agenda byte-identical",
              _gespleisst.split("## Überblick")[0] == kopf_ablauf.split("## Überblick")[0])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(344, "without an agenda nothing changes",
              consumer.protocol_header(**_HEAD, mode="sitzung", agenda=[])
              == consumer.HEADER_V2.format(**_HEAD, mode="sitzung"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _agenda_datei = _v2 / "agenda.txt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _agenda_datei.write_text("# Kommentar\n1. Rückblick\n\n2) Budget\n"
                                 + "".join(f"Punkt {n}\n" for n in range(20)),
                                 encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _gelesen = consumer.read_agenda(str(_agenda_datei))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(345, "read_agenda drops comments, blanks and numbering, caps the list",
              _gelesen[:3] == ["Rückblick", "Budget", "Punkt 0"]
              and len(_gelesen) == consumer.MAX_AGENDA_ITEMS, repr(_gelesen))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(346, "read_agenda('') is empty", consumer.read_agenda("") == [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            consumer.read_agenda(str(_v2 / "fehlt.txt"))
            _chk(347, "a missing agenda file raises OSError", False)
        except OSError:
            _chk(348, "a missing agenda file raises OSError", True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(349, "--agenda exists", "--agenda" in Path(consumer.__file__).read_text(
            encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _kaputt = _v2 / "agenda-kaputt.txt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _kaputt.write_bytes(b"1. R\xfcckblick\n\xff\xfe Budget\n")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            consumer.read_agenda(str(_kaputt))
            _chk(350, "an agenda with invalid UTF-8 raises UnicodeDecodeError", False)
        except UnicodeDecodeError:
            _chk(351, "an agenda with invalid UTF-8 raises UnicodeDecodeError", True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(352, "main() turns an undecodable agenda into a start-up error",
              "except (OSError, UnicodeDecodeError) as e:\n        raise SystemExit("
              "f\"agenda not readable" in Path(consumer.__file__).read_text(
                  encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(353, "--mode exists, choices from protocol_doc, default sitzung",
              "--mode" in Path(consumer.__file__).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        p_neu = v2_datei("neu.md")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_v2 = k.Protocol(p_neu, _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(354, "the protocol recognises a v2 file", prot_v2.is_v2 is True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_alt = _v2 / "alt.md"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_alt.write_text(consumer.HEADER.format(**_HEAD), encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_v1 = k.Protocol(p_alt, _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(355, "an existing v1 file stays v1", prot_v1.is_v2 is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(356, "a missing file is not v2 and does not raise",
              k.Protocol(_v2 / "fehlt" / "x.md", _v2).is_v2 is False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec




@pytest.fixture(scope="module")
def sec_40(sec_38):
    """Scenario: v1: a tag_entry is refused, the file is not touched"""
    global _REC
    _REC = rec = _Recorder()
    global alt_inhalt, d1, st1, vor1, w1

    try:
        print("\nv1: a tag_entry is refused, the file is not touched")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w1, st1, d1 = v2_schreiber("w1", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        alt_inhalt = p_alt.read_bytes()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vor1 = w1.applied
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w1, d1, {"action": "tag_entry", "tag": "aufgabe", "text": "X"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(362, "refused as v1_protocol",
              any(e["event"] == "proposal_rejected" and e.get("reason") == "v1_protocol"
                  for e in spur(d1 / "events.jsonl")), str(spur(d1 / "events.jsonl")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(363, "the v1 file is byte-identical", p_alt.read_bytes() == alt_inhalt)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(364, "a refused tag_entry is not a surface change", w1.applied == vor1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec






@pytest.fixture(scope="module")
def sec_43(sec_40):
    """Scenario: v2: what fell off the stage is filed by the consumer itself"""
    global _REC
    _REC = rec = _Recorder()
    global _, d_u, gefiled, offen, p_v1, prot_u, st_park_v2, st_u, t, text_u, w_u, w_u1

    try:
        print("\nv2: what fell off the stage is filed by the consumer itself")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_u = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for t in ("erste Meldung", "zweite Meldung", "dritte Meldung"):
            st_u.apply({"action": "notice", "kind": "value_conflict", "text": t})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_u._remember_unrecorded("expired", "Welche Frist gilt?")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_u = k.Protocol(v2_datei("u.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_u, _, d_u = v2_schreiber("wu", prot_u)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        offen = st_u.unrecorded()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        gefiled = w_u.file_unrecorded(offen)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_u.mark_recorded(gefiled)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_u = prot_u.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(396, "a parked notice is filed as frage",
              re.search(r"\[frage\] geparkt: dritte Meldung\n", text_u) is not None, text_u)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(397, "an expired question is filed as frage",
              "[frage] abgelaufen: Welche Frist gilt?\n" in text_u, text_u)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(398, "and both are marked recorded", st_u.unrecorded() == [], str(st_u.unrecorded()))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(399, "the v2 prompt does not ask the agent to file them",
              "NOCH NICHT IM PROTOKOLL" not in consumer.open_state_block(
                  st_park_v2 := StageState(), include_unrecorded=False))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_park_v2._remember_unrecorded("parked", "x")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(400, "open_state_block(include_unrecorded=False) omits the part",
              "NOCH NICHT" not in consumer.open_state_block(st_park_v2,
                                                            include_unrecorded=False)
              and "NOCH NICHT" in consumer.open_state_block(st_park_v2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_v1 = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                     block("Weiter."), st_park_v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(401, "build_prompt(fmt=1) is unchanged",
              p_v1 == consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                            block("Weiter."), st_park_v2, fmt=1)
              and "NOCH NICHT IM PROTOKOLL" in p_v1)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(402, "build_prompt(fmt=2) omits it, first and later turns",
              "NOCH NICHT" not in consumer.build_prompt(
                  Path("/tmp/p.md"), Path("/tmp/v.jsonl"), block("Weiter."),
                  st_park_v2, fmt=2)
              and "NOCH NICHT" not in consumer.build_prompt(
                  Path("/tmp/p.md"), Path("/tmp/v.jsonl"), block("Weiter."),
                  st_park_v2, fmt=2, first_turn=False))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_u1, _, _ = v2_schreiber("wu1", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(403, "under v1 nothing is filed by the consumer",
              w_u1.file_unrecorded(st_park_v2.unrecorded()) == [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec




@pytest.fixture(scope="module")
def sec_45(sec_43):
    """Scenario: v2 prompt: the agent proposes tagged entries, the protocol is read-only"""
    global _REC
    _REC = rec = _Recorder()
    global V1_SECTIONS, _graph_regel, b_p2, p2, p_viele, st_p2, viele

    # ======================================================= protocol v2: prompt
    try:
        print("\nv2 prompt: the agent proposes tagged entries, the protocol is read-only")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        V1_SECTIONS = ("## Entscheidungen", "## Aufgaben", "## Offene Fragen", "## Verlauf")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_p2 = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_p2.apply({"action": "notice", "kind": "value_conflict",
                     "text": "**Frist** 6.10. gegen 5.10."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_p2 = block("Dann machen wir das so.")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p2 = consumer.build_prompt(Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"),
                                   b_p2, st_p2, fmt=2,
                                   file_names=["kostenplan", "leitfaden-2026"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(410, "v2 first turn is AGENT_PROMPT_V2, not the v1 text",
              "Du bist Robo" in p2 and p2 != consumer.build_prompt(
                  Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_p2, st_p2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(411, "it offers the tag_entry action", '"action":"tag_entry"' in p2, p2[:400])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(412, "it names all six tags", all(t in p2 for t in _pd.TAGS))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(413, "each tag has a meaning line",
              all(re.search(rf"^\s*-?\s*`{t}`", p2, re.M) for t in _pd.TAGS),
              str([t for t in _pd.TAGS if not re.search(rf"^\s*-?\s*`{t}`", p2, re.M)]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(414, "no v1 section is a write target any more",
              not any(s in p2 for s in V1_SECTIONS),
              str([s for s in V1_SECTIONS if s in p2]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(415, "the protocol is read-only for the agent", "Das Protokoll liest du NUR" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(416, "only the proposal file is written",
              "VORSCHLAEGE: /tmp/vorschlag.jsonl" in p2 and "PROTOKOLL:  /tmp/protokoll.md" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(417, "decisions go through `decision` only, never also as tag_entry",
              "nie zusaetzlich als `tag_entry`" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(418, "a topic opens the new topic block", "Themenblock" in p2.split('"action":"topic"')[-1])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _graph_regel = " ".join(p2.split("   - `graph`, wenn", 1)[-1].split("\n   - ", 1)[0].split())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(419, "graph never replaces topic: on a topic change, topic goes FIRST",
              "ersetzt `topic` nie" in _graph_regel
              and "schick ZUERST `topic`" in _graph_regel, _graph_regel)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(420, "the v1 prompt does not carry the v2 graph/topic order rule",
              "ersetzt `topic` nie" not in consumer.AGENT_PROMPT)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(421, "the decision_breach notice points at the overview",
              "Ueberblick unter Beschluesse" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(422, "at most ONE question, the rest as tag_entry frage",
              "jede weitere als `tag_entry` mit Tag `frage`" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(423, "done work becomes a notiz entry starting **erledigt**",
              "**erledigt**" in p2 and "`notiz`" in p2.split("**erledigt**")[0][-200:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(424, "nothing new = only the none line", '{"action":"none"}' in p2
              and "NICHTS NEUES" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(425, "the file list is there", "DATEIEN IM PROJEKTORDNER" in p2
              and "kostenplan" in p2 and "leitfaden-2026" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(426, "the wikilink rule names only listed files", "[[dateiname]]" in p2
              and "nur Namen aus der Liste" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(427, "the rules from the real meeting survive",
              all(s in p2 for s in ("ANBAUEN STATT UMBAUEN", "FRAGEN NUR, WENN RATEN SCHADET",
                                    "AUCH OHNE WECKWORT", "GEGENPRUEFEN",
                                    "sagst du das in `mirror`", "Chat", "Telegram",
                                    '"OK — ')))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(428, "the stage actions are all still offered",
              all(f'"action":"{a}"' in p2 for a in ("mirror", "headline", "decision",
                                                    "notice", "question", "resolve",
                                                    "main", "topic", "graph", "links",
                                                    "none")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(429, "the open entries still ride along", "N1" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(430, "the raw transcript stays off limits", "## Rohtranskript" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(431, "the prompt is ASCII-transliterated prose (no umlauts outside headings)",
              not re.search("[äöüÄÖÜß]", p2.replace("## Überblick", "")),
              str(re.findall(".{20}[äöüÄÖÜß].{5}", p2)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(432, "braces in file names survive the template",
              "a{b}c" in consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                               b_p2, st_p2, fmt=2, file_names=["a{b}c"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(433, "no file names -> the list says so",
              "(keine)" in consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                                 b_p2, st_p2, fmt=2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        viele = [f"datei{i:03d}" for i in range(100)]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_viele = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                                        b_p2, st_p2, fmt=2, file_names=viele)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(434, "the file list is capped at 60",
              "datei059" in p_viele and "datei060" not in p_viele)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(435, "a name that cannot be a link is not offered",
              "zwei\nzeilen" not in consumer.build_prompt(
                  Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_p2, fmt=2,
                  file_names=["zwei\nzeilen", "ok"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_46(sec_45):
    """Scenario: v2 order note: the triad without v1 sections"""
    global _REC
    _REC = rec = _Recorder()

    try:
        print("\nv2 order note: the triad without v1 sections")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(436, "ORDER_NOTE_V2 names no v1 section",
              not any(s in consumer.ORDER_NOTE_V2 for s in V1_SECTIONS))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(437, "it keeps the triad and the quote rule",
              all(s in consumer.ORDER_NOTE_V2 for s in ("AUFTRAG", "EINHEIT",
                                                        "BESCHLUSSLAGE", "quote",
                                                        "KOPIERVORGANG")))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(438, "it routes the result to a tag_entry", "tag_entry" in consumer.ORDER_NOTE_V2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(439, "the v1 order note is untouched", "## Entscheidungen" in consumer.ORDER_NOTE
              and "## Aufgaben" in consumer.ORDER_NOTE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_47(sec_46):
    """Scenario: later turns: DELTA_PROMPT, shared by both formats"""
    global _REC
    _REC = rec = _Recorder()
    global d1_, d2_, st_d

    try:
        print("\nlater turns: DELTA_PROMPT, shared by both formats")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_d = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d1_ = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_d,
                                    first_turn=False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        d2_ = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_d,
                                    first_turn=False, fmt=2, file_names=["x"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(440, "v1 and v2 later turns are the same DELTA_PROMPT",
              d1_ == d2_ == consumer.DELTA_PROMPT.format(
                  situation="", open_state="", thread="", block=b_p2.text), repr(d2_[:200]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_48(sec_47):
    """Scenario: v1 prompt unchanged"""
    global _REC
    _REC = rec = _Recorder()
    global p_v1a

    try:
        print("\nv1 prompt unchanged")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_v1a = consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(441, "fmt=1 ignores file_names", p_v1a == consumer.build_prompt(
            Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_p2, fmt=1,
            file_names=["kostenplan"]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(442, "fmt=1 first turn is exactly AGENT_PROMPT.format(...) (byte for byte)",
              p_v1a == consumer.AGENT_PROMPT.format(
                  path=Path("/tmp/p.md"), proposals=Path("/tmp/v.jsonl"),
                  block=b_p2.text, folder=Path("/tmp"),
                  situation=st_p2.follow_up_note(),
                  open_state=consumer.open_state_block(st_p2)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(443, "the v1 order run is AGENT_PROMPT with ORDER_NOTE in the situation",
              consumer.build_prompt(Path("/tmp/p.md"), Path("/tmp/v.jsonl"), b_p2, st_p2,
                                    order_note=consumer.ORDER_NOTE)
              == consumer.AGENT_PROMPT.format(
                  path=Path("/tmp/p.md"), proposals=Path("/tmp/v.jsonl"),
                  block=b_p2.text, folder=Path("/tmp"),
                  situation=consumer.ORDER_NOTE + st_p2.follow_up_note(),
                  open_state=consumer.open_state_block(st_p2)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(444, "a wake-word turn gets ORDER_NOTE_V2 in v2, ORDER_NOTE in v1, none without",
              consumer.order_note_for(2, True) == consumer.ORDER_NOTE_V2
              and consumer.order_note_for(1, True) == consumer.ORDER_NOTE
              and consumer.order_note_for(2, False) == ""
              and consumer.order_note_for(1, False) == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(445, "the v2 link rule also allows the file just made on request",
              "auf\n   Zuruf angelegt oder geaendert" in p2
              or "auf Zuruf angelegt oder geaendert" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(446, "writing a project file only on an explicit order, never the protocol",
              "ausser ein Auftrag der Runde verlangt ausdruecklich" in p2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(447, "fmt=1 still is AGENT_PROMPT with its sections",
              "## Verlauf" in p_v1a and "## Entscheidungen" in p_v1a
              and "tag_entry" not in p_v1a)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_49(sec_48):
    """Scenario: v2 -> v1 mid-run: the turn falls back to the v1 prompt, logged once"""
    global _REC
    _REC = rec = _Recorder()
    global d_fb, fb_log, fb_log2, p_fb, prot_fb, st_fb, w_fb

    try:
        print("\nv2 -> v1 mid-run: the turn falls back to the v1 prompt, logged once")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_fb = v2_datei("fb.md")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_fb = k.Protocol(p_fb, _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_fb, st_fb, d_fb = v2_schreiber("wfb", prot_fb)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        fb_log = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(448, "a live v2 protocol gives fmt 2",
              consumer.turn_protocol_format(w_fb, fb_log.append) == (2, False))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_fb.write_text(consumer.HEADER.format(**_HEAD), encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(449, "the next write notices the file is v1 now",
              prot_fb.add_entry("notiz", "x").reason == "not v2" and not prot_fb.is_v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        fb_log2 = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(450, "the turn falls back to fmt 1 and says it switched",
              consumer.turn_protocol_format(w_fb, lambda *a: fb_log2.append(a)) == (1, True))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(451, "the writer no longer treats the file as v2", w_fb.protocol is None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(452, "the next turn stays v1 without a second log line",
              consumer.turn_protocol_format(w_fb, lambda *a: fb_log2.append(a)) == (1, False)
              and len(fb_log2) == 1, str(fb_log2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(453, "a v1 run from the start is fmt 1, no switch",
              consumer.turn_protocol_format(v2_schreiber("wfb1", None)[0],
                                            fb_log.append) == (1, False)
              and fb_log == [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_50(sec_49):
    """Scenario: v2 turn preparation never raises into the turn"""
    global _REC
    _REC = rec = _Recorder()
    global _kaputt, d_pr, e, erg_pr, n, names_pr, pr_log, proj, prot_pr, st_pr, w_pr

    try:
        print("\nv2 turn preparation never raises into the turn")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_pr = k.Protocol(v2_datei("pr.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_pr, st_pr, d_pr = v2_schreiber("wpr", prot_pr)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        proj = _v2 / "projekt"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (proj / "sessions" / "s1").mkdir(parents=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for n in ("kostenplan", "leitfaden"):
            (proj / f"{n}.md").write_text("x", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (proj / "sessions" / "s1" / "protokoll.md").write_text("x", encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        names_pr = consumer.prepare_v2_turn(w_pr, st_pr, proj, [], lambda *a: None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(454, "it returns the prompt's file names, sessions excluded",
              names_pr == ["kostenplan", "leitfaden"], str(names_pr))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(455, "and refreshes the full set for the link check",
              w_pr.link_names == {"kostenplan", "leitfaden"}, str(w_pr.link_names))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    def _kaputt(*a, **kw):
        raise RuntimeError("synthetic")


    try:
        w_pr.file_unrecorded = _kaputt
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        pr_log = []
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            erg_pr = consumer.prepare_v2_turn(w_pr, st_pr, _v2 / "gibtsnicht", [{"x": 1}],
                                              lambda *a: pr_log.append(a))
        except Exception as e:  # noqa: BLE001
            erg_pr = e
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(456, "a raising filing step is logged, not raised",
              isinstance(erg_pr, list) and bool(pr_log), repr(erg_pr))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec






@pytest.fixture(scope="module")
def sec_53(sec_50):
    """Scenario: level 2: a proposal answering an order gets its marker"""
    global _REC
    _REC = rec = _Recorder()
    global _PolicyStub, d_b, d_g, d_o, d_t, prot_b, prot_g, prot_o, prot_t, st_b, st_g, st_o, st_t, stub_b, stub_g, stub_o, stub_t, text_b, text_g, text_o, text_t, w_b, w_g, w_o, w_t


    # ======================= MM-B level 2: order ids carry the marker
    try:
        print("\nlevel 2: a proposal answering an order gets its marker")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]


    class _PolicyStub:
        """Stands in for gateway/policy_runner.PolicyRunner at the writer."""

        def __init__(self, state, marks: dict) -> None:
            self.state = state
            self.marks = marks
            self.delivered_ids: list = []
            self.state_lock_free: list = []

        def marker_for(self, oid) -> str:
            return self.marks.get(oid, "")

        def delivered(self, oid) -> None:
            # The lock contract of policy_runner: never called while the
            # caller holds StageState._lock. A second thread must get it.
            frei: list = []

            def _probe() -> None:
                ok = self.state._lock.acquire(timeout=1.0)  # noqa: SLF001
                if ok:
                    self.state._lock.release()              # noqa: SLF001
                frei.append(ok)

            t = _threading.Thread(target=_probe)
            t.start()
            t.join()
            self.state_lock_free.append(bool(frei and frei[0]))
            self.delivered_ids.append(oid)


    try:
        k.log = lambda *a: None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_o = k.Protocol(v2_datei("order.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_o, st_o, d_o = v2_schreiber("w_order", prot_o)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stub_o = _PolicyStub(st_o, {"A1": "*(autonom)*"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_o.policy = stub_o
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w_o, d_o, {"action": "tag_entry", "tag": "aufgabe",
                               "text": "Anna prüft den Termin.", "order": "A1",
                               "time": "10:05"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_o = prot_o.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(461, "an ordered tag_entry ends with its marker",
              "- 10:05 [aufgabe] Anna prüft den Termin. *(autonom)*\n" in text_o, text_o)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(462, "and the order is reported delivered", stub_o.delivered_ids == ["A1"],
              str(stub_o.delivered_ids))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(463, "delivered() is called outside the state lock",
              stub_o.state_lock_free == [True], str(stub_o.state_lock_free))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        prot_t = k.Protocol(v2_datei("order-topic.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_t, st_t, d_t = v2_schreiber("w_order_topic", prot_t)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stub_t = _PolicyStub(st_t, {"A2": "*(bestätigt 14:05)*"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_t.policy = stub_t
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w_t, d_t, {"action": "topic", "title": "Haushalt", "order": "A2",
                               "markdown": "## Haushalt"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_t = prot_t.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(464, "an ordered topic's block heading carries the marker",
              re.search(r"^### \d\d:\d\d Haushalt \*\(bestätigt 14:05\)\*$", text_t,
                        re.M) is not None, text_t)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(465, "the stage title does not", st_t.main.title == "Haushalt",
              st_t.main.title)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(466, "the topic order is delivered, outside the state lock",
              stub_t.delivered_ids == ["A2"] and stub_t.state_lock_free == [True],
              f"{stub_t.delivered_ids} {stub_t.state_lock_free}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        prot_b = k.Protocol(v2_datei("order-decision.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_b, st_b, d_b = v2_schreiber("w_order_decision", prot_b)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stub_b = _PolicyStub(st_b, {"A3": "*(autonom)*"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_b.policy = stub_b
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w_b, d_b, {"action": "decision", "time": "10:30", "order": "A3",
                               "text": "Antrag wird gestellt"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_b = prot_b.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(467, "an ordered decision's beschluss entry ends with the marker",
              "- 10:30 [beschluss] Antrag wird gestellt *(autonom)*\n" in text_b, text_b)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(468, "the stage decision does not",
              st_b.decisions and st_b.decisions[-1].text == "Antrag wird gestellt",
              str([x.text for x in st_b.decisions]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(469, "the decision order is delivered, outside the state lock",
              stub_b.delivered_ids == ["A3"] and stub_b.state_lock_free == [True],
              f"{stub_b.delivered_ids} {stub_b.state_lock_free}")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        prot_g = k.Protocol(v2_datei("order-garbage.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_g, st_g, d_g = v2_schreiber("w_order_garbage", prot_g)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        stub_g = _PolicyStub(st_g, {"A1": "*(autonom)*",
                                    "das ist ein Satz": "*(autonom)*"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_g.policy = stub_g
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w_g, d_g,
                    {"action": "tag_entry", "tag": "notiz", "text": "Erstens",
                     "order": "das ist ein Satz", "time": "10:06"},
                    {"action": "tag_entry", "tag": "notiz", "text": "Zweitens",
                     "order": "A9", "time": "10:07"},
                    {"action": "tag_entry", "tag": "notiz", "text": "Drittens",
                     "order": 17, "time": "10:08"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        text_g = prot_g.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(470, "an unknown or garbage order id adds no marker, the entries still land",
              "- 10:06 [notiz] Erstens\n" in text_g
              and "- 10:07 [notiz] Zweitens\n" in text_g
              and "- 10:08 [notiz] Drittens\n" in text_g and "autonom" not in text_g,
              text_g)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(471, "and nothing is delivered", stub_g.delivered_ids == [],
              str(stub_g.delivered_ids))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_54(sec_53):
    """Scenario: level 2 off: the writer files exactly what it filed before"""
    global _REC
    _REC = rec = _Recorder()
    global d_off, prot_off, prot_ref, st_off, w_off

    try:
        print("\nlevel 2 off: the writer files exactly what it filed before")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_off = k.Protocol(v2_datei("order-off.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_ref = k.Protocol(v2_datei("order-ref.md"), _v2)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        w_off, st_off, d_off = v2_schreiber("w_order_off", prot_off)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(472, "policy is None by default", w_off.policy is None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        vorschlagen(w_off, d_off, {"action": "tag_entry", "tag": "aufgabe",
                                   "text": "Anna  prüft   den Termin.",
                                   "order": "A1", "time": "10:05"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        prot_ref.add_entry("aufgabe", "Anna  prüft   den Termin.", stamp="10:05")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(473, "without a policy the order field changes nothing (byte-identical)",
              prot_off.path.read_bytes() == prot_ref.path.read_bytes(),
              prot_off.path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(474, "file_entry without a mark is the plain add_entry",
              w_off.file_entry("notiz", "x  y", stamp="10:09")
              == prot_ref.add_entry("notiz", "x  y", stamp="10:09").reason
              and prot_off.path.read_bytes() == prot_ref.path.read_bytes())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        k.log = alt_log
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_55(sec_54):
    """Scenario: level 2: policy_turn() -- what a turn takes from the policy"""
    global _REC
    _REC = rec = _Recorder()
    global _, _TurnStub, _extra, _extra_v1, _extra_w, _extra_ww, _frueher, _gesagt, _gesagt_w, _note, _pr, _st, _w


    # ======================= MM-B level 2: the per-turn steps of run_turn
    try:
        print("\nlevel 2: policy_turn() -- what a turn takes from the policy")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import policy_runner as _pr  # noqa: E402


    class _TurnStub:
        """Stands in for PolicyRunner.take_turn(): hands out one TurnOrders."""

        def __init__(self, orders) -> None:
            self.orders = orders
            self.taken = 0

        def take_turn(self):
            self.taken += 1
            return self.orders


    try:
        _note = "AUFTRAG A1: Trage eine Aufgabe ein."
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _frueher = "Robo zeig uns die Liste der offenen Punkte"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st = _TurnStub(_pr.TurnOrders(note=_note))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _extra, _gesagt = k.policy_turn(_st, 2, block("Wir reden über den Termin."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(475, "the orders' note becomes the turn's extra order note",
              _st.taken == 1 and _extra == _note, repr(_extra))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(476, "the ordinary note plus it is what reaches order_note",
              (k.order_note_for(2, False) + _extra).endswith(_note)
              and _note in k.order_note_for(2, True) + _extra)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(477, "spoken is the block's own text without a wake order",
              _gesagt == "Wir reden über den Termin.", repr(_gesagt))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _w = _TurnStub(_pr.TurnOrders(note=_note, spoken=_frueher, wake=True))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _extra_w, _gesagt_w = k.policy_turn(_w, 2, block("Ja genau so."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(478, "a wake order on a block WITHOUT the wake word adds the v2 order note",
              _extra_w == _note + k.ORDER_NOTE_V2, repr(_extra_w))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(479, "and spoken is extended with the wake block's text",
              _gesagt_w == "Ja genau so. " + _frueher, repr(_gesagt_w))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _extra_v1, _ = k.policy_turn(
            _TurnStub(_pr.TurnOrders(note=_note, spoken=_frueher, wake=True)), 1,
            block("Ja genau so."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(480, "in a v1 turn the v1 order note", _extra_v1 == _note + k.ORDER_NOTE)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _extra_ww, _ = k.policy_turn(
            _TurnStub(_pr.TurnOrders(note=_note, spoken=_frueher, wake=True)), 2,
            block("Robo, mach das.", wake_word=True))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(481, "a block WITH the wake word carries its order note once, not twice",
              _extra_ww == _note, repr(_extra_ww))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(482, "without a runner: no note, spoken is the block text (as before)",
              k.policy_turn(None, 2, block("Nur das.")) == ("", "Nur das."))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(483, "PolicyRunner is imported lazily, only under --policy",
              "PolicyRunner" not in vars(k))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_56(sec_55):
    """Scenario: level 2: its own question is not the agent's to resolve"""
    global _REC
    _REC = rec = _Recorder()
    global _args, _eigene_zeile, _erster, _fmt, _kw, _mit, _nur, _ohne, _p_pol, _q_pol, _quelle, _st_nur, _st_pol

    try:
        print("\nlevel 2: its own question is not the agent's to resolve")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_pol = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_pol.apply({"action": "notice", "kind": "value_conflict",
                       "text": "**Frist** 6.10. gegen 5.10."})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_pol.apply({"action": "question", "text": "Eigenes Dokument dazu anlegen?"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _q_pol = _st_pol.question.id
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ohne = k.open_state_block(_st_pol)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _mit = k.open_state_block(_st_pol, policy_ask_id=_q_pol)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _eigene_zeile = (f"  ? {_q_pol} — Robo-Rueckfrage: wertet Robo selbst aus "
                         "-- nicht aufloesen, nicht ausfuehren.")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(484, "without the id the policy question is listed as resolvable",
              f"? {_q_pol} — Eigenes Dokument" in _ohne and "Robo-Rueckfrage" not in _ohne,
              repr(_ohne))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(485, "with the id it leaves the resolvable list ...",
              "Eigenes Dokument" not in _mit and "N1" in _mit, repr(_mit))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(486, "... and gets its own line instead", _eigene_zeile + "\n" in _mit
              and _mit.index("N1") < _mit.index(_eigene_zeile), repr(_mit))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_nur = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _st_nur.apply({"action": "question", "text": "Eigenes Dokument dazu anlegen?"})
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _nur = k.open_state_block(_st_nur, policy_ask_id=_st_nur.question.id)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(487, "the policy question alone: no 'nur du kannst das aufloesen' heading",
              "nur du kannst" not in _nur and _nur == "\n" + _eigene_zeile.replace(
                  _q_pol, _st_nur.question.id) + "\n", repr(_nur))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(488, "an id that is not open changes nothing",
              k.open_state_block(_st_pol, policy_ask_id="Q99") == _ohne)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(489, "an empty id is byte for byte the block without it",
              k.open_state_block(_st_pol, policy_ask_id="") == _ohne)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        for _fmt in (1, 2):
            for _erster in (True, False):
                _args = (Path("/tmp/p.md"), Path("/tmp/v.jsonl"),
                         block("Wir reden über den Termin."), _st_pol)
                _kw = dict(fmt=_fmt, first_turn=_erster, file_names=["a.md"])
                _chk(490, f"build_prompt(fmt={_fmt}, first_turn={_erster}) with an "
                      "empty policy_ask_id is byte-identical",
                      k.build_prompt(*_args, policy_ask_id="", **_kw)
                      == k.build_prompt(*_args, **_kw))
                _p_pol = k.build_prompt(*_args, policy_ask_id=_q_pol, **_kw)
                _chk(491, f"build_prompt(fmt={_fmt}, first_turn={_erster}) passes the "
                      "id on", _eigene_zeile in _p_pol
                      and "Eigenes Dokument" not in _p_pol)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _quelle = Path(k.__file__).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(492, "run_turn hands the runner's open ask to build_prompt",
              "policy_ask_id=(policy_runner.open_ask_id()" in _quelle)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_57(sec_56):
    """Scenario: topic_criterion (C2 measure 1): default OFF, byte-identical to f1bf253"""
    global _REC
    _REC = rec = _Recorder()
    global b_tc, hashlib, p_tc_delta, p_tc_first, st_tc
    # ============================ topic_criterion (C2 measure 1, Task 1) =====
    # docs/superpowers/plans/2026-09-26-topic-prompt-c2.md, D4 + Task 1: a new,
    # default-OFF prompt block that tells the agent WHEN a topic change is a
    # topic change, behind `build_prompt(..., topic_criterion=True)`. With the
    # default (False) `build_prompt()` must keep rendering byte-identical to
    # f1bf253 -- pinned below by sha256, computed from the code BEFORE this
    # feature existed.
    try:
        print("\ntopic_criterion (C2 measure 1): default OFF, byte-identical to f1bf253")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import hashlib  # noqa: E402

    try:
        st_tc = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_tc = Block(sentences=["Also der sechste Oktober gilt."], reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_tc_first = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
            backlog_note="", order_note="", first_turn=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_tc_delta = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
            backlog_note="", order_note="", first_turn=False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(493, "default first-turn rendering is byte-identical to f1bf253 (pinned sha256)",
              hashlib.sha256(p_tc_first.encode("utf-8")).hexdigest()
              == "505143b00375974fa10b10b9355e6c0491648501c6c8e51808f31815db368813",
              hashlib.sha256(p_tc_first.encode("utf-8")).hexdigest())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(494, "default delta rendering is byte-identical to f1bf253 (pinned sha256)",
              hashlib.sha256(p_tc_delta.encode("utf-8")).hexdigest()
              == "118e355a1b3b8bb07b2824936bc35c3cd03ecb431e9f7f6283b7d9f106993cab",
              hashlib.sha256(p_tc_delta.encode("utf-8")).hexdigest())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_58(sec_57):
    """Scenario: topic_criterion=True: the criterion text, positioned and complete"""
    global _REC
    _REC = rec = _Recorder()
    global p_tc_on

    try:
        print("\ntopic_criterion=True: the criterion text, positioned and complete")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_tc_on = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
            backlog_note="", order_note="", first_turn=True, topic_criterion=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(495, "the criterion heading is present", "WORAN du einen Wechsel erkennst" in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(496, "marker (a) -- a new subject -- is present",
              "ein neuer Gegenstand" in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(497, "marker (b) -- the round returns to an older topic -- is present",
              "aelteren Thema zurueck" in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(498, "marker (c) -- the explicit markers -- is present",
              '"kommen wir zu"' in p_tc_on and '"andere Frage"' in p_tc_on
              and '"noch was anderes"' in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(499, "the KEIN-Wechsel counter-rule is present", "KEIN Wechsel ist" in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(500, "the three invented negative examples are present",
              "Gartenzaun" in p_tc_on and "Frist ist zu knapp" in p_tc_on
              and "Abrechnung dabei" in p_tc_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(501, "the criterion sits after the topic bullet and before the graph bullet",
              p_tc_on.index("schick denselben Wechsel nicht noch einmal.")
              < p_tc_on.index("WORAN du einen Wechsel erkennst")
              < p_tc_on.index("`graph`, wenn ein NEUES Thema"),
              "ordering violated")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_59(sec_58):
    """Scenario: topic_criterion=True: mutation tests -- the asserts check pieces, not presence"""
    global _REC
    _REC = rec = _Recorder()
    global _tc_original, p_no_c, p_no_counter

    try:
        print("\ntopic_criterion=True: mutation tests -- the asserts check pieces, not presence")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _tc_original = consumer.TOPIC_CRITERION
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            consumer.TOPIC_CRITERION = _tc_original.replace(
                "     KEIN Wechsel ist: eine Vertiefung, ein Beispiel oder ein Einwand zum\n"
                "     selben Thema. Erfundene Beispiele fuer KEINEN Wechsel: \"Nehmen wir mal\n"
                "     den Fall mit dem Gartenzaun\" (ein Beispiel), \"Da muss ich widersprechen,\n"
                "     die Frist ist zu knapp\" (ein Einwand), \"Und wie genau laeuft die\n"
                "     Abrechnung dabei?\" (eine Vertiefung).\n", "")
            _chk(502, "mutation fixture actually dropped the counter-rule line",
                  consumer.TOPIC_CRITERION != _tc_original)
            p_no_counter = consumer.build_prompt(
                Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
                backlog_note="", order_note="", first_turn=True, topic_criterion=True)
            _chk(503, "mutation: dropping the counter-rule line fails the counter-rule assert",
                  "KEIN Wechsel ist" not in p_no_counter)
            _chk(504, "mutation: the (a)/(b)/(c) markers survive the counter-rule drop",
                  "ein neuer Gegenstand" in p_no_counter)
        finally:
            consumer.TOPIC_CRITERION = _tc_original
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        try:
            consumer.TOPIC_CRITERION = _tc_original.replace(
                '       (c) ein ausdruecklicher Marker: "kommen wir zu", "andere Frage",\n'
                '           "noch was anderes".\n', "")
            _chk(505, "mutation fixture actually dropped marker (c)",
                  consumer.TOPIC_CRITERION != _tc_original)
            p_no_c = consumer.build_prompt(
                Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
                backlog_note="", order_note="", first_turn=True, topic_criterion=True)
            _chk(506, "mutation: dropping marker (c) fails the marker-(c) assert",
                  '"kommen wir zu"' not in p_no_c)
            _chk(507, "mutation: the counter-rule survives the marker-(c) drop",
                  "KEIN Wechsel ist" in p_no_c)
        finally:
            consumer.TOPIC_CRITERION = _tc_original
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(508, "mutation fixture is restored after both mutations",
              consumer.TOPIC_CRITERION == _tc_original)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_60(sec_59):
    """Scenario: topic_criterion=True: B5 -- the delta turn never repeats the criterion"""
    global _REC
    _REC = rec = _Recorder()
    global p_tc_delta_on

    try:
        print("\ntopic_criterion=True: B5 -- the delta turn never repeats the criterion")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_tc_delta_on = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_tc, st_tc,
            backlog_note="", order_note="", first_turn=False, topic_criterion=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(509, "the delta turn with topic_criterion=True carries no criterion text",
              "WORAN du einen Wechsel erkennst" not in p_tc_delta_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(510, "the delta turn with topic_criterion=True carries no other AGENT_PROMPT text",
              "PROTOKOLL:" not in p_tc_delta_on and '"mirror"' not in p_tc_delta_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(511, "the delta turn with topic_criterion=True still carries the spoken block",
              "Also der sechste Oktober gilt." in p_tc_delta_on)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_61(sec_60):
    """Scenario: topic_criterion: canary -- no session text leaks into the static criterion"""
    global _REC
    _REC = rec = _Recorder()
    global KANARI_TC, b_kanari, p_kanari

    try:
        print("\ntopic_criterion: canary -- no session text leaks into the static criterion")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Same pattern as tests/test_bench.py and tests/test_topic_detect.py: an
    # invented word that must never reach a place it doesn't belong.
    try:
        KANARI_TC = ["SEEPFERDCHENPARADE", "MURMELTIERKONFERENZ", "REGENWURMSEMINAR"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_kanari = Block(sentences=[f"Wir sprechen jetzt ueber die {KANARI_TC[0]}."],
                         reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_kanari = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_kanari, st_tc,
            backlog_note="", order_note="", first_turn=True, topic_criterion=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(512, "the canary word from the spoken block reaches the rendered prompt",
              KANARI_TC[0] in p_kanari)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(513, "the canary word appears exactly once -- only inside the spoken block",
              p_kanari.count(KANARI_TC[0]) == 1, p_kanari.count(KANARI_TC[0]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(514, "none of the invented canary words leak into the static criterion",
              not any(w in consumer.TOPIC_CRITERION for w in KANARI_TC),
              consumer.TOPIC_CRITERION)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_62(sec_61):
    """Scenario: thread (C2 measure 2): default OFF, byte-identical to f1bf253/06bdb64"""
    global _REC
    _REC = rec = _Recorder()
    global b_th, p_th_delta, p_th_first, st_th

    # ============================ thread in the delta turn (C2 measure 2, Task 2)
    # docs/superpowers/plans/2026-09-26-topic-prompt-c2.md, D5 + Task 2: the
    # delta turn gets the current headline and the last Verlauf bullets, so it
    # can compare instead of guessing from one block. Default OFF
    # (`thread=None`); with the default, rendering must keep matching the
    # Task 1 pins above byte-for-byte -- those are re-asserted here so a
    # regression in this task fails the SAME two checks Task 1 already has.
    try:
        print("\nthread (C2 measure 2): default OFF, byte-identical to f1bf253/06bdb64")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_th = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_th = Block(sentences=["Also der sechste Oktober gilt."], reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_th_first = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_th, st_th,
            backlog_note="", order_note="", first_turn=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_th_delta = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_th, st_th,
            backlog_note="", order_note="", first_turn=False)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(515, "default (thread=None) first-turn rendering still matches the "
              "topic_criterion pin",
              hashlib.sha256(p_th_first.encode("utf-8")).hexdigest()
              == "505143b00375974fa10b10b9355e6c0491648501c6c8e51808f31815db368813",
              hashlib.sha256(p_th_first.encode("utf-8")).hexdigest())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(516, "default (thread=None) delta rendering still matches the "
              "topic_criterion pin",
              hashlib.sha256(p_th_delta.encode("utf-8")).hexdigest()
              == "118e355a1b3b8bb07b2824936bc35c3cd03ecb431e9f7f6283b7d9f106993cab",
              hashlib.sha256(p_th_delta.encode("utf-8")).hexdigest())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_63(sec_62):
    """Scenario: recent_verlauf: last n bullets of ## Verlauf, never ## Rohtranskript or below"""
    global _REC
    _REC = rec = _Recorder()
    global _protokoll_voll

    try:
        print("\nrecent_verlauf: last n bullets of ## Verlauf, never ## Rohtranskript or below")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _protokoll_voll = """## Entscheidungen

## Verlauf
- 10:00 erstes Thema
kein Stichpunkt, keine Zeile mit "- " Praefix
- 10:05 zweites Thema
- 10:10 drittes Thema
- 10:15 viertes Thema
- 10:20 fuenftes Thema

## Rohtranskript
- 10:25 sollte nie erscheinen
"""
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(517, "last n=4 bullets, in order, the not-a-bullet line ignored",
              consumer.recent_verlauf(_protokoll_voll, n=4) == [
                  "- 10:05 zweites Thema", "- 10:10 drittes Thema",
                  "- 10:15 viertes Thema", "- 10:20 fuenftes Thema"],
              consumer.recent_verlauf(_protokoll_voll, n=4))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(518, "a bullet planted below ## Rohtranskript never comes back",
              not any("sollte nie erscheinen" in b
                      for b in consumer.recent_verlauf(_protokoll_voll, n=100)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(519, "n is respected (n=2)",
              consumer.recent_verlauf(_protokoll_voll, n=2) == [
                  "- 10:15 viertes Thema", "- 10:20 fuenftes Thema"],
              consumer.recent_verlauf(_protokoll_voll, n=2))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(520, "missing ## Verlauf section -> []",
              consumer.recent_verlauf("## Entscheidungen\n- nicht Verlauf\n", n=4) == [])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(521, "default n is THREAD_BULLETS",
              consumer.recent_verlauf(_protokoll_voll) == consumer.recent_verlauf(
                  _protokoll_voll, n=consumer.THREAD_BULLETS))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(522, "THREAD_BULLETS is 4", consumer.THREAD_BULLETS == 4)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_64(sec_63):
    """Scenario: thread=[...]: the block appears between open state and 'Neu gesprochen', delta only"""
    global _REC
    _REC = rec = _Recorder()
    global b_faden, p_faden, p_faden_first, st_faden

    try:
        print("\nthread=[...]: the block appears between open state and 'Neu gesprochen', "
              "delta only")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_faden = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_faden.headline = "Kostenrahmen"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_faden = Block(sentences=["Weiter im Text."], reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_faden = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_faden, st_faden,
            backlog_note="", order_note="", first_turn=False,
            thread=["- 10:05 erstes Thema", "- 10:10 zweites Thema"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(523, "the header line is present", "DER FADEN BISHER" in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(524, "the headline line is present", "Ueberschrift jetzt: Kostenrahmen" in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(525, "the bullets header is present", "Letzte Verlauf-Punkte:" in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(526, "both bullets are present, each on its own line",
              "- 10:05 erstes Thema" in p_faden and "- 10:10 zweites Thema" in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(527, "the thread sits between the open state and 'Neu gesprochen'",
              p_faden.index("DER FADEN BISHER")
              < p_faden.index("Ueberschrift jetzt:")
              < p_faden.index("Letzte Verlauf-Punkte:")
              < p_faden.index("- 10:05 erstes Thema")
              < p_faden.index("Neu gesprochen seit deiner letzten Runde:"),
              "ordering violated")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_faden_first = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_faden, st_faden,
            backlog_note="", order_note="", first_turn=True,
            thread=["- 10:05 erstes Thema"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(528, "B5: the first turn (full AGENT_PROMPT) is not changed by thread",
              "DER FADEN BISHER" not in p_faden_first)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_65(sec_64):
    """Scenario: thread=[]: empty list still renders the headline and '(noch keine)'"""
    global _REC
    _REC = rec = _Recorder()
    global p_faden_leer, st_leer_th

    try:
        print("\nthread=[]: empty list still renders the headline and '(noch keine)'")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_leer_th = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_faden_leer = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_faden, st_leer_th,
            backlog_note="", order_note="", first_turn=False, thread=[])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(529, "empty headline renders '(noch keine)'",
              "Ueberschrift jetzt: (noch keine)" in p_faden_leer, p_faden_leer)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(530, "empty bullet list renders a single '(noch keine)' line",
              "Letzte Verlauf-Punkte:\n(noch keine)" in p_faden_leer, p_faden_leer)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_66(sec_65):
    """Scenario: thread: B5 -- the delta with a thread carries no AGENT_PROMPT static text"""
    global _REC
    _REC = rec = _Recorder()

    try:
        print("\nthread: B5 -- the delta with a thread carries no AGENT_PROMPT static text")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(531, "no AGENT_PROMPT path line", "PROTOKOLL:" not in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(532, "no AGENT_PROMPT action catalogue", '"mirror"' not in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(533, "the spoken block still arrives", "Weiter im Text." in p_faden)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_67(sec_66):
    """Scenario: thread: mutation -- dropping the headline line or the bullets fails specific asserts"""
    global _REC
    _REC = rec = _Recorder()
    global _orig_delta_prompt, p_mut

    try:
        print("\nthread: mutation -- dropping the headline line or the bullets fails "
              "specific asserts")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _orig_delta_prompt = consumer.DELTA_PROMPT
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            consumer.DELTA_PROMPT = consumer.DELTA_PROMPT.replace("{thread}", "")
            _chk(534, "mutation fixture actually dropped the thread placeholder",
                  consumer.DELTA_PROMPT != _orig_delta_prompt)
            p_mut = consumer.build_prompt(
                Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_faden, st_faden,
                backlog_note="", order_note="", first_turn=False,
                thread=["- 10:05 erstes Thema"])
            _chk(535, "mutation: dropping the placeholder fails the headline-line assert",
                  "Ueberschrift jetzt:" not in p_mut)
            _chk(536, "mutation: dropping the placeholder fails the bullets assert",
                  "- 10:05 erstes Thema" not in p_mut)
        finally:
            consumer.DELTA_PROMPT = _orig_delta_prompt
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(537, "mutation fixture is restored", consumer.DELTA_PROMPT == _orig_delta_prompt)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_68(sec_67):
    """Scenario: thread: token growth -- the thread block's character count, pinned (D7 i)"""
    global _REC
    _REC = rec = _Recorder()
    global _end, _fixed_bullets, _start, _thread_block_text, p_growth, st_growth

    try:
        print("\nthread: token growth -- the thread block's character count, pinned (D7 i)")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    # Four bullets of fixed synthetic length, so the char count is deterministic
    # and reproducible outside this test. Goes into docs/bench.md as D7 (i).
    try:
        _fixed_bullets = [
            "- 10:01 erstes fixes Stichwort mit exakt bekannter Laenge hier",
            "- 10:02 zweites fixes Stichwort mit exakt bekannter Laenge hie",
            "- 10:03 drittes fixes Stichwort mit exakt bekannter Laenge hi",
            "- 10:04 viertes fixes Stichwort mit exakt bekannter Laenge h",
        ]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(538, "fixture carries exactly THREAD_BULLETS=4 bullets", len(_fixed_bullets) == 4)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_growth = StageState()
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        st_growth.headline = "Testueberschrift"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_growth = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_faden, st_growth,
            backlog_note="", order_note="", first_turn=False, thread=_fixed_bullets)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _start = p_growth.index("DER FADEN BISHER")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _end = p_growth.index("\nNeu gesprochen seit deiner letzten Runde:")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _thread_block_text = p_growth[_start:_end]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(539, "thread block character count is pinned (D7 i)",
              len(_thread_block_text) == 367, len(_thread_block_text))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_69(sec_68):
    """Scenario: thread: canary -- an invented word in synthetic bullets stays inside the thread block"""
    global _REC
    _REC = rec = _Recorder()
    global KANARI_TH, b_th_kanari, p_th_kanari

    try:
        print("\nthread: canary -- an invented word in synthetic bullets stays inside "
              "the thread block")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        KANARI_TH = ["FLUSSPFERDKONGRESS", "SCHNEEBALLSCHLACHTKOMITEE"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        b_th_kanari = Block(sentences=["Neutraler Abschnitt ohne das Stichwort."],
                            reason="pause")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        p_th_kanari = consumer.build_prompt(
            Path("/tmp/protokoll.md"), Path("/tmp/vorschlag.jsonl"), b_th_kanari, st_th,
            backlog_note="", order_note="", first_turn=False,
            thread=[f"- 10:00 ueber die {KANARI_TH[0]}"])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(540, "the canary bullet reaches the rendered prompt", KANARI_TH[0] in p_th_kanari)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(541, "the canary word appears exactly once -- only inside the thread block",
              p_th_kanari.count(KANARI_TH[0]) == 1, p_th_kanari.count(KANARI_TH[0]))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(542, "the other invented canary word never leaks anywhere",
              KANARI_TH[1] not in p_th_kanari)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_70(sec_69):
    """Scenario: COTHINKER_PROJECT_DIR overrides the $ENTITIES lookup (point 11)"""
    global _REC
    _REC = rec = _Recorder()
    global entity, folder, old_env, pslug, tmp_proj

    try:
        print("\nCOTHINKER_PROJECT_DIR overrides the $ENTITIES lookup (point 11)")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        tmp_proj = Path(_mkdtemp(prefix="cothinker-project-dir-"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        old_env = os.environ.pop("COTHINKER_PROJECT_DIR", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            os.environ["COTHINKER_PROJECT_DIR"] = str(tmp_proj)
            folder, entity, pslug = consumer.resolve_project("irrelevant-arg")
            _chk(543, "the override folder is returned verbatim",
                  folder == tmp_proj, str(folder))
            _chk(544, "pslug comes from the override folder's own name",
                  pslug == tmp_proj.name, pslug)
        finally:
            os.environ.pop("COTHINKER_PROJECT_DIR", None)
            if old_env is not None:
                os.environ["COTHINKER_PROJECT_DIR"] = old_env
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        os.environ["COTHINKER_PROJECT_DIR"] = "/no/such/directory/at/all"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            consumer.resolve_project("x")
            _chk(545, "a non-existent override directory is a clear SystemExit, not a "
                 "silent fallback", False)
        except SystemExit:
            _chk(546, "a non-existent override directory is a clear SystemExit, not a "
                 "silent fallback", True)
        finally:
            os.environ.pop("COTHINKER_PROJECT_DIR", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_71(sec_70):
    """Scenario: agent.turn_timeout_s config key"""
    global _REC
    _REC = rec = _Recorder()
    global _old_env, tmp_home

    try:
        print("\nagent.turn_timeout_s config key")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(547, "worker_command_hermes_home() reads the embedded --hermes-home",
              consumer.worker_command_hermes_home(
                  "/usr/bin/python3 worker.py --hermes-home /opt/x/.hermes/profiles/cothinker")
              == "/opt/x/.hermes/profiles/cothinker", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(548, "worker_command_hermes_home() of an empty command is ''",
              consumer.worker_command_hermes_home("") == "", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(549, "worker_command_hermes_home() without the flag is ''",
              consumer.worker_command_hermes_home("/usr/bin/python3 worker.py") == "", None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]

    try:
        _chk(550, "resolve_turn_timeout_s() reads agent.turn_timeout_s from the "
             "profile named in --worker-command when no env override is set",
              hasattr(consumer, "resolve_turn_timeout_s"), None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _root = Path(consumer.__file__).parents[1]

        def _timeout_with(env_value):
            env = {k_: v_ for k_, v_ in os.environ.items()
                   if k_ != "COTHINKER_TURN_TIMEOUT"}
            if env_value is not None:
                env["COTHINKER_TURN_TIMEOUT"] = env_value
            return subprocess.run(
                [sys.executable, "-c",
                 "import gateway.consumer as c; print(c.TURN_TIMEOUT_S)"],
                cwd=_root, env=env, text=True, capture_output=True,
                timeout=60).stdout.strip()
        _chk(551, "the turn timeout defaults to 900s without an env override",
              _timeout_with(None) == "900.0", _timeout_with(None))
        _chk(552, "COTHINKER_TURN_TIMEOUT sets the turn timeout",
              _timeout_with("30") == "30.0", _timeout_with("30"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_72(sec_71):
    """Scenario: protocol lane helpers"""
    global _REC
    _REC = rec = _Recorder()
    global _dt, merged, pb, rs

    try:
        print("\nprotocol lane helpers")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    from datetime import datetime as _dt  # noqa: E402
    try:
        pb = consumer.protocol_lane_block(Block(sentences=["Wir planen den Garten."],
                                                reason="pause"), _dt(2026, 9, 26, 12, 20, 7))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(553, "the copy carries the block time, never the wake flag",
              pb.text == "[12:20] Wir planen den Garten." and not pb.wake_word, pb.text)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        merged = Block(sentences=["[12:20] a", "[12:24] b", "[12:24] c",
                                  "[… aeltere unverdichtete Abschnitte ausgelassen]"],
                       reason="batch")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(554, "block_stamps: ordered, deduplicated, non-stamp sentences ignored",
              consumer.block_stamps(merged) == ["12:20", "12:24"],
              str(consumer.block_stamps(merged)))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        rs = consumer.resolve_entry_stamp
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(555, "order stamp wins over a foreign agent time", rs("13:05", ["12:20"], ["12:24"]) == "12:20")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(556, "agent time kept when it is one of the order's", rs("12:24", ["12:20", "12:24"], []) == "12:24")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(557, "batch stamps when there is no order", rs("13:05", [], ["12:20", "12:24"]) == "12:20")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(558, "agent time kept when it is one of the batch's", rs("12:24", [], ["12:20", "12:24"]) == "12:24")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(559, "no stamps at all: unchanged (serial path)", rs("13:05", [], None) == "13:05" and rs(None, [], None) is None)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_73(sec_72):
    """Scenario: StageWriter.ingest(allowed=PROTOCOL_LANE_ACTIONS): stage actions refused, entries stamped with the block time"""
    global _REC
    _REC = rec = _Recorder()
    global _EventLog, _before, _pjson, _ppath, _ppd, _prun, _pstate, _ptext, _ptf, _ptmp, _ptrace, _pw, _raw_before, _rej

    try:
        print("\nStageWriter.ingest(allowed=PROTOCOL_LANE_ACTIONS): stage actions refused, "
              "entries stamped with the block time")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    import json as _pjson  # noqa: E402
    import tempfile as _ptf  # noqa: E402
    import shared.minutes_format as _ppd  # noqa: E402
    from shared.events import EventLog as _EventLog  # noqa: E402
    try:
        _ptmp = Path(_mkdtemp())
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _prun = _ptmp / "sessions" / "2026-09-26-probe"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _prun.mkdir(parents=True)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pstate = StageState(now=lambda: 1000.0, clock=lambda: _dt(2026, 9, 26, 14, 7, 0))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ptrace = _prun / "stage-events.jsonl"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pw = consumer.StageWriter(_prun, _pstate, log=lambda *a: None,
                                   proposals=_prun / "vorschlag.jsonl",
                                   events=_EventLog(_ptrace))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ppath = _prun / "protokoll.md"
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ppath.write_text(consumer.protocol_header(
            title="Synthetischer Termin", entity="probe", day="2026-09-26",
            pslug="probe", participants="A, B", start="10:00", mode="sitzung"),
            encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pw.protocol = consumer.Protocol(_ppath, _ptmp, mode="sitzung")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pw.block_stamps = ["12:20", "12:24"]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _before = _ppath.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _raw_before = _before.split("## Rohtranskript", 1)[-1]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (_prun / "vorschlag.jsonl").write_text("".join(_pjson.dumps(x) + "\n" for x in [
            {"action": "topic", "title": "T", "markdown": "m"},
            {"action": "headline", "text": "H"},
            {"action": "tag_entry", "tag": "aufgabe", "text": "Tabelle fertig machen", "time": "13:05"},
            {"action": "tag_entry", "tag": "idee", "text": "Beete tauschen", "time": "12:24"},
        ]), encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _pw.ingest(_prun / "vorschlag.jsonl", spoken="",
                   allowed=consumer.PROTOCOL_LANE_ACTIONS)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _ptext = _ppath.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(560, "topic and headline refused",
              hasattr(consumer, "PROTOCOL_LANE_ACTIONS") and _pstate.main.id == "" and _pstate.headline == "")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _rej = [_pjson.loads(l) for l in _ptrace.read_text(encoding="utf-8").splitlines()
                if '"proposal_rejected"' in l]
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(561, "two proposal_rejected reason=protocol_lane events",
              sum(1 for r in _rej if r.get("reason") == "protocol_lane") == 2, str(_rej))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(562, "foreign agent time replaced by the batch's first stamp",
              _ppd.entry_line("12:20", "aufgabe", "Tabelle fertig machen") in _ptext, _ptext[-400:])
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(563, "agent time kept when it is one of the batch's",
              _ppd.entry_line("12:24", "idee", "Beete tauschen") in _ptext)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(564, "Rohtranskript section unchanged",
              hasattr(consumer, "PROTOCOL_LANE_ACTIONS") and _ptext.split("## Rohtranskript", 1)[-1] == _raw_before)
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec


@pytest.fixture(scope="module")
def sec_74(sec_73):
    """Scenario: protocol-lane gate: unhashable / non-string action does not abort the batch"""
    global _REC
    _REC = rec = _Recorder()
    global _e, _exc

    try:
        print("\nprotocol-lane gate: unhashable / non-string action does not abort the batch")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        (_prun / "vorschlag.jsonl").write_text("".join(_pjson.dumps(x) + "\n" for x in [
            {"action": ["x"]},
            {"action": {}},
            {"action": 5},
            {"action": "tag_entry", "tag": "aufgabe", "text": "Nach dem Unhashable", "time": "12:24"},
        ]), encoding="utf-8")
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _exc = None
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        try:
            _pw.ingest(_prun / "vorschlag.jsonl", spoken="",
                       allowed=consumer.PROTOCOL_LANE_ACTIONS)
        except Exception as _e:  # noqa: BLE001
            _exc = _e
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(565, "no exception on list/dict/int action", _exc is None, repr(_exc))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    try:
        _chk(566, "valid protocol action after the bad lines is still applied",
              _ppd.entry_line("12:24", "aufgabe", "Nach dem Unhashable")
              in _ppath.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rec.error += traceback.format_exc()[-1200:]
    return rec




@pytest.mark.parametrize("site, occ", [
    pytest.param(1, None, id='simple event'),
    pytest.param(2, None, id='multi-line data field'),
    pytest.param(3, None, id='CRLF line endings'),
    pytest.param(4, None, id='comment/event/id/retry skipped'),
    pytest.param(5, None, id='broken JSON skipped'),
    pytest.param(6, None, id='non-objects discarded'),
    pytest.param(7, None, id='event at end of stream'),
    pytest.param(8, None, id='data without a space'),
    pytest.param(9, None, id='invalid bytes replaced'),
    pytest.param(10, None, id='two events separated'),
])
def test_00_sse_parsing(sec_00, site, occ):
    _assert_site(sec_00, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(11, None, id='a plain title becomes a plain slug'),
    pytest.param(12, None, id='umlauts are spelled out, not stripped'),
    pytest.param(13, None, id='the sharp s becomes ss'),
    pytest.param(14, None, id='an accent is decomposed away'),
    pytest.param(15, None, id='a slash cannot open a second path level'),
    pytest.param(16, None, id='punctuation and quotes collapse to single dashes'),
    pytest.param(17, None, id='no leading or trailing dash'),
    pytest.param(18, None, id='emoji leave nothing behind but the words'),
    pytest.param(19, None, id='a title of pure punctuation falls back'),
    pytest.param(20, None, id='an empty title falls back'),
    pytest.param(21, None, id='the fallback can be chosen by the caller'),
    pytest.param(22, None, id='None does not raise'),
    pytest.param(23, None, id='a long title is shortened'),
    pytest.param(24, None, id='and not cut mid-word'),
    pytest.param(25, None, id='a fresh run gets its own dated folder'),
    pytest.param(26, None, id='the protocol drops the date suffix inside it'),
    pytest.param(27, None, id='an existing sitzungen/ is used instead of creating sessions/'),
    pytest.param(28, None, id='and no second folder is invented beside it'),
    pytest.param(29, None, id='so do the proposals'),
    pytest.param(30, None, id='and the event trace'),
    pytest.param(31, None, id='all three land in the same folder'),
    pytest.param(32, None, id='a fresh run is not the flat case'),
    pytest.param(33, None, id='without a title the fallback names the folder'),
    pytest.param(34, None, id='a flat run of the same day is continued where it lies'),
    pytest.param(35, None, id='its proposal file keeps the flat, dated name'),
    pytest.param(36, None, id='and so does its event trace'),
    pytest.param(37, None, id='the flat case says so'),
    pytest.param(38, None, id='no session folder is created beside it'),
    pytest.param(39, None, id="a flat protocol from another day does not capture today's run"),
    pytest.param(40, None, id='--file is honoured in the flat case'),
    pytest.param(41, None, id='--file names the protocol in the session folder too'),
])
def test_01_the_session_folder_and_its_slug(sec_01, site, occ):
    _assert_site(sec_01, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(42, None, id='a fresh run lands in the configured folder'),
    pytest.param(43, None, id='no sessions/ is created beside it'),
    pytest.param(44, None, id='an existing sitzungen/ still wins over the configured name'),
    pytest.param(45, None, id='unset, the default folder name is unchanged'),
])
def test_02_cothinker_sessions_dir_configures_the_folder_name_(sec_02, site, occ):
    _assert_site(sec_02, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(46, None, id='Unicode in the protocol'),
    pytest.param(47, None, id='one final = one line'),
    pytest.param(48, None, id='line break becomes a space'),
    pytest.param(49, None, id='no heading created from the transcript'),
    pytest.param(50, None, id='speaker is set in italics'),
    pytest.param(51, None, id='writes even under the C locale'),
    pytest.param(52, None, id='content correct under the C locale'),
    pytest.param(53, None, id='loss detected'),
    pytest.param(54, None, id='Satz zwei is back'),
    pytest.param(55, None, id='Satz drei is back'),
    pytest.param(56, None, id='Satz eins not duplicated'),
    pytest.param(57, None, id='second pass restores nothing'),
    pytest.param(58, None, id='conflict file detected'),
])
def test_03_protocol(sec_03, site, occ):
    _assert_site(sec_03, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(59, None, id='without a remote: success'),
    pytest.param(60, None, id='without a remote: no error message'),
    pytest.param(61, None, id='committed'),
    pytest.param(62, None, id='empty commit is not an error'),
    pytest.param(63, None, id='empty commit reports nothing'),
    pytest.param(64, None, id='broken remote reports failure'),
    pytest.param(65, None, id='failure is logged'),
    pytest.param(66, None, id='still committed locally'),
    pytest.param(67, None, id="git doesn't block the reader"),
])
def test_04_git(sec_04, site, occ):
    _assert_site(sec_04, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(68, None, id='single block unchanged'),
    pytest.param(69, None, id='queue empty afterwards'),
    pytest.param(70, None, id='backlog gets merged'),
    pytest.param(71, None, id='reason = batch'),
    pytest.param(72, None, id='queue empty afterwards'),
    pytest.param(73, None, id='order preserved'),
    pytest.param(74, None, id='order comes first'),
    pytest.param(75, None, id='order not mixed in'),
    pytest.param(76, None, id='rest merged afterwards'),
    pytest.param(77, None, id='all orders processed'),
    pytest.param(78, None, id="transcript doesn't starve next to the orders"),
    pytest.param(79, None, id='queue runs empty'),
    pytest.param(80, None, id='cap applies'),
    pytest.param(81, None, id='rest stays in the queue'),
    pytest.param(82, None, id='the default cap is 2000, not the old 12000', marks=_ABSENT),
    pytest.param(1082, None, id='the default cap is 12000 in this version'),
    pytest.param(83, None, id='the simulated backlog is well past the old 12000 cap (~7.5k shape from the card)'),
    pytest.param(84, None, id='the backlog took more than one turn to drain -- it was chunked, not run as a single oversized turn'),
    pytest.param(85, None, id="every chunk stays within the cap (plus one block's own overhead)"),
    pytest.param(86, None, id='nothing from the backlog was lost across the chunks'),
    pytest.param(87, None, id='backlog reported'),
    pytest.param(88, None, id='an hour of nonstop talking = one run'),
    pytest.param(89, None, id='nothing lost'),
    pytest.param(90, None, id='first run failed'),
    pytest.param(91, None, id='lost block carries into the next run'),
    pytest.param(92, None, id='carry buffer empty after success'),
    pytest.param(93, None, id='carry buffer capped'),
    pytest.param(94, None, id='the newest survives'),
    pytest.param(95, None, id='order flag survives the failure'),
    pytest.param(96, None, id='normal turn is in flight'),
    pytest.param(97, None, id='order dequeued immediately, not after the normal turn', marks=_ABSENT),
    pytest.param(1097, None, id='order stays queued until the normal turn has returned (one work loop)'),
    pytest.param(98, None, id='normal run started'),
    pytest.param(99, None, id='order dequeued while normal still running', marks=_ABSENT),
    pytest.param(1099, None, id='order is still in the queue while the normal turn runs (one work loop)'),
    pytest.param(100, None, id="order's run() has NOT started yet -- blocked on the shared run lock", marks=_ABSENT),
    pytest.param(1100, None, id="order's run() has NOT started yet -- it waits behind the normal turn in the queue"),
    pytest.param(101, None, id="order's run() starts right after, never overlapping"),
    pytest.param(102, None, id='no two runs overlapped (normal ended before order started)'),
    pytest.param(103, None, id='all 30 orders processed'),
    pytest.param(104, None, id='all 30 normal blocks processed (merging into fewer runs is fine)'),
    pytest.param(105, None, id='queue runs empty'),
])
def test_05_batching_and_backlog(sec_05, site, occ):
    _assert_site(sec_05, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(106, None, id='the proposal file is named'),
    pytest.param(107, None, id='buehne.json is NOT written by the agent any more'),
    pytest.param(108, None, id='the actions are listed'),
    pytest.param(109, None, id='the quote requirement is explicit'),
    pytest.param(110, None, id='the automatic topic change is offered'),
    pytest.param(111, None, id='it is named as the way that needs no order'),
    pytest.param(112, None, id='the prompt no longer forbids what N1 asks for'),
    pytest.param(113, None, id='a request for an earlier surface is routed to the call-out path'),
    pytest.param(114, None, id='the open notice is offered with its identity'),
    pytest.param(115, None, id='the open question is offered with its identity'),
    pytest.param(116, None, id='the notice text is included'),
    pytest.param(117, None, id='both notice kinds are explained'),
    pytest.param(118, None, id='no third trigger is invented'),
    pytest.param(119, None, id='the chat ban survives'),
    pytest.param(120, None, id='append, do not rebuild, survives'),
    pytest.param(121, None, id='nothing new = nothing written survives'),
    pytest.param(122, None, id='the raw transcript stays off limits'),
    pytest.param(123, None, id='the project folder is named'),
    pytest.param(124, None, id='the folder is derived from the protocol path, so they cannot drift'),
    pytest.param(125, None, id='reading it is bounded to what the section demands'),
    pytest.param(126, None, id='and it is read-only'),
])
def test_06_the_prompt_describes_the_new_contract(sec_06, site, occ):
    _assert_site(sec_06, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(127, None, id='nothing open, nothing said'),
    pytest.param(128, None, id='an empty state adds no heading to the prompt'),
    pytest.param(129, None, id='what is open is offered with its identity'),
    pytest.param(130, None, id='a resolved notice drops out of the open-state block'),
    pytest.param(131, None, id='a resolved question drops out too'),
    pytest.param(132, None, id='with everything resolved the block is empty again'),
])
def test_07_the_open_state_block_only_carries_what_is_really_o(sec_07, site, occ):
    _assert_site(sec_07, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(133, None, id='no follow-up without age'),
    pytest.param(134, None, id='the follow-up instruction is in the prompt'),
    pytest.param(135, None, id='it names the identity'),
])
def test_08_the_follow_up_supplement_rides_along_on_the_next_r(sec_08, site, occ):
    _assert_site(sec_08, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(136, None, id='batch run names the backlog'),
    pytest.param(137, None, id='a normal block does not'),
    pytest.param(138, None, id='an order run gets the triad'),
    pytest.param(139, None, id='an order run names the quote requirement'),
    pytest.param(140, 0, id='normal: chat ban present'),
    pytest.param(140, 1, id='batch: chat ban present'),
    pytest.param(140, 2, id='order: chat ban present'),
    pytest.param(141, None, id='curly braces in the transcript survive the template'),
    pytest.param(142, None, id='batch run uses the current field names'),
    pytest.param(143, None, id='batch run does not use the retired field names'),
])
def test_09_the_situational_notes_still_work(sec_09, site, occ):
    _assert_site(sec_09, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(144, None, id='the counter-check routes a value conflict to a notice'),
    pytest.param(145, None, id='the counter-check no longer names a `frage` action'),
    pytest.param(146, None, id='the counter-check keeps both values'),
    pytest.param(147, None, id='the counter-check still asks about rebuilding'),
    pytest.param(148, None, id='links are restricted to files that exist'),
    pytest.param(149, None, id='the main area states its budget'),
    pytest.param(150, None, id='`none` is explained where it is listed'),
    pytest.param(151, None, id='the non-executable order names its route'),
])
def test_10_the_rules_the_counter_check_routes_to_actually_exi(sec_10, site, occ):
    _assert_site(sec_10, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(152, None, id='first_turn defaults to True (every untouched caller keeps working)'),
    pytest.param(153, None, id='turn 1 still carries the instruction (PROTOKOLL path)'),
    pytest.param(154, None, id='turn 1 still carries the action schema'),
    pytest.param(155, None, id='turn 1 still carries the three rules'),
    pytest.param(156, None, id='turn 2+ does NOT repeat the instruction'),
    pytest.param(157, None, id='turn 2+ does NOT repeat the action schema'),
    pytest.param(158, None, id='turn 2+ does NOT repeat the three rules'),
    pytest.param(159, None, id='turn 2+ still carries the spoken block'),
    pytest.param(160, None, id='turn 2+ still carries the open notice/question identities'),
    pytest.param(161, None, id='turn 2+ is drastically smaller than turn 1'),
])
def test_11_b5_the_static_prompt_is_sent_once_per_worker_sessi(sec_11, site, occ):
    _assert_site(sec_11, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(162, None, id='agent_lauf is gone'),
    pytest.param(163, None, id='GEDAECHTNIS is gone'),
    pytest.param(164, None, id='gedaechtnis_text is gone'),
    pytest.param(165, None, id='sitzung.py is gone'),
])
def test_12_the_old_one_shot_path_is_gone(sec_12, site, occ):
    _assert_site(sec_12, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(166, None, id='a call-out that replaced the surface counts as answered'),
    pytest.param(167, None, id='a surface that did not move is not an answer'),
    pytest.param(168, None, id='a MOVED surface without a quote is not an answer either -- that is the topic change, not the order'),
    pytest.param(169, None, id='and that case is named in the trace as the topic change'),
    pytest.param(170, None, id='an explanation in the mirror line is still named'),
    pytest.param(171, None, id='a new headline is still named'),
    pytest.param(172, None, id='silence is still silence'),
    pytest.param(173, None, id='every kind is a token the trace will actually write'),
])
def test_13_an_order_counts_as_answered_only_when_the_surface_(sec_13, site, occ):
    _assert_site(sec_13, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(174, None, id='no slash'),
    pytest.param(175, None, id='no parent traversal'),
    pytest.param(176, None, id='not a drive letter'),
    pytest.param(177, None, id='stable for the same meeting'),
    pytest.param(178, None, id='different per meeting'),
])
def test_14_session_ids_survive_the_path_guard(sec_14, site, occ):
    _assert_site(sec_14, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(179, None, id='schema v2'),
    pytest.param(180, None, id='the operating line is in the file'),
    pytest.param(181, None, id='the content fields are there'),
    pytest.param(182, None, id='since carries a UTC offset'),
    pytest.param(183, None, id='the mirror reached the file'),
    pytest.param(184, None, id='no leftover temp file'),
])
def test_15_the_consumer_is_the_only_writer_of_buehne_json(sec_15, site, occ):
    _assert_site(sec_15, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(185, None, id='a second write with nothing changed does nothing'),
    pytest.param(186, None, id='the file was not touched'),
    pytest.param(187, None, id='a status change does write'),
    pytest.param(188, None, id='a content change does write'),
    pytest.param(189, None, id='and immediately settles again'),
])
def test_16_no_write_without_a_change_the_vault_folder_is_sync(sec_16, site, occ):
    _assert_site(sec_16, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(190, None, id='the decision arrived'),
    pytest.param(191, None, id='the broken line was counted'),
    pytest.param(192, None, id='the warning is in the stage file'),
    pytest.param(193, None, id="and the operating line still answers 'did you hear me'"),
])
def test_17_proposals_reach_the_stage_through_the_consumer(sec_17, site, occ):
    _assert_site(sec_17, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(194, None, id='no half-written stage under concurrent writers'),
    pytest.param(195, None, id='no leftover temp file after the storm'),
])
def test_18_three_threads_write_the_stage_the_file_stays_reada(sec_18, site, occ):
    _assert_site(sec_18, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(196, None, id='the first write happens'),
    pytest.param(197, None, id='nothing changed, nothing written'),
    pytest.param(198, None, id='a missing stage file is written again'),
    pytest.param(199, None, id='and it settles again'),
    pytest.param(200, None, id='a foreign version is replaced'),
    pytest.param(201, None, id='our own state is back in the file'),
    pytest.param(202, None, id='and it settles again after the replacement'),
])
def test_19_the_stage_is_written_again_when_the_file_vanishes_(sec_19, site, occ):
    _assert_site(sec_19, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(203, None, id='a fresh writer reads nothing that was already there'),
    pytest.param(204, None, id='no stale mirror'),
    pytest.param(205, None, id='no stale question'),
    pytest.param(206, None, id='no stale decision'),
    pytest.param(207, None, id='what the agent appends afterwards does arrive'),
    pytest.param(208, None, id="the ingest defaults to the writer's own proposal file"),
])
def test_20_yesterday_s_proposals_do_not_land_on_today_s_stage(sec_20, site, occ):
    _assert_site(sec_20, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(209, None, id='no exception from concurrent tick()/apply()'),
    pytest.param(210, None, id='no notice was swept twice'),
    pytest.param(211, None, id='no notice was lost'),
    pytest.param(212, None, id='no parked entry was lost'),
    pytest.param(213, None, id='the state ends empty'),
])
def test_21_tick_and_apply_from_two_threads_at_once(sec_21, site, occ):
    _assert_site(sec_21, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(214, None, id='ingesting a lone surrogate does not raise'),
    pytest.param(215, None, id='the readable part reached the stage'),
    pytest.param(216, None, id='the value in the state can be encoded'),
    pytest.param(217, None, id='the proposal AFTER the poisoned one is still applied'),
    pytest.param(218, None, id='writing the stage does not raise either'),
    pytest.param(219, None, id='the stage file is valid JSON'),
    pytest.param(220, None, id='a later prompt is built from the poisoned state'),
    pytest.param(221, None, id='a subsequent turn still reaches the stage'),
])
def test_22_a_poisoned_proposal_value_damages_nothing(sec_22, site, occ):
    _assert_site(sec_22, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(222, None, id='write() to an impossible path does not raise'),
    pytest.param(223, None, id='and it reports that nothing was written'),
    pytest.param(224, None, id='it keeps trying instead of assuming the content is on disk'),
])
def test_23_a_stage_that_cannot_be_written_does_not_take_the_c(sec_23, site, occ):
    _assert_site(sec_23, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(225, None, id='the verified call-out replaces the main area'),
    pytest.param(226, None, id='an instruction nobody gave does not buy the main area'),
])
def test_24_a_main_area_is_only_replaced_with_a_quote_that_was(sec_24, site, occ):
    _assert_site(sec_24, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(227, None, id='the parked entry is handed to the agent'),
    pytest.param(228, None, id='it is named as still to be recorded'),
    pytest.param(229, None, id="the prompt supplement uses the prompt's ASCII transliteration"),
    pytest.param(230, None, id='once a run was told, the next prompt is quiet again'),
])
def test_25_the_prompt_names_what_has_to_go_under_offene_frage(sec_25, site, occ):
    _assert_site(sec_25, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(231, None, id='the warning is on its own line'),
    pytest.param(232, None, id='the acknowledgement still stands beside it'),
    pytest.param(233, None, id='the failed run is visible'),
    pytest.param(234, None, id='and the acknowledgement moved with it'),
    pytest.param(235, None, id='a running turn is shown while the last failure still stands'),
    pytest.param(236, None, id='a real warning outranks the failure on that line'),
    pytest.param(237, None, id='only the named warning is withdrawn'),
])
def test_26_the_operating_line_keeps_answering_while_something(sec_26, site, occ):
    _assert_site(sec_26, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(238, None, id='a failed write is reported as failed'),
    pytest.param(239, None, id='restore_missing() does not claim a restoration that failed'),
    pytest.param(240, None, id='nothing is reported as restored when the write back failed'),
    pytest.param(241, None, id='the failure is reported instead'),
    pytest.param(242, None, id='a write that works again is reported as working'),
])
def test_27_a_protocol_that_cannot_be_written_is_not_a_foreign(sec_27, site, occ):
    _assert_site(sec_27, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(243, None, id='the proposal file is git-ignored', marks=_NO_GITIGNORE),
    pytest.param(244, None, id='the event trace is git-ignored too', marks=_NO_GITIGNORE),
    pytest.param(245, None, id='the timing trace is git-ignored too', marks=_NO_GITIGNORE),
    pytest.param(246, None, id='the leak guard has a forbidden-path pattern'),
    pytest.param(247, None, id='the leak guard blocks the proposal file'),
    pytest.param(248, None, id='... also in a subfolder'),
    pytest.param(249, None, id='the protocol stays blocked'),
    pytest.param(250, None, id='an ordinary source file is not blocked'),
])
def test_28_the_proposal_file_is_covered_by_both_guards(sec_28, site, occ):
    _assert_site(sec_28, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(251, None, id='the restart restores the surface'),
    pytest.param(252, None, id='the decision is still there'),
    pytest.param(253, None, id='the notice is still there and still open'),
    pytest.param(254, None, id='the question is still there'),
    pytest.param(255, None, id='no identity is handed out a second time'),
    pytest.param(256, None, id='nothing is warned about after a clean restore'),
    pytest.param(257, None, id='a stage from another day is not taken over'),
    pytest.param(258, None, id='and that is not an alarm'),
    pytest.param(259, None, id='an unreadable stage file is not restored silently'),
    pytest.param(260, None, id='a first start has nothing to restore and says nothing'),
])
def test_29_a_restart_takes_the_running_meeting_s_stage_back_o(sec_29, site, occ):
    _assert_site(sec_29, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(261, None, id='the consumer wrote the stage at all'),
    pytest.param(262, None, id='the stage is schema v2'),
    pytest.param(263, None, id='the proposal reached the stage through a real run'),
    pytest.param(264, None, id='the consumer assigned the identity, not the agent'),
    pytest.param(265, None, id='the mirror came from the same file'),
    pytest.param(266, None, id='the worker started from a path containing a space'),
    pytest.param(267, None, id="the proposal file lies in the meeting's own folder"),
    pytest.param(268, None, id='the folder is dated, so the next meeting gets its own'),
    pytest.param(269, None, id='no shared vorschlag.jsonl is left behind in the project folder'),
    pytest.param(270, None, id='at least two turns ran in this worker session'),
    pytest.param(271, None, id='turn 1 carries the full instruction'),
    pytest.param(272, None, id='turn 2 does NOT repeat the instruction'),
    pytest.param(273, None, id='turn 2 does NOT repeat the action schema'),
    pytest.param(274, None, id='turn 2 still carries the spoken block (new information)'),
    pytest.param(275, None, id='turn 2 is drastically smaller than turn 1'),
    pytest.param(276, None, id='consumer exits on Ctrl-C'),
    pytest.param(278, None, id='the round sees that a turn is running'),
    pytest.param(279, None, id='the working line asks the page to count the duration'),
    pytest.param(280, None, id='an utterance during the turn does not wipe the working line'),
    pytest.param(281, None, id='the duration is counted from ONE moment per turn'),
    pytest.param(282, None, id='the listening line comes back after the turn'),
    pytest.param(283, None, id='the worker answered'),
    pytest.param(284, None, id='no unhandled exception'),
    pytest.param(285, None, id="the transitional 'no proposals are read' note is gone"),
    pytest.param(286, None, id='the meeting ends on the listening line'),
    pytest.param(287, None, id='the raw transcript belongs to the consumer alone'),
    pytest.param(288, None, id='a fresh meeting writes protocol v2'),
    pytest.param(289, None, id='the accepted decision was filed by the consumer as beschluss'),
    pytest.param(290, None, id='the accepted topic opened `### HH:MM Titel`, the tag_entry landed in it'),
    pytest.param(291, None, id='topic block and entry sit between ## Themen and ## Rohtranskript'),
    pytest.param(292, None, id='## Rohtranskript is intact: one heading, every sentence, in order'),
])
def test_30_the_live_path_block_worker_proposals_stage(sec_30, site, occ):
    _assert_site(sec_30, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(293, None, id='the restart continues the file (appending, format 2)'),
    pytest.param(294, None, id="the file's modus wins over the restart's --mode flag"),
    pytest.param(295, None, id='the topic blocks from before the restart are unchanged, a prefix'),
    pytest.param(296, None, id='the new entry is appended after them'),
    pytest.param(297, None, id='the decision is still filed once after the restart'),
    pytest.param(298, None, id='the raw section keeps the old lines and appends the new sentence'),
    pytest.param(299, None, id='the restart raised no unhandled exception'),
])
def test_31_restart_on_an_existing_v2_protocol_it_keeps_append(sec_31, site, occ):
    _assert_site(sec_31, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(300, None, id='a conflict file appearing mid-meeting is announced'),
    pytest.param(301, None, id='the operating line survives the warning'),
    pytest.param(302, None, id='the warning survives a successful turn while it is still true'),
    pytest.param(303, None, id='a warning that is no longer true is withdrawn'),
    pytest.param(304, None, id='the dry run exits on Ctrl-C'),
    pytest.param(306, None, id='the acknowledgement keeps moving without any worker'),
    pytest.param(307, None, id='turns really ran in the dry run'),
    pytest.param(308, None, id='no unhandled exception in the dry run'),
    pytest.param(309, None, id='a call-out announces a main entry in the trace'),
    pytest.param(310, None, id='an unanswered call-out ends its announcement as unanswered'),
])
def test_32_without_an_agent_the_acknowledgement_keeps_running(sec_32, site, occ):
    _assert_site(sec_32, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(311, None, id='the dropped stream reaches the stage'),
    pytest.param(312, None, id='the acknowledgement is not blanked by it'),
    pytest.param(313, None, id='the warning is withdrawn once the stream is back'),
    pytest.param(314, None, id='the reader kept reading'),
    pytest.param(315, None, id='no unhandled exception while the stream dropped'),
])
def test_33_a_dropped_speech_to_text_stream_is_announced_and_w(sec_33, site, occ):
    _assert_site(sec_33, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(316, None, id='without a vault a graph action is refused, not guessed'),
    pytest.param(317, None, id='with a vault the payload is filled in by the consumer'),
    pytest.param(318, None, id='the state takes it and the surface is a graph'),
    pytest.param(319, None, id='the trace reports the graph, with numbers only'),
])
def test_34_the_graph_action_the_consumer_builds_the_state_onl(sec_34, site, occ):
    _assert_site(sec_34, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(320, None, id='announce returns the id'),
    pytest.param(321, None, id='the pending entry is in buehne.json right after the call'),
    pytest.param(322, None, id='an unknown kind announces nothing and writes nothing new'),
    pytest.param(323, None, id='pending_start reached the trace with id and kind'),
    pytest.param(324, None, id='pending_end reached the trace with outcome and duration'),
    pytest.param(325, None, id='the trace carries no label'),
])
def test_35_an_announcement_is_written_immediately_and_traced_(sec_35, site, occ):
    _assert_site(sec_35, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(326, None, id="the call-out's label is Zuruf: plus <= 3 content words"),
    pytest.param(327, None, id="the call-out's label is not the block"),
    pytest.param(328, None, id='the call-out canary is on the stage but not in the trace'),
    pytest.param(329, None, id='a wake word in the LAST sentence: the label names the order, not the earlier talk'),
    pytest.param(330, None, id='a call-out block without content words keeps the old label'),
])
def test_36_a_call_out_s_announcement_names_the_what_the_trace(sec_36, site, occ):
    _assert_site(sec_36, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(331, None, id="the first call-out's run ends only its own announcement"),
    pytest.param(332, None, id="the second call-out's run ends the second"),
    pytest.param(333, None, id="a call-out announced during a run survives that run's answer"),
    pytest.param(334, None, id='a failed run ends its own announcement as failed'),
    pytest.param(335, None, id='a call-out wrapped with carried-over text still finds its own'),
    pytest.param(336, None, id='every outcome is a token the trace will write'),
])
def test_37_a_call_out_run_settles_its_own_announcement_never_(sec_37, site, occ):
    _assert_site(sec_37, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(337, None, id='a new protocol is v2'),
    pytest.param(338, None, id='the sitzung header is HEADER_V2 verbatim'),
    pytest.param(339, None, id='and its view is exactly what render_view gives an empty protocol'),
    pytest.param(340, None, id='another mode gets its own sections from the start'),
    pytest.param(341, None, id='an agenda becomes ## Ablauf right above ## Überblick'),
    pytest.param(342, None, id='a protocol with an agenda is still v2'),
    pytest.param(343, None, id='splice keeps the agenda byte-identical'),
    pytest.param(344, None, id='without an agenda nothing changes'),
    pytest.param(345, None, id='read_agenda drops comments, blanks and numbering, caps the list'),
    pytest.param(346, None, id="read_agenda('') is empty"),
    pytest.param(348, None, id='a missing agenda file raises OSError'),
    pytest.param(349, None, id='--agenda exists'),
    pytest.param(351, None, id='an agenda with invalid UTF-8 raises UnicodeDecodeError'),
    pytest.param(352, None, id='main() turns an undecodable agenda into a start-up error'),
    pytest.param(353, None, id='--mode exists, choices from protocol_doc, default sitzung'),
    pytest.param(354, None, id='the protocol recognises a v2 file'),
    pytest.param(355, None, id='an existing v1 file stays v1'),
    pytest.param(356, None, id='a missing file is not v2 and does not raise'),
])
def test_38_protocol_v2_the_consumer_is_the_only_writer_of_pro(sec_38, site, occ):
    _assert_site(sec_38, site, occ)




@pytest.mark.parametrize("site, occ", [
    pytest.param(362, None, id='refused as v1_protocol'),
    pytest.param(363, None, id='the v1 file is byte-identical'),
    pytest.param(364, None, id='a refused tag_entry is not a surface change'),
])
def test_40_v1_a_tag_entry_is_refused_the_file_is_not_touched(sec_40, site, occ):
    _assert_site(sec_40, site, occ)






@pytest.mark.parametrize("site, occ", [
    pytest.param(396, None, id='a parked notice is filed as frage'),
    pytest.param(397, None, id='an expired question is filed as frage'),
    pytest.param(398, None, id='and both are marked recorded'),
    pytest.param(399, None, id='the v2 prompt does not ask the agent to file them'),
    pytest.param(400, None, id='open_state_block(include_unrecorded=False) omits the part'),
    pytest.param(401, None, id='build_prompt(fmt=1) is unchanged'),
    pytest.param(402, None, id='build_prompt(fmt=2) omits it, first and later turns'),
    pytest.param(403, None, id='under v1 nothing is filed by the consumer'),
])
def test_43_v2_what_fell_off_the_stage_is_filed_by_the_consume(sec_43, site, occ):
    _assert_site(sec_43, site, occ)




@pytest.mark.parametrize("site, occ", [
    pytest.param(410, None, id='v2 first turn is AGENT_PROMPT_V2, not the v1 text'),
    pytest.param(411, None, id='it offers the tag_entry action'),
    pytest.param(412, None, id='it names all six tags'),
    pytest.param(413, None, id='each tag has a meaning line'),
    pytest.param(414, None, id='no v1 section is a write target any more'),
    pytest.param(415, None, id='the protocol is read-only for the agent'),
    pytest.param(416, None, id='only the proposal file is written'),
    pytest.param(417, None, id='decisions go through `decision` only, never also as tag_entry'),
    pytest.param(418, None, id='a topic opens the new topic block'),
    pytest.param(419, None, id='graph never replaces topic: on a topic change, topic goes FIRST'),
    pytest.param(420, None, id='the v1 prompt does not carry the v2 graph/topic order rule'),
    pytest.param(421, None, id='the decision_breach notice points at the overview'),
    pytest.param(422, None, id='at most ONE question, the rest as tag_entry frage'),
    pytest.param(423, None, id='done work becomes a notiz entry starting **erledigt**'),
    pytest.param(424, None, id='nothing new = only the none line'),
    pytest.param(425, None, id='the file list is there'),
    pytest.param(426, None, id='the wikilink rule names only listed files'),
    pytest.param(427, None, id='the rules from the real meeting survive'),
    pytest.param(428, None, id='the stage actions are all still offered'),
    pytest.param(429, None, id='the open entries still ride along'),
    pytest.param(430, None, id='the raw transcript stays off limits'),
    pytest.param(431, None, id='the prompt is ASCII-transliterated prose (no umlauts outside headings)'),
    pytest.param(432, None, id='braces in file names survive the template'),
    pytest.param(433, None, id='no file names -> the list says so'),
    pytest.param(434, None, id='the file list is capped at 60'),
    pytest.param(435, None, id='a name that cannot be a link is not offered'),
])
def test_45_v2_prompt_the_agent_proposes_tagged_entries_the_pr(sec_45, site, occ):
    _assert_site(sec_45, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(436, None, id='ORDER_NOTE_V2 names no v1 section'),
    pytest.param(437, None, id='it keeps the triad and the quote rule'),
    pytest.param(438, None, id='it routes the result to a tag_entry'),
    pytest.param(439, None, id='the v1 order note is untouched'),
])
def test_46_v2_order_note_the_triad_without_v1_sections(sec_46, site, occ):
    _assert_site(sec_46, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(440, None, id='v1 and v2 later turns are the same DELTA_PROMPT'),
])
def test_47_later_turns_delta_prompt_shared_by_both_formats(sec_47, site, occ):
    _assert_site(sec_47, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(441, None, id='fmt=1 ignores file_names'),
    pytest.param(442, None, id='fmt=1 first turn is exactly AGENT_PROMPT.format(...) (byte for byte)'),
    pytest.param(443, None, id='the v1 order run is AGENT_PROMPT with ORDER_NOTE in the situation'),
    pytest.param(444, None, id='a wake-word turn gets ORDER_NOTE_V2 in v2, ORDER_NOTE in v1, none without'),
    pytest.param(445, None, id='the v2 link rule also allows the file just made on request'),
    pytest.param(446, None, id='writing a project file only on an explicit order, never the protocol'),
    pytest.param(447, None, id='fmt=1 still is AGENT_PROMPT with its sections'),
])
def test_48_v1_prompt_unchanged(sec_48, site, occ):
    _assert_site(sec_48, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(448, None, id='a live v2 protocol gives fmt 2'),
    pytest.param(449, None, id='the next write notices the file is v1 now'),
    pytest.param(450, None, id='the turn falls back to fmt 1 and says it switched'),
    pytest.param(451, None, id='the writer no longer treats the file as v2'),
    pytest.param(452, None, id='the next turn stays v1 without a second log line'),
    pytest.param(453, None, id='a v1 run from the start is fmt 1, no switch'),
])
def test_49_v2_v1_mid_run_the_turn_falls_back_to_the_v1_prompt(sec_49, site, occ):
    _assert_site(sec_49, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(454, None, id="it returns the prompt's file names, sessions excluded"),
    pytest.param(455, None, id='and refreshes the full set for the link check'),
    pytest.param(456, None, id='a raising filing step is logged, not raised'),
])
def test_50_v2_turn_preparation_never_raises_into_the_turn(sec_50, site, occ):
    _assert_site(sec_50, site, occ)






@pytest.mark.parametrize("site, occ", [
    pytest.param(461, None, id='an ordered tag_entry ends with its marker'),
    pytest.param(462, None, id='and the order is reported delivered'),
    pytest.param(463, None, id='delivered() is called outside the state lock'),
    pytest.param(464, None, id="an ordered topic's block heading carries the marker"),
    pytest.param(465, None, id='the stage title does not'),
    pytest.param(466, None, id='the topic order is delivered, outside the state lock'),
    pytest.param(467, None, id="an ordered decision's beschluss entry ends with the marker"),
    pytest.param(468, None, id='the stage decision does not'),
    pytest.param(469, None, id='the decision order is delivered, outside the state lock'),
    pytest.param(470, None, id='an unknown or garbage order id adds no marker, the entries still land'),
    pytest.param(471, None, id='and nothing is delivered'),
])
def test_53_level_2_a_proposal_answering_an_order_gets_its_mar(sec_53, site, occ):
    _assert_site(sec_53, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(472, None, id='policy is None by default'),
    pytest.param(473, None, id='without a policy the order field changes nothing (byte-identical)'),
    pytest.param(474, None, id='file_entry without a mark is the plain add_entry'),
])
def test_54_level_2_off_the_writer_files_exactly_what_it_filed(sec_54, site, occ):
    _assert_site(sec_54, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(475, None, id="the orders' note becomes the turn's extra order note"),
    pytest.param(476, None, id='the ordinary note plus it is what reaches order_note'),
    pytest.param(477, None, id="spoken is the block's own text without a wake order"),
    pytest.param(478, None, id='a wake order on a block WITHOUT the wake word adds the v2 order note'),
    pytest.param(479, None, id="and spoken is extended with the wake block's text"),
    pytest.param(480, None, id='in a v1 turn the v1 order note'),
    pytest.param(481, None, id='a block WITH the wake word carries its order note once, not twice'),
    pytest.param(482, None, id='without a runner: no note, spoken is the block text (as before)'),
    pytest.param(483, None, id='PolicyRunner is imported lazily, only under --policy'),
])
def test_55_level_2_policy_turn_what_a_turn_takes_from_the_pol(sec_55, site, occ):
    _assert_site(sec_55, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(484, None, id='without the id the policy question is listed as resolvable'),
    pytest.param(485, None, id='with the id it leaves the resolvable list ...'),
    pytest.param(486, None, id='... and gets its own line instead'),
    pytest.param(487, None, id="the policy question alone: no 'nur du kannst das aufloesen' heading"),
    pytest.param(488, None, id='an id that is not open changes nothing'),
    pytest.param(489, None, id='an empty id is byte for byte the block without it'),
    pytest.param(490, 0, id='build_prompt(fmt=1, first_turn=True) with an empty policy_ask_id is byte-identical'),
    pytest.param(490, 1, id='build_prompt(fmt=1, first_turn=False) with an empty policy_ask_id is byte-identical'),
    pytest.param(490, 2, id='build_prompt(fmt=2, first_turn=True) with an empty policy_ask_id is byte-identical'),
    pytest.param(490, 3, id='build_prompt(fmt=2, first_turn=False) with an empty policy_ask_id is byte-identical'),
    pytest.param(491, 0, id='build_prompt(fmt=1, first_turn=True) passes the id on'),
    pytest.param(491, 1, id='build_prompt(fmt=1, first_turn=False) passes the id on'),
    pytest.param(491, 2, id='build_prompt(fmt=2, first_turn=True) passes the id on'),
    pytest.param(491, 3, id='build_prompt(fmt=2, first_turn=False) passes the id on'),
    pytest.param(492, None, id="run_turn hands the runner's open ask to build_prompt"),
])
def test_56_level_2_its_own_question_is_not_the_agent_s_to_res(sec_56, site, occ):
    _assert_site(sec_56, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(493, None, id='default first-turn rendering is byte-identical to f1bf253 (pinned sha256)'),
    pytest.param(494, None, id='default delta rendering is byte-identical to f1bf253 (pinned sha256)'),
])
def test_57_topic_criterion_c2_measure_1_default_off_byte_iden(sec_57, site, occ):
    _assert_site(sec_57, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(495, None, id='the criterion heading is present'),
    pytest.param(496, None, id='marker (a) -- a new subject -- is present'),
    pytest.param(497, None, id='marker (b) -- the round returns to an older topic -- is present'),
    pytest.param(498, None, id='marker (c) -- the explicit markers -- is present'),
    pytest.param(499, None, id='the KEIN-Wechsel counter-rule is present'),
    pytest.param(500, None, id='the three invented negative examples are present'),
    pytest.param(501, None, id='the criterion sits after the topic bullet and before the graph bullet'),
])
def test_58_topic_criterion_true_the_criterion_text_positioned(sec_58, site, occ):
    _assert_site(sec_58, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(502, None, id='mutation fixture actually dropped the counter-rule line'),
    pytest.param(503, None, id='mutation: dropping the counter-rule line fails the counter-rule assert'),
    pytest.param(504, None, id='mutation: the (a)/(b)/(c) markers survive the counter-rule drop'),
    pytest.param(505, None, id='mutation fixture actually dropped marker (c)'),
    pytest.param(506, None, id='mutation: dropping marker (c) fails the marker-(c) assert'),
    pytest.param(507, None, id='mutation: the counter-rule survives the marker-(c) drop'),
    pytest.param(508, None, id='mutation fixture is restored after both mutations'),
])
def test_59_topic_criterion_true_mutation_tests_the_asserts_ch(sec_59, site, occ):
    _assert_site(sec_59, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(509, None, id='the delta turn with topic_criterion=True carries no criterion text'),
    pytest.param(510, None, id='the delta turn with topic_criterion=True carries no other AGENT_PROMPT text'),
    pytest.param(511, None, id='the delta turn with topic_criterion=True still carries the spoken block'),
])
def test_60_topic_criterion_true_b5_the_delta_turn_never_repea(sec_60, site, occ):
    _assert_site(sec_60, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(512, None, id='the canary word from the spoken block reaches the rendered prompt'),
    pytest.param(513, None, id='the canary word appears exactly once -- only inside the spoken block'),
    pytest.param(514, None, id='none of the invented canary words leak into the static criterion'),
])
def test_61_topic_criterion_canary_no_session_text_leaks_into_(sec_61, site, occ):
    _assert_site(sec_61, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(515, None, id='default (thread=None) first-turn rendering still matches the topic_criterion pin'),
    pytest.param(516, None, id='default (thread=None) delta rendering still matches the topic_criterion pin'),
])
def test_62_thread_c2_measure_2_default_off_byte_identical_to_(sec_62, site, occ):
    _assert_site(sec_62, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(517, None, id='last n=4 bullets, in order, the not-a-bullet line ignored'),
    pytest.param(518, None, id='a bullet planted below ## Rohtranskript never comes back'),
    pytest.param(519, None, id='n is respected (n=2)'),
    pytest.param(520, None, id='missing ## Verlauf section -> []'),
    pytest.param(521, None, id='default n is THREAD_BULLETS'),
    pytest.param(522, None, id='THREAD_BULLETS is 4'),
])
def test_63_recent_verlauf_last_n_bullets_of_verlauf_never_roh(sec_63, site, occ):
    _assert_site(sec_63, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(523, None, id='the header line is present'),
    pytest.param(524, None, id='the headline line is present'),
    pytest.param(525, None, id='the bullets header is present'),
    pytest.param(526, None, id='both bullets are present, each on its own line'),
    pytest.param(527, None, id="the thread sits between the open state and 'Neu gesprochen'"),
    pytest.param(528, None, id='B5: the first turn (full AGENT_PROMPT) is not changed by thread'),
])
def test_64_thread_the_block_appears_between_open_state_and_ne(sec_64, site, occ):
    _assert_site(sec_64, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(529, None, id="empty headline renders '(noch keine)'"),
    pytest.param(530, None, id="empty bullet list renders a single '(noch keine)' line"),
])
def test_65_thread_empty_list_still_renders_the_headline_and_n(sec_65, site, occ):
    _assert_site(sec_65, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(531, None, id='no AGENT_PROMPT path line'),
    pytest.param(532, None, id='no AGENT_PROMPT action catalogue'),
    pytest.param(533, None, id='the spoken block still arrives'),
])
def test_66_thread_b5_the_delta_with_a_thread_carries_no_agent(sec_66, site, occ):
    _assert_site(sec_66, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(534, None, id='mutation fixture actually dropped the thread placeholder'),
    pytest.param(535, None, id='mutation: dropping the placeholder fails the headline-line assert'),
    pytest.param(536, None, id='mutation: dropping the placeholder fails the bullets assert'),
    pytest.param(537, None, id='mutation fixture is restored'),
])
def test_67_thread_mutation_dropping_the_headline_line_or_the_(sec_67, site, occ):
    _assert_site(sec_67, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(538, None, id='fixture carries exactly THREAD_BULLETS=4 bullets'),
    pytest.param(539, None, id='thread block character count is pinned (D7 i)'),
])
def test_68_thread_token_growth_the_thread_block_s_character_c(sec_68, site, occ):
    _assert_site(sec_68, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(540, None, id='the canary bullet reaches the rendered prompt'),
    pytest.param(541, None, id='the canary word appears exactly once -- only inside the thread block'),
    pytest.param(542, None, id='the other invented canary word never leaks anywhere'),
])
def test_69_thread_canary_an_invented_word_in_synthetic_bullet(sec_69, site, occ):
    _assert_site(sec_69, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(543, None, id='the override folder is returned verbatim'),
    pytest.param(544, None, id="pslug comes from the override folder's own name"),
    pytest.param(546, None, id='a non-existent override directory is a clear SystemExit, not a silent fallback'),
])
def test_70_cothinker_project_dir_overrides_the_entities_looku(sec_70, site, occ):
    _assert_site(sec_70, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(547, None, id='worker_command_hermes_home() reads the embedded --hermes-home', marks=_ABSENT),
    pytest.param(548, None, id="worker_command_hermes_home() of an empty command is ''", marks=_ABSENT),
    pytest.param(549, None, id="worker_command_hermes_home() without the flag is ''", marks=_ABSENT),
    pytest.param(550, None, id='resolve_turn_timeout_s() reads agent.turn_timeout_s from the profile named in --worker-command when no env override is set', marks=_ABSENT),
    pytest.param(551, None, id='the turn timeout defaults to 900s without an env override'),
    pytest.param(552, None, id='COTHINKER_TURN_TIMEOUT sets the turn timeout'),
])
def test_71_agent_turn_timeout_s_config_key(sec_71, site, occ):
    _assert_site(sec_71, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(553, None, id='the copy carries the block time, never the wake flag', marks=_ABSENT),
    pytest.param(554, None, id='block_stamps: ordered, deduplicated, non-stamp sentences ignored', marks=_ABSENT),
    pytest.param(555, None, id='order stamp wins over a foreign agent time', marks=_ABSENT),
    pytest.param(556, None, id="agent time kept when it is one of the order's", marks=_ABSENT),
    pytest.param(557, None, id='batch stamps when there is no order', marks=_ABSENT),
    pytest.param(558, None, id="agent time kept when it is one of the batch's", marks=_ABSENT),
    pytest.param(559, None, id='no stamps at all: unchanged (serial path)', marks=_ABSENT),
])
def test_72_protocol_lane_helpers(sec_72, site, occ):
    _assert_site(sec_72, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(560, None, id='topic and headline refused', marks=_ABSENT),
    pytest.param(561, None, id='two proposal_rejected reason=protocol_lane events', marks=_ABSENT),
    pytest.param(562, None, id="foreign agent time replaced by the batch's first stamp", marks=_ABSENT),
    pytest.param(563, None, id="agent time kept when it is one of the batch's", marks=_ABSENT),
    pytest.param(564, None, id='Rohtranskript section unchanged', marks=_ABSENT),
])
def test_73_stagewriter_ingest_allowed_protocol_lane_actions_s(sec_73, site, occ):
    _assert_site(sec_73, site, occ)


@pytest.mark.parametrize("site, occ", [
    pytest.param(565, None, id='no exception on list/dict/int action', marks=_ABSENT),
    pytest.param(566, None, id='valid protocol action after the bad lines is still applied', marks=_ABSENT),
])
def test_74_protocol_lane_gate_unhashable_non_string_action_do(sec_74, site, occ):
    _assert_site(sec_74, site, occ)




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
                if any(m.name == "xfail" for m in getattr(param, "marks", ())):
                    names.add(name)
    return names


_XFAIL_SCENARIOS = _scenarios_with_expected_failures()


@pytest.fixture(autouse=True)
def _setup_error_guard(request):
    """Scenarios without expected failures must have recorded no setup error."""
    global _SCENARIO_EXPECTS_FAILURES
    _SCENARIO_EXPECTS_FAILURES = request.node.originalname in _XFAIL_SCENARIOS
    yield
