#!/usr/bin/env python3
# CoThinker - the stage with made-up content, for design work
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Start the stage and the graph tab against a synthetic meeting.

    python3 scripts/demo-stage.py            # http://127.0.0.1:8030/?t=demo
    python3 scripts/demo-stage.py --port 8031 --keep

No STT, no consumer, no agent, no Hermes, no network: a throwaway project
folder under $TMPDIR is filled with a hand-written `buehne.json` and
`protokoll.md` (the invented "Gartenrunde" from `tests/alpha/project/`),
then `stage/stage.py` is started on it. Every zone of the stage carries
something, so a design change can be judged in one look.

Edit the files while it runs -- the page polls every 3 s and swaps what
changed, exactly as in a meeting. Static files (`stage/static/`) are
served no-store: change, F5, done. `stage/stage.py` itself (STYLE,
SCRIPT, GRAPH_STYLE) needs a restart of this script.

All content is invented. Nothing here reads or writes the vault.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time as _zeit
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "tests" / "alpha" / "project"
SESSION = "2026-09-26-saisonplanung"


def stamp(minutes_ago: float) -> str:
    """A timestamp relative to now, with offset -- the stage counts ages
    in the browser, so fixed dates would make every entry look old."""
    t = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
    return t.isoformat(timespec="seconds")


def hm(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%H:%M")


def stage_json() -> dict:
    history = [
        {"id": "M1", "title": "Rückblick Saison", "set_at": stamp(15),
         "markdown": "## Rückblick Saison\n\n"
                     "- 11 Hochbeete in Betrieb, 2 davon betreut von der Kita\n"
                     "- Mitglieder: 24 → 31\n"
                     "- Problem: Hitze im August, Salat fast komplett verloren",
         "collapsed_lines": 0, "collapsed_markdown": ""},
        {"id": "M2", "title": "Budget Saatgut & Werkzeug", "set_at": stamp(8),
         "markdown": "## Budget Saatgut & Werkzeug\n\n"
                     "**1.400 €** stehen fürs nächste Jahr zur Verfügung.\n\n"
                     "| Posten | Vorjahr | Vorschlag |\n|---|---|---|\n"
                     "| Saatgut (samenfest) | 500 € | **600 €** |\n"
                     "| Werkzeug + Rücklage | 300 € | 800 € |\n\n"
                     "- Zwei neue Schubkarren nötig — die alten sind durchgerostet\n"
                     "- Details: [[budget]], Angebote in [[aufgaben]]",
         "collapsed_lines": 0, "collapsed_markdown": ""},
        {"id": "M3", "title": "Bewässerung", "set_at": stamp(1.5),
         "markdown": "## Bewässerung\n\n"
                     "Der August hat gezeigt: ohne Wasserspeicher geht der "
                     "Salat ein.\n\n"
                     "- Idee: **drei Regentonnen** am Schuppen, je ~300 l\n"
                     "- Tröpfchenschlauch für die Hochbeete prüfen\n"
                     "- Wer kennt einen Anbieter? → [[bewaesserung]]\n\n"
                     "> Offen: Reicht das Dach des Schuppens als Auffangfläche?",
         "collapsed_lines": 0, "collapsed_markdown": ""},
    ]
    last = history[-1]
    return {
        "version": 2, "updated": stamp(0),
        # `warning` is normally empty; it is set here so the trouble line
        # has something in it and can be designed like every other zone.
        "status": {"state": "listening", "ticking": False, "since": "",
                   "text": f"🎧 hört zu · zuletzt {datetime.now():%H:%M:%S}",
                   "warning": "⚠️ Konfliktdateien im Projektordner — ein "
                              "Sync-Dienst schreibt mit (Demo)"},
        "mirror": "Notiert: Regentonnen als Vorschlag, noch kein Beschluss",
        "headline": "Gartenrunde Lindenhof – Saisonplanung",
        "main": {k: last[k] for k in ("id", "markdown", "set_at",
                                       "collapsed_lines", "collapsed_markdown")},
        "history": history,
        "decisions": [
            {"id": "D1", "time": hm(9), "added_at": stamp(9),
             "text": "600 € für Saatgut, Rest für Werkzeug und Rücklage"},
            {"id": "D2", "time": hm(6), "added_at": stamp(0.1),
             "text": "Zwei Schubkarren werden angeschafft"},
        ],
        "notices": [
            {"id": "N1", "kind": "value_conflict", "raised_at": stamp(13),
             "text": "Erntefest: **80** vs. **90** Besucher — beide Zahlen genannt",
             "state": "open", "resolution": "", "follow_up": False},
        ],
        "question": {"id": "Q1", "raised_at": stamp(1),
                     "text": "Wer holt bis Oktober ein Angebot für die "
                             "Regentonnen ein?",
                     "state": "open", "resolution": "", "follow_up": False},
        "links": [{"file": "budget", "label": "Budget"},
                  {"file": "bewaesserung", "label": "Bewässerung"},
                  {"file": "aufgaben", "label": "Aufgaben"}],
        "parked": 0, "parked_entries": [],
        "pending": [{"kind": "research", "label": "Recherche: Regentonne 300 l",
                     "since": stamp(0.1), "id": "P7"}],
        "age_visible_after_s": 20, "follow_up_after_s": 120,
        # The built-in agenda zone (gateway/consumer.py::AgendaController,
        # fed by the timer and speaker-queue view intents):
        # on item 3 of 4, 1.5 min into a 5-min budget, with a speaker queue
        # cycling. `item_started_at`/`meeting_deadline` are epoch seconds --
        # the page counts down from them itself, so they have to be real
        # "now"-relative numbers, not the `stamp()` ISO labels the rest of
        # this fixture uses.
        "agenda": {
            "current": 3, "current_text": "Bewässerung",
            "next": 4, "next_text": "Erntefest-Planung", "total": 4,
            "item_started_at": _zeit.time() - 90, "item_budget_s": 300,
            "meeting_deadline": _zeit.time() + 40 * 60,
            "people": ["Jonas", "Mira", "Theo"], "speaker": "Mira",
            "next_speaker": "Theo",
        },
    }


def protocol() -> str:
    return f"""---
title: "Gartenrunde Lindenhof – Saisonplanung"
entity: demo
protokoll_format: 2
modus: sitzung
---

# Gartenrunde Lindenhof – Saisonplanung

⚠️ **Automatische Transkription.** Eigennamen und Zahlen sind unbestaetigt.
**Start:** {hm(18)}

## Überblick

<!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem Eintrag neu. -->

### Beschlüsse

- {hm(9)} 600 € für Saatgut, Rest für Werkzeug und Rücklage (Thema: Budget)
- {hm(6)} Zwei Schubkarren werden angeschafft (Thema: Budget)

### Aufgaben

- {hm(5)} Jonas holt Angebote für Schubkarren ein (Thema: Budget)

### Offene Fragen

- {hm(1)} Wer holt ein Angebot für die Regentonnen ein? (Thema: Bewässerung)

## Themen

### {hm(15)} Rückblick Saison
- {hm(14)} [notiz] 11 Hochbeete, Mitglieder 24 → 31
- {hm(12)} [frage] Erntefest: 80 oder 90 Besucher?

### {hm(8)} Budget Saatgut & Werkzeug -> [[budget]], [[aufgaben]]
- {hm(9)} [beschluss] 600 € für Saatgut, Rest für Werkzeug und Rücklage
- {hm(6)} [beschluss] Zwei Schubkarren werden angeschafft
- {hm(5)} [aufgabe] Jonas holt Angebote für Schubkarren ein

### {hm(2)} Bewässerung -> [[bewaesserung]]
- {hm(1)} [idee] Drei Regentonnen am Schuppen, siehe [[bewaesserung]]
- {hm(1)} [frage] Wer holt ein Angebot für die Regentonnen ein?
- {hm(0)} [recherche] Preise Regentonne 300 l

## Rohtranskript

**{hm(18)}:04** Hallo zusammen, schön dass ihr alle da seid.
**{hm(18)}:11** Wir haben heute sechs Punkte.
**{hm(2)}:31** Ich würde drei Regentonnen am Schuppen vorschlagen.
"""


def build(root: Path) -> Path:
    project = root / "entities" / "demo" / "projekte" / "gartenrunde"
    shutil.copytree(NOTES, project)
    session = project / "sitzungen" / SESSION
    session.mkdir(parents=True)
    (session / "buehne.json").write_text(
        json.dumps(stage_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    (session / "protokoll.md").write_text(protocol(), encoding="utf-8")
    return session


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage + graph tab against a synthetic meeting.")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--keep", action="store_true",
                    help="keep the throwaway folder after stopping")
    a = ap.parse_args()

    # SIGTERM (kill, a closed terminal) takes the same exit as Ctrl-C, so
    # the throwaway folder does not outlive the demo.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(
        KeyboardInterrupt()))
    root = Path(tempfile.mkdtemp(prefix="cothinker-demo-"))
    session = build(root)
    print(f"demo project: {session}", flush=True)
    print(f"edit buehne.json / protokoll.md there -- the page follows in 3 s",
          flush=True)
    env = dict(os.environ, COTHINKER_ENTITIES=str(root / "entities"))
    cmd = [sys.executable, str(ROOT / "stage" / "stage.py"),
           "--project", "gartenrunde", "--port", str(a.port),
           "--host", "127.0.0.1", "--token", "demo",
           "--vault", str(root / "entities")]
    print(f"stage:     http://127.0.0.1:{a.port}/?t=demo", flush=True)
    print(f"graph tab: http://127.0.0.1:{a.port}/graph?t=demo", flush=True)
    try:
        return subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        return 0
    finally:
        if a.keep:
            print(f"kept: {root}", flush=True)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
