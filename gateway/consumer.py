#!/usr/bin/env python3
# CoThinker — consumer of the STT event stream
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""CoThinker consumer: reads the event stream of the STT server.

The STT server runs on the machine in the meeting and delivers a `final`
after every speaking pause. We only consume it, we never change it.

Flow:
  1. every `final` goes into the raw transcript immediately (nothing is lost)
  2. sentences are bundled into meaningful blocks (gateway/segmenter.py)
  3. a turn of the long-lived agent worker is triggered per finished block:
     it maintains the protocol and PROPOSES stage entries
  4. the consumer applies the proposals and writes the stage itself
  5. blocks with the wake word take priority

  python3 consumer.py --stt http://<host>:5051 --project <slug>

Three rules that come from a real meeting and are not negotiable:

  * The SSE reader must NEVER block and NEVER die. Anything slow (git) or
    anything that can go wrong (block building, agent run) runs in its own
    thread and catches its own errors.
  * Every `final` goes into the raw transcript FIRST, then into batching.
  * The agent reports exit 0 even on a failed API call. That's why the
    answer TEXT is checked as well (`FAILURE_MARKERS` in gateway/worker.py,
    pinned by a test). Never remove this check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from segmenter import Block, BlockBuilder  # noqa: E402
from correction import (CorrectionWorker, Corrector, MODEL_CHAIN,  # noqa: E402
                        build_glossary)
import correction_prefilter  # noqa: E402
from infomaniak_chat import Client as InfomaniakClient  # noqa: E402
from infomaniak_chat import api_key as infomaniak_key  # noqa: E402
from correction import FINISH_GRACE_S, FINISH_TIMEOUT_S  # noqa: E402
from shared.events import EventLog, is_token  # noqa: E402
from proposals import read_proposals  # noqa: E402
from shared.minutes_format import (DEFAULT_MODE, MODES, TAGS, Entry,  # noqa: E402
                          Topic, clean_line, clean_wikilinks, entry_line,
                          marker, parse_topics, pending_label,
                          project_md_names, read_mode, render_view, splice,
                          topic_line, with_marker)
from shared.minutes_format import is_v2 as is_v2_text  # noqa: E402
from shared.minutes_format import _ROH_LINE_RE  # noqa: E402
from stage_state import PARKED, StageState, scrub  # noqa: E402
from stage_state import (detect_agenda_command,  # noqa: E402
                         meeting_deadline, parse_agenda)
from stage_texts import status as render_status  # noqa: E402
from stage_texts import trouble as trouble_text  # noqa: E402
from stage_texts import warning as warning_text  # noqa: E402
from stt.pipeline.speaker_labels.enroll import (  # noqa: E402
    DEFAULT_THRESHOLD as SPEAKER_THRESHOLD_DEFAULT)

# Wall-clock cap per agent turn (seconds). Live 2026-10-20: one ORDER ran
# 614 s and nine blocks waited behind it. Unset = 900, the old default.
try:
    TURN_TIMEOUT_S = float(os.environ.get("COTHINKER_TURN_TIMEOUT", "") or 900)
except ValueError:
    TURN_TIMEOUT_S = 900.0
from timing import TimingLog, valid_span  # noqa: E402
from sync_safe import SyncWindow, atomic_write_text  # noqa: E402
from conflict_triage import find_conflicts, project_folders  # noqa: E402
from conflict_triage import triage as triage_conflicts  # noqa: E402
from thought_end_live import (DEFAULT_THRESHOLD as THOUGHT_END_THRESHOLD,  # noqa: E402
                              FINISH_TIMEOUT_S as THOUGHT_END_FINISH_TIMEOUT_S,
                              ThoughtEndLane, default_laya_argv)
from worker import WorkerClient  # noqa: E402
import call_lane as call_lane_mod  # noqa: E402
import stage_image as stage_image_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph.graph_build import GraphSource            # noqa: E402
from graph.seed_note import find_or_create_seed      # noqa: E402
from graph.vault_notes import DEFAULT_EXCLUDE        # noqa: E402
from gateway.actions_reader import ActionsReader, dispatcher_for  # noqa: E402
from blocks.knowledge_link import KnowledgeLinkBlock  # noqa: E402
from gateway.block_host_wiring import (build_session_blocks,  # noqa: E402
                                       coach_block_config, dispatch_call_window_state,
                                       feed_utterance, make_agent_for,
                                       wire_call_window, wire_meeting_chat)
from shared.block_registry import load_from  # noqa: E402
from shared.session_runtime import SessionRuntime  # noqa: E402
from gateway.raw_transcript import MinutesFile  # noqa: E402
from blocks.minutes_document import MinutesDocumentBlock  # noqa: E402
from blocks.minutes_document import minutes as minutes_block  # noqa: E402
from gateway.stage_surface import StageSurface       # noqa: E402
from shared.stt import SttConfig, open_stt           # noqa: E402
from shared.modules import StateStore                # noqa: E402

ENTITIES = Path(os.environ.get(
    "COTHINKER_ENTITIES", Path.home() / "vault/entities"))

# The most spoken text that gets bundled into a SINGLE agent run when a
# backlog has formed. Rationale: docs/review-report.md.
MAX_BATCH_CHARS = 12000
# The most text that gets carried into the next run when an agent run has
# failed.
MAX_CARRY_CHARS = 6000

HEADER = """---
title: "{title}"
entity: {entity}
status: "laufend (Live-Mitschrift)"
date: {day}
teilnehmende: [{participants}]
tags: [protokoll, meeting, live-mitschrift, {pslug}]
contributors: [robo]
---

# {title}

⚠️ **Automatische Transkription.** Eigennamen und Zahlen sind unbestaetigt —
im Zweifel nachfragen, nicht uebernehmen.
**Start:** {start}

## Entscheidungen

## Aufgaben

## Offene Fragen

## Verlauf

## Rohtranskript
"""

# Protocol format v2 (docs/entwurf-meeting-modi.md §1-2): topic blocks with
# tagged entries under `## Themen`, and a derived view (`## Überblick`)
# that only the consumer writes, re-rendered on every entry. New protocols
# are created in this shape; a file of the same day that is not in this
# shape (the old format, where the agent kept `## Verlauf` and friends) is
# continued in it below a dated marker line, see `continue_in_format_2`.
# The old text is never rewritten. The view section is
# exactly `render_view([], "sitzung")`; other modes get theirs spliced in
# by `protocol_header()`.
HEADER_V2 = """---
title: "{title}"
entity: {entity}
status: "laufend (Live-Mitschrift)"
date: {day}
teilnehmende: [{participants}]
tags: [protokoll, meeting, live-mitschrift, {pslug}]
contributors: [robo]
protokoll_format: 2
modus: {mode}
---

# {title}

⚠️ **Automatische Transkription.** Eigennamen und Zahlen sind unbestaetigt —
im Zweifel nachfragen, nicht uebernehmen.
**Start:** {start}

## Überblick

<!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem Eintrag neu. -->

### Beschlüsse

*noch keine*

### Aufgaben

*noch keine*

### Offene Fragen

*noch keine*

## Themen

## Rohtranskript
"""


# The agenda of a new protocol (`--agenda`): at most this many items. A
# right column whose first section scrolls is no longer an overview.
MAX_AGENDA_ITEMS = 12


def read_agenda(path: str) -> list[str]:
    """Agenda items from a text file: one per line.

    Blank lines and `#` comments are skipped, a leading `1.` / `2)` is
    dropped (the section numbers the items itself), every item is cut like
    a topic title. Raises `OSError` for a file that cannot be read -- the
    caller turns that into a start-up error, before anything is written.
    """
    if not path:
        return []
    items: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = clean_line(re.sub(r"^\d+[.)]\s*", "", line), 120)
        if line:
            items.append(line)
    return items[:MAX_AGENDA_ITEMS]


def project_agenda_text(title: str, participants: str, agenda: list[str],
                        enrolled_names: list[str] | None = None) -> str:
    """The STABLE prefix text for each lane's prompt (point 7, 2026-10-20):
    project, participants and agenda, same cache segment as profile/tools.

    `enrolled_names` replaces the bare `--participants` list with the
    speaker-enrollment subsystem's resolved names once the intro round
    ends -- the one deliberate, accepted change this prefix may make
    mid-meeting (gateway.call_lane.CallLane.update_stable_prefix()).
    """
    names = (", ".join(enrolled_names) if enrolled_names else participants)
    lines = [f"Projekt: {title}"]
    if names.strip():
        lines.append(f"Teilnehmende: {names.strip()}")
    if agenda:
        lines.extend(f"- {item}" for item in agenda)
    return "\n".join(lines)


def agenda_section(items: Iterable[str]) -> str:
    """`## Ablauf` with one `- N. item` line per agenda item, or "".

    A bullet, not an ordered list: the stage's small Markdown converter
    only knows `-` lists, and the number stays readable either way.
    """
    cleaned = [c for c in (clean_line(i, 120) for i in items) if c]
    if not cleaned:
        return ""
    lines = [f"- {n}. {item}" for n, item in enumerate(cleaned, 1)]
    return "## Ablauf\n\n" + "\n".join(lines) + "\n\n"


def protocol_header(*, title: str, entity: str, day: str, pslug: str,
                    participants: str, start: str,
                    mode: str = DEFAULT_MODE,
                    agenda: Iterable[str] = ()) -> str:
    """The text of a freshly created v2 protocol.

    `HEADER_V2` carries the `sitzung` view verbatim; for any other mode
    the empty view of THAT mode is spliced in, so the file shows the right
    sections from the first second instead of after the first entry.

    An agenda goes ABOVE `## Überblick`: `splice()` passes everything
    before that heading through untouched, so the section is written once
    and never rewritten, and the stage renders it as the first thing in
    the right column.
    """
    text = HEADER_V2.format(title=title, entity=entity, day=day, pslug=pslug,
                            participants=participants, start=start, mode=mode)
    section = agenda_section(agenda)
    if section:
        text = text.replace("\n## Überblick\n",
                            "\n" + section + "## Überblick\n", 1)
    return splice(text, view=render_view([], mode), append="")


# C2 measure 1 (docs/superpowers/plans/2026-09-26-topic-prompt-c2.md, D4):
# the criterion behind the `topic` bullet above -- WHEN a topic change is
# a topic change. Default-OFF, spliced in by build_prompt(...,
# topic_criterion=True) right after TOPIC_CRITERION_ANCHOR, which must
# occur exactly once in AGENT_PROMPT. Only ever inserted on the first
# turn: it lives in the worker session's history afterwards (B5), the
# same as the rest of AGENT_PROMPT. The three negative examples are
# invented -- never a real quote (DATA RULE).
TOPIC_CRITERION_ANCHOR = "     schick denselben Wechsel nicht noch einmal.\n"
TOPIC_CRITERION = """     WORAN du einen Wechsel erkennst — mindestens eins davon trifft zu:
       (a) ein neuer Gegenstand, den die letzten Abschnitte nicht hatten;
       (b) die Runde kehrt zu einem aelteren Thema zurueck;
       (c) ein ausdruecklicher Marker: "kommen wir zu", "andere Frage",
           "noch was anderes".
     KEIN Wechsel ist: eine Vertiefung, ein Beispiel oder ein Einwand zum
     selben Thema. Erfundene Beispiele fuer KEINEN Wechsel: "Nehmen wir mal
     den Fall mit dem Gartenzaun" (ein Beispiel), "Da muss ich widersprechen,
     die Frist ist zu knapp" (ein Einwand), "Und wie genau laeuft die
     Abrechnung dabei?" (eine Vertiefung).
"""

BACKLOG_NOTE = """
⏩ RUECKSTAND: Dieser Abschnitt fasst mehrere Sprechabschnitte zusammen — du
bist hinterher, die Runde ist weiter. Arbeite grob und schnell: EIN Stichpunkt
je erkennbarem Thema, und richte `mirror`, `headline` und `main` am ZULETZT
Gesprochenen aus, nicht am Anfang. Lieber knapp und aktuell als vollstaendig
und veraltet.
"""

# A minutes file of the current day that is not in the current format (the
# old one: the agent kept `## Verlauf`, `## Entscheidungen`, ... itself) is
# continued, not migrated: its text stays byte for byte, and what follows is
# the current format below this marker line. `{stamp}` is "YYYY-MM-DD HH:MM".
FORMAT_2_MARKER = "<!-- format 2 from {stamp} -->"


def continue_in_format_2(text: str, stamp: str, mode: str = DEFAULT_MODE) -> str:
    """`text` with the current format continued below a dated marker line.

    A text that already is in the current format comes back unchanged. For
    any other text everything in front of its `## Rohtranskript` (or the
    whole text, when it has none) stays as it is; the marker line, an empty
    `## Überblick` view of `mode` and an empty `## Themen` follow it, then
    the raw section, byte for byte. Only additions: nothing already in the
    file is rewritten.
    """
    if is_v2_text(text):
        return text
    raw_at = _ROH_LINE_RE.search(text)
    head, raw = ((text[:raw_at.start()], text[raw_at.start():]) if raw_at
                 else (text, "## Rohtranskript\n"))
    if head and not head.endswith("\n\n"):
        head += "\n" if head.endswith("\n") else "\n\n"
    new = (head + FORMAT_2_MARKER.format(stamp=stamp)
           + "\n\n## Überblick\n\n## Themen\n\n" + raw)
    return splice(new, view=render_view([], mode), append="")


def continue_protocol(path: Path, now: datetime, mode: str = DEFAULT_MODE) -> bool:
    """Continue the minutes file at `path` in the current format when it is
    not in it (`continue_in_format_2`). True when the file was changed.
    Raises `OSError` when it cannot be read or written -- the caller decides."""
    with open(path, encoding="utf-8", newline="", errors="surrogateescape") as f:
        text = f.read()
    new = continue_in_format_2(text, f"{now:%Y-%m-%d %H:%M}", mode)
    if new == text:
        return False
    atomic_write_text(path, new)
    return True


# The main turn (issue #49, ADR 0005): the agent RETURNS its content as one
# fenced JSON reply, it edits no file. The session host owns every file:
# the `capture-bullets` block parses the reply (`agent_reply` event), the
# host files the minutes entries into `protokoll.md` (through the
# minutes-document block) and appends the stage proposals to the proposal
# file, from which it alone decides what reaches `buehne.json`.
#
# DESIGN DECISION -- the reply's shape. Minutes sections are lists of
# one-line items (a string, or an object with `text` and an optional
# `topic_id`); `buehne` items are the proposal lines of the stage actions
# (`StageState.apply()`), unchanged, so the stage's checks, caps and brakes
# stay where they were. A `tag_entry` action stays possible INSIDE `buehne`
# for one reason only: an order of the level-2 policy asks for it and
# carries its order id.
#
# `decision` doubles as `beschluss`: an accepted decision is shown on the
# stage AND filed by the consumer as a `beschluss` entry. The minutes
# document refuses a `beschluss` whose text it already holds, but only on
# the same words. So the prompt gives every decision ONE channel, the
# `entscheidungen` item, and lets the `decision` action carry the very same
# text where the stage should show it too: the duplicate check then holds
# by construction.
#
# `topic` stays the one trigger for a new topic block, and its `title`
# becomes the block's heading. The host applies the stage proposals BEFORE
# it files the minutes items, so items of the same reply land in the new
# block.
#
# ASCII transliteration like every prompt here; only the literal headings
# are spelled as they stand in the file (`## Überblick`).
AGENT_PROMPT = """Du bist Robo, der Mitdenker in einem laufenden Termin. Die
Runde liest deine Ausgabe live mit, waehrend sie weiterspricht.

PROTOKOLL:  {path}
PROJEKTORDNER: {folder}
{situation}{open_state}
Neu gesprochen seit deiner letzten Runde:
---
{block}
---

HINTERGRUND: Im Projektordner liegen die Unterlagen zu diesem Termin —
Vorbereitung, Leitfaden, frueheres Protokoll, Notizen. Brauchst du fuer eine
Frage aus der Runde den Sachstand (ein Name, eine Zahl, was frueher schon
beschlossen wurde), lies dort gezielt die passende Datei. Sonst nicht: die
Runde wartet auf dich, und ein Zug dauert ohnehin 40-180 Sekunden. Erst lesen,
wenn der Abschnitt es verlangt, und dann nur das eine Dokument — nicht den
Ordner durchforsten. Der Termin hat Vorrang vor der Vollstaendigkeit.

Du liest dort NUR — ausser ein Auftrag der Runde verlangt ausdruecklich, eine
Datei dort anzulegen oder zu aendern (nie das Protokoll). Alles andere im
Ordner gehoert dem Konsumenten und dem Sync-Dienst, und ein zweiter Schreiber
erzeugt Konfliktkopien.

DEINE ANTWORT IST DER INHALT. Du schreibst weder das Protokoll noch die Buehne
noch eine Vorschlagsdatei. Du antwortest mit GENAU EINEM JSON-Objekt in einem
Codeblock (```json ... ```), sonst mit nichts. Der Konsument liest es, traegt
die Eintraege ins Protokoll ein, vergibt die Kennungen und entscheidet, was auf
die Buehne kommt. Oben im Protokoll steht die Sicht `## Überblick` (Beschluesse,
Aufgaben, Offene Fragen), darunter `## Themen` mit einem Block je Thema
(`### HH:MM Titel`) und den Eintraegen darin (`- HH:MM [tag] Text`) — du liest
es NUR, und nur was OBERHALB von `## Rohtranskript` steht. Das Rohtranskript
brauchst du nicht: es waechst den ganzen Termin.

{{"verlauf": [...], "entscheidungen": [...], "aufgaben": [...],
 "offene_fragen": [...], "buehne": [...]}}

1. PROTOKOLL: `verlauf`, `entscheidungen`, `aufgaben` und `offene_fragen` sind
   Listen. Ein Eintrag ist EINE Zeile: ein Text oder ein Objekt
   {{"text": "<eine Zeile>", "topic_id": "<optional>"}}. Fasse das Neue in
   hoechstens 1-3 Eintraegen je Liste. Eine Liste ohne Neues bleibt leer oder
   fehlt.
   - `verlauf`: ein Stand, eine Zahl, eine Erkenntnis, die festgehalten
     gehoert (landet als `notiz`).
   - `entscheidungen`: was entschieden oder zugesagt wurde (`beschluss`).
   - `aufgaben`: wer macht was bis wann (`aufgabe`).
   - `offene_fragen`: was offen ist und von der Runde noch geklaert werden
     muss (`frage`).

   Nur eintragen, was wirklich gefallen ist. Nichts erfinden. Zahlen,
   Betraege, Fristen, Namen und Zusagen woertlich; Unsicheres aus der
   Spracherkennung mit "(unbestaetigt)". Eintraege werden nur angehaengt:
   Erledigtes schreibst du nicht um, sondern meldest es als neuen Eintrag in
   `verlauf`, der mit **erledigt** beginnt und die geaenderte Datei als
   [[dateiname]] nennt. Du loeschst nichts.

   VERLINKEN: Nennt ein Eintrag einen Baustein, eine Zahl oder eine Aufgabe,
   zu der es unten eine Datei gibt, verlinke sie als [[dateiname]] (ohne
   Endung) — nur Namen aus der Liste oder die Datei, die du gerade auf
   Zuruf angelegt oder geaendert hast. Jeden anderen Link entfernt der
   Konsument wieder.

   DATEIEN IM PROJEKTORDNER:
{files}

2. BUEHNE: `buehne` ist eine Liste von Objekten, hoechstens eines je Zeile
   dieser Art:

   {{"action":"mirror","text":"<EINE Zeile, dritte Person: was Robo verstanden
        hat und woran er gerade arbeitet>"}}
   {{"action":"headline","text":"<worueber gerade gesprochen wird, 3-6 Woerter>"}}
   {{"action":"decision","time":"HH:MM","text":"<kurz: was beschlossen oder
        zugesagt wurde>"}}
   {{"action":"notice","kind":"value_conflict","text":"<**Bezeichnung** Wert A
        gegen Wert B — welcher gilt?>"}}
   {{"action":"notice","kind":"decision_breach","text":"<**Beschluss** ... steht
        im Ueberblick unter Beschluesse, fehlt hier>"}}
   {{"action":"question","text":"<laut in drei Worten beantwortbar>"}}
   {{"action":"resolve","id":"<Kennung>","text":"<wie es geklaert wurde>"}}
   {{"action":"main","quote":"<der Auftrag WOERTLICH, so wie er gefallen ist>",
        "markdown":"<was gezeigt werden soll>"}}
   {{"action":"topic","title":"<das neue Thema, EINE Zeile>",
        "markdown":"<was dazu gezeigt werden soll>"}}
   {{"action":"graph","title":"<das Thema, EINE Zeile>",
        "seed":"<optional: Titel der Notiz, die der Saatknoten ist>"}}
   {{"action":"links","items":[{{"file":"<dateiname-ohne-endung, nur Dateien,
        die es wirklich gibt>","label":"<kurz>"}}]}}
   {{"action":"none"}} — "dieser Abschnitt bringt nichts Neues"; dann sind
        auch alle anderen Listen leer.

   Was wohin gehoert:
   - `decision` NUR fuer Entscheidungen und Zusagen. Nicht fuer Themen, nicht
     fuer Beobachtungen, nicht fuer Ideen, die gerade erst entstehen. Jeden
     Beschluss meldest du genau EINMAL, als Eintrag unter `entscheidungen`.
     Soll er auch auf die Buehne, traegt die `decision`-Zeile WORTGLEICH
     denselben Text; der Konsument legt ihn dann nur einmal ab. Eine andere
     Formulierung waere ein zweiter Eintrag im Protokoll.
   - `notice` NUR fuer die zwei Faelle oben. Es gibt keinen dritten Grund.
     Zwei Meldungen stehen hoechstens gleichzeitig; alles weitere unter
     `offene_fragen`.
   - `main` NUR, wenn die Runde dich darum gebeten hat — dann ist `quote`
     Pflicht: der Auftrag woertlich aus dem Abschnitt. Ohne `quote` wird die
     Zeile verworfen und die Flaeche bleibt, wie sie ist. Ohne Auftrag nimmst
     du nie `main` — dafuer gibt es `topic`. Sie zeigt das Ergebnis, um das gebeten
     wurde — hoechstens 15 Zeilen, kein Archiv und kein Volltext; was
     darueber steht, wird eingeklappt.
   - `topic`, wenn die Runde erkennbar zu einem ANDEREN Thema uebergegangen
     ist. Dafuer brauchst du keinen Auftrag und kein Zitat: eine Flaeche, die
     beim alten Thema haengt, behauptet etwas Falsches und stoert. Kurz halten,
     `title` ist eine Zeile: ein angenommenes `topic` eroeffnet im Protokoll
     den neuen Themenblock, und `title` ist seine Ueberschrift. Einen
     Themenblock gibt es nur so. Die Eintraege derselben Antwort landen schon
     im neuen Block. Wechselt nur das Thema: `topic`. Wurde dir etwas
     zugerufen: `main`. Zu dichte Wechsel lehnt der Konsument ab — das ist
     kein Fehler, sondern der Takt; die Eintraege landen dann im bisherigen
     Block. Dazu:
     schick denselben Wechsel nicht noch einmal.
   - `graph`, wenn ein NEUES Thema eroeffnet wird und das Netz der Notizen
     dazu helfen kann. Du nennst nur das Thema — der Konsument sucht die
     passende Notiz oder legt sie an und baut das Bild. Du schickst keine
     Knoten und keine Pfade mit. Findet er den Namen mehrfach, lehnt er ab;
     dann nimm einen genaueren Titel. Zu dichte Wechsel lehnt er ebenfalls
     ab — das ist der Takt, kein Fehler. `graph` teilt sich diesen Takt mit
     `topic` und ersetzt `topic` nie: Wechselt das Thema, schick ZUERST
     `topic`. Ein `graph` davor liesse das `topic` als zu frueh ablehnen,
     und im Protokoll entstuende kein neuer Themenblock.
   - Bittet die Runde, etwas FRUEHERES nochmal zu sehen ("nochmal das davor",
     "zeig wieder die Kosten"), ist das ein Auftrag wie jeder andere: `main`
     mit dem Zuruf woertlich in `quote` und dem frueheren Stand in `markdown`.
     Die Buehne blaettert nicht fuer dich — im Verlauf blaettert die Runde
     selbst, an der Buehne.
   - `resolve`, sobald die Runde eine Meldung oder Frage beantwortet hat.
     Steht oben etwas Offenes, pruefe zuerst, ob dieser Abschnitt es klaert.
   - Verlangt ein Auftrag oben ausdruecklich eine `tag_entry`-Aktion, steht
     sie samt Kennung des Auftrags in `buehne`.

DREI REGELN AUS EINEM ECHTEN TERMIN — sie gelten fuer JEDE Datei:

ANBAUEN STATT UMBAUEN. Die schlimmste Stoerung des ausgewerteten Termins war
keine falsche Aussage, sondern eine Umsortierung, waehrend die Runde mitlas
("Es wurde umsortiert. Also wir muessen jetzt ein bisschen mitdenken."). Du
fuegst hinzu — im Protokoll tut das ohnehin der Konsument fuer dich.
In einer Datei, die du auf Zuruf aenderst, gilt dasselbe: Sortieren,
streichen oder Vorhandenes umformulieren nur, wenn genau das zugerufen wurde
— und dann mit einer Zeile darueber, was sich bewegt hat.

NICHTS NEUES = NICHTS EINTRAGEN. Bringt der Abschnitt keine Erkenntnis
(Tonprobe, Technikgerede, Small Talk), antwortest du mit
{{"buehne": [{{"action":"none"}}]}} und sonst nichts. Ein Lauf ohne Eintrag
ist ein guter Lauf.

FRAGEN NUR, WENN RATEN SCHADET: zu bestaetigende Entscheidung, unklarer
Eigenname. Eine widersprechende Zahl ist keine Frage, sondern eine `notice`
der Art `value_conflict`. Laut in drei Worten beantwortbar ("5. oder
6. Oktober?"). Hoechstens EINE; jede weitere unter `offene_fragen`.

AUCH OHNE WECKWORT KANN DER ABSCHNITT EIN AUFTRAG SEIN. Die Spracherkennung
verstuemmelt das Weckwort ("Oboe", "Opa", "Robert") — im ausgewerteten Termin
in drei von fuenf Faellen. Steht im Abschnitt eine Anrede an dich ODER ein
Imperativ in der zweiten Person (mach, zeig, schreib, erstelle, berechne,
sortier, ergaenze, recherchier, schick, streich, update), dann fuehre ihn aus,
statt ihn nur zu protokollieren. Kannst oder darfst du ihn nicht ausfuehren
(Chat und Telegram sind ausgeschlossen), sagst du das in `mirror` und unter
`aufgaben`. Ein Auftrag, den du still als "offen" ablegst, ist verloren.

GEGENPRUEFEN, bevor du fertig bist:
   - Widerspricht eine Zahl, ein Datum oder eine Frist aus diesem Abschnitt
     einer frueheren im Protokoll? Dann als `notice` der Art `value_conflict`,
     mit beiden Werten.
   - Habe ich nur angebaut und nichts umgestellt?
   - Ist meine Antwort EIN gueltiges JSON-Objekt, ohne Text davor oder
     danach? Eine Antwort, die der Konsument nicht lesen kann, zaehlt als
     nicht geliefert.

Schreibe NICHT in den Chat und schicke KEINE Telegram-Nachricht — deine Antwort
ist die Ausgabe. Lege keine Dokumente an, um die nicht gebeten wurde: im
ausgewerteten Termin entstanden 28 Themenblaetter, drei trugen den Termin.
"""

# The order note of a wake-word turn: the triad and the quote rule, with the
# result routed into the reply's lists (`run_turn` adds it to the situation).
ORDER_NOTE = """
🔴 DIREKTER AUFTRAG: Der Abschnitt enthaelt eine Ansprache mit dem Weckwort.
Fuehre ihn AUS, bevor du Eintraege lieferst — auch wenn dafuer eine andere
Datei im Projektordner geaendert werden muss (nie das Protokoll: das fuehrt
der Konsument). Danach der Dreisatz, sichtbar im Ergebnis:
  1. AUFTRAG — zitiere ihn woertlich. Ist jede darin genannte Groesse
     abgebildet? Einzeln abhaken.
  2. EINHEIT — in welcher Einheit war das Ergebnis gefordert (Aufwand,
     Personenmonate, Euro, Stueck)? Steht es in genau dieser? Sagt der Auftrag
     "Aufwand", ist Euro falsch, auch wenn Euro nuetzlicher wirkt.
  3. BESCHLUSSLAGE — welche Beschluesse (im Protokoll oben im Ueberblick unter
     Beschluesse) beruehren das Ergebnis, und wo sind sie eingeflossen?
Halte Ergebnis und Pfad auf der Buehne (`main` — mit dem Auftrag woertlich in
`quote`) und in der Antwort fest: ein Eintrag unter `verlauf`, beginnend mit
**erledigt**, mit der geaenderten Datei als [[dateiname]] — oder unter
`aufgaben`, wenn noch etwas offen bleibt. Ein Ergebnis ohne diesen Dreisatz
gilt als nicht geliefert.

🔴🔴 `quote` IST EIN KOPIERVORGANG, KEINE FORMULIERUNGSAUFGABE.
Der Wert von `quote` wird maschinell im Transkript gesucht (Zeichen fuer
Zeichen, nur Gross/Kleinschreibung und Zeilenumbrueche sind egal). Findet die
Pruefung ihn nicht WOERTLICH, wird dein ganzer Vorschlag verworfen — die
Buehne bleibt leer, und niemand sagt dir warum.

Deshalb, ohne Ausnahme:
  • Nimm eine Zeichenkette, die GENAU SO im Abschnitt oben steht. Kopiere sie.
  • Fuelllaute, Versprecher, Wiederholungen, abgebrochene Saetze BLEIBEN DRIN.
    "ähm", "äh", "also" gehoeren dazu, wenn sie dastehen.
  • Kuerze NICHT, glaette NICHT, korrigiere NICHT, fasse NICHT zusammen.
  • Ein Teilsatz reicht voellig — lieber sechs woertliche Woerter als ein
    schoener Satz, den niemand so gesagt hat.
  • Im Zweifel: die kuerzeste eindeutige Passage kopieren.

FALSCH: "Wir wollten als Naechstes den Textentwurf ansehen"  (geglaettet)
RICHTIG: "uns den Textentwurf"                                (wortwoertlich)
"""

# How many project file names the v2 prompt lists at most. The link check
# itself runs against the FULL set (`StageWriter.link_names`).
PROMPT_FILE_LIMIT = 60

# B5: nur der Teil von AGENT_PROMPT, der sich von Runde zu Runde aendert
# (siehe build_prompt). Woertlich dieselben drei Zeilen wie im Volltext
# oben -- PROTOKOLL/VORSCHLAEGE/PROJEKTORDNER und die Anweisung selbst
# stehen nach der ersten Runde bereits im Verlauf, den der Worker (ein
# Prozess, eine Sitzung, gateway/worker.py) bei jedem Zug mitschickt.
#
# `{thread}` is C2 measure 2 (D5): empty by default, so the rendering is
# byte-identical to f1bf253/06bdb64 whenever `build_prompt(..., thread=None)`
# -- see build_prompt(). Filled in only on a delta turn (never the first
# turn) when a caller passes an explicit `thread` list.
DELTA_PROMPT = """{situation}{open_state}{thread}
Neu gesprochen seit deiner letzten Runde:
---
{block}
---
"""

# C2 measure 2 (docs/superpowers/plans/2026-09-26-topic-prompt-c2.md, D5):
# exactly the last THREAD_BULLETS Verlauf bullets go into the delta turn's
# thread block -- enough to see one or two earlier topics, small enough to
# stay cheap (card's range: 3-5).
THREAD_BULLETS = 4


def recent_verlauf(protocol_text: str, n: int = THREAD_BULLETS) -> list:
    """The last `n` bullet lines of the protocol's `## Verlauf` section.

    A bullet is a line starting with "- " inside `## Verlauf`, up to the
    next `## ` heading -- `## Rohtranskript` or any later section ends it,
    the same boundary the agent itself is told to respect (WAS DU LIEST in
    AGENT_PROMPT). Lines are returned with the "- " prefix kept, stripped
    of trailing whitespace; a line inside the section that is not a bullet
    is ignored. No `## Verlauf` section at all -> `[]`.
    """
    bullets = []
    in_verlauf = False
    for line in protocol_text.splitlines():
        if line.startswith("## "):
            in_verlauf = line.rstrip() == "## Verlauf"
            continue
        if in_verlauf and line.startswith("- "):
            bullets.append(line.rstrip())
    return bullets[-n:] if n else []


def log(*a):
    # Newline included, in ONE write call: `print` writes text and the
    # newline separately, and then the lines from the four threads
    # interleave — the operations log becomes unreadable in a real incident.
    sys.stdout.write(f"[{datetime.now():%H:%M:%S}] "
                     + " ".join(str(x) for x in a) + "\n")
    sys.stdout.flush()


def resolve_project(project: str) -> tuple[Path, str, str]:
    # COTHINKER_PROJECT_DIR (operator's point 11, 2026-10-20): a direct
    # folder override for a project that is not under $COTHINKER_ENTITIES
    # at all -- tonight's test target is a plain git clone
    # (a third-party Codeberg repo), not a vault entity. Smallest
    # change: an env var short-circuits the lookup below entirely; the
    # existing $ENTITIES/*/projekte/<slug> lookup is untouched for
    # everyone who does not set it.
    override = os.environ.get("COTHINKER_PROJECT_DIR", "").strip()
    if override:
        cand = Path(override).expanduser()
        if not cand.is_dir():
            raise SystemExit(f"COTHINKER_PROJECT_DIR not a directory: {cand}")
        return cand, cand.parent.name, cand.name
    p = project.strip("/")
    cand = ENTITIES / p
    if cand.is_dir():
        return cand, p.split("/")[0], p.split("/")[-1]
    hits = [d for d in ENTITIES.glob("*/projekte/*") if d.is_dir() and d.name == p]
    if len(hits) == 1:
        return hits[0], hits[0].parts[-3], hits[0].name
    raise SystemExit(f"project not unique: {project!r} ({len(hits)} matches)")


def project_commit_scope(folder: Path, run_dir: Path) -> list[str]:
    """Git pathspecs committed with the protocol when the project is its own
    repo (COTHINKER_PROJECT_DIR): the whole project folder, so files the
    call lane wrote on request land in the commit, and the whole session
    folder including its machine files (buehne.json, timing.jsonl,
    stage-events.jsonl, decisions-*.jsonl, vorschlag.jsonl,
    transkript-korrigiert.md). Operator, 2026-10-20: files that agents or a
    later improvement round need are committed; "only for machines" is no
    reason to leave them out. None of them carries the stage token (the
    token lives only in the stage process). Empty without the override
    (vault case unchanged)."""
    if not os.environ.get("COTHINKER_PROJECT_DIR", "").strip():
        return []
    return [str(folder)]


# ======================================================== session folder
SESSIONS_DIR = "sessions"
# The German spelling, accepted beside the English one. Both exist in the
# real vault: `cothinker/sessions/` and
# `another-project/sitzungen/`. The stage reads either; the
# consumer writes into whichever the project already has.
SESSIONS_DIR_ALT = "sitzungen"


def configured_sessions_dir() -> str:
    """`COTHINKER_SESSIONS_DIR`, or `SESSIONS_DIR` when unset.

    A partner repo may already file its meetings under a folder name that
    is neither `sessions/` nor `sitzungen/` (e.g. `protokolle/`). Read at
    call time, like `COTHINKER_PROJECT_DIR` in `resolve_project()`, so a
    test can set the variable and call straight into `session_paths()`
    without reloading the module.
    """
    return os.environ.get("COTHINKER_SESSIONS_DIR", "").strip() or SESSIONS_DIR
SESSION_FILES = {
    "protocol": "protokoll.md",
    "proposals": "vorschlag.jsonl",
    "events": "stage-events.jsonl",
    "timing": "timing.jsonl",
    # Display only, and only with --correct: the smoothed transcript and
    # its numbers-only trace. The stage lists every *.md of the project
    # folder, so the first of the two is readable on the surface without
    # a single change to stage/stage.py.
    "correction": "transkript-korrigiert.md",
    "correction_trace": "korrektur.jsonl",
    # Only with --thought-end combo: the parallel lane's numbers-only
    # trace, one row per utterance (gateway/thought_end_live.py).
    "thought_end": "thought-end.jsonl",
    # Only with --speakers: enrolled name -> embedding store
    # (stt/pipeline/speaker_labels/enroll.py), survives a consumer restart. Never
    # read by the stage, never logged -- biometric-ish data stays inside
    # the session folder like the raw transcript does.
    "speakers": "speakers.json",
    # The built-in agenda + voice-command element (AgendaController
    # below): a checkbox list the round ticks by speaking. Item and
    # speaker position live in the timer / speaker-queue block state, not
    # in a file of its own. New, no legacy layout, like timing.jsonl -- no
    # `-{day}` suffix.
    "agenda": "agenda.md",
}
# Long enough to still read as the meeting's name in a folder listing,
# short enough that `<date>-<slug>` stays a sane path component.
MAX_SLUG_CHARS = 48

# The German vowels first, then the general fallback. `unicodedata`'s
# decomposition turns "ü" into "u" -- which is wrong for a German title:
# "Bühne" would become "buhne" instead of "buehne". So the four letters
# that actually occur get spelled out, everything else is decomposed.
UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
           "Ä": "ae", "Ö": "oe", "Ü": "ue"}


def slugify(text: str, fallback: str = "termin") -> str:
    """A title as a folder-safe slug: lowercase, umlauts spelled out.

    Used for the session folder `<YYYY-MM-DD>-<slug>`. A title comes from
    the command line and can carry anything -- quotes, slashes, emoji, a
    hundred characters. None of that may end up in a path, and an empty
    result must never produce a folder that is only the date and a dash.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    for zeichen, ersatz in UMLAUTS.items():
        text = text.replace(zeichen, ersatz)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > MAX_SLUG_CHARS:
        # Cut on a word boundary when there is one close enough, so the
        # slug doesn't end mid-word.
        text = text[:MAX_SLUG_CHARS].rstrip("-")
        if "-" in text[MAX_SLUG_CHARS // 2:]:
            text = text.rsplit("-", 1)[0]
    return text or fallback


def flat_run_exists(folder: Path, day: str, file: str | None = None) -> Path | None:
    """The flat protocol of TODAY in the project folder, or None.

    A meeting that already started wrote `protokoll-<day>.md` next to the
    project's other documents (the layout before the session folders).
    Putting a fresh folder beside it mid-meeting would tear the run in
    two: the consumer would start an empty protocol while the round is
    reading the old one, and the proposal offsets would restart as well.
    So an existing flat run of the same day keeps the old layout until it
    ends. An explicit `--file` is honoured the same way.
    """
    kandidat = folder / (file or f"protokoll-{day}.md")
    return kandidat if kandidat.is_file() else None


def session_paths(folder: Path, day: str, title: str | None,
                  file: str | None = None, fallback: str = "termin") -> dict:
    """Where this run's files go: protocol, proposals, event trace.

    A project folder collects meetings over months. Flat, the files of
    four meetings lie interleaved and only the date suffix ties them
    together; in `sessions/<YYYY-MM-DD>-<slug>/` one meeting is one
    folder, and the human-written README and summary have somewhere to
    live next to it.

    Backwards compatible in the one case that matters: a flat run of the
    same day that is already on disk is continued where it lies.
    """
    alt = flat_run_exists(folder, day, file)
    if alt is not None:
        return {"folder": folder,
                "protocol": alt,
                "proposals": folder / f"vorschlag-{day}.jsonl",
                "events": folder / f"stage-events-{day}.jsonl",
                # No `-{day}` suffix here, unlike the other flat-mode
                # files: timing.jsonl is a new, simpler trace with no
                # legacy layout to stay compatible with, and the
                # .gitignore entry is a literal name rather than a glob.
                "timing": folder / SESSION_FILES["timing"],
                # The corrected transcript carries the date like the
                # protocol does -- flat, it lies among the project's own
                # documents, and two meetings of different days must not
                # share one file. The trace does not: it is new, like
                # timing.jsonl, and has no legacy layout to stay
                # compatible with.
                "correction": folder / f"transkript-korrigiert-{day}.md",
                "correction_trace": folder / SESSION_FILES["correction_trace"],
                "speakers": folder / SESSION_FILES["speakers"],
                # The level-1 decider's answer vectors (blocks/decisions_tasks_questions/decider.py),
                # only written with COTHINKER_DECIDER set. Flat, it lies in
                # the project folder like timing.jsonl; dated, because the
                # project folder outlives the day.
                "decisions": folder / f"decisions-{day}.jsonl",
                "thought_end": folder / SESSION_FILES["thought_end"],
                "agenda": folder / SESSION_FILES["agenda"],
                "flat": True}
    # Which of the known names does this project already use? The stage
    # reads all of them, so the consumer must not blindly create the
    # canonical one beside an existing folder under another name:
    # `another-project` keeps its meetings in `sitzungen/`, and a
    # fresh `sessions/` next to it would split one round's material across
    # two folders -- the stage then shows one of them and the meeting
    # silently misses whatever landed in the other.
    #
    # An existing folder wins; only when none exists do we create one, in
    # `COTHINKER_SESSIONS_DIR` when a partner repo set it, else `sessions/`
    # as before. `sessions` and `sitzungen` stay checked either way --
    # extended, never replaced.
    configured = configured_sessions_dir()
    kandidaten_namen = [SESSIONS_DIR, SESSIONS_DIR_ALT]
    if configured not in kandidaten_namen:
        kandidaten_namen.append(configured)
    basis = folder / configured
    for name in kandidaten_namen:
        if (folder / name).is_dir():
            basis = folder / name
            break
    ordner = basis / f"{day}-{slugify(title or '', fallback)}"
    return {"folder": ordner,
            "protocol": ordner / (file or SESSION_FILES["protocol"]),
            "proposals": ordner / SESSION_FILES["proposals"],
            "events": ordner / SESSION_FILES["events"],
            "timing": ordner / SESSION_FILES["timing"],
            "correction": ordner / SESSION_FILES["correction"],
            "correction_trace": ordner / SESSION_FILES["correction_trace"],
            "speakers": ordner / SESSION_FILES["speakers"],
            "decisions": ordner / f"decisions-{day}.jsonl",
            "thought_end": ordner / SESSION_FILES["thought_end"],
            "agenda": ordner / SESSION_FILES["agenda"],
            "flat": False}


# A live contract surface (`surfaces.contract --live-pointer`, issue #161)
# has no way to know this run's session folder in advance -- only the
# consumer picks its name (date + title slug) once it opens it, and a
# sibling guessing the newest folder under sessions/ can pick an older or
# a prepared one instead and crash-loop on the missing stage state. When
# this names a file, the surface reads it (re-reading on change) to follow
# the session actually open instead of guessing.
SESSION_POINTER_ENV = "COTHINKER_SESSION_POINTER"


def write_session_pointer(run_dir: Path) -> None:
    """Tell a live contract surface which session is current now.

    A dedicated write, not folded into `SyncWindow.acquire()`: the sync
    window's marker and lock protect the vault sync against a half-written
    file (docs/feedback-2026-09-26.md point 9) -- a narrow, already
    documented concern that has nothing to do with naming the current
    session for a live view; giving that write a second, unrelated meaning
    would not make either concern easier to read.

    A no-op without `COTHINKER_SESSION_POINTER` set, so a deployment that
    never named a pointer file sees no behaviour change.
    """
    pointer = os.environ.get(SESSION_POINTER_ENV, "").strip()
    if not pointer:
        return
    atomic_write_text(Path(pointer), str(run_dir))


# ========================================================= agenda + timer
def _set_agenda_checkbox(path: Path, index: int, done: bool) -> None:
    """Flip the `index`-th checkbox (0-based, in file order) to `[x]`
    (`done`) or `[ ]` (not `done`).

    Read-modify-write on the whole file: an agenda has at most a handful
    of items, so this is not a hot path, and it keeps every other line
    (the `Dauer`/`Ende` header, blank lines) byte-identical.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    seen = -1
    for i, line in enumerate(lines):
        m = re.match(r"^-\s*\[([ xX])\]", line.lstrip())
        if not m:
            continue
        seen += 1
        if seen == index:
            lines[i] = re.sub(r"\[[ xX]\]", "[x]" if done else "[ ]", line,
                              count=1)
            break
    atomic_write_text(path, "".join(lines))


def tick_agenda_item(path: Path, index: int) -> None:
    """Marks the `index`-th checkbox (0-based, in file order) done."""
    _set_agenda_checkbox(path, index, True)


def untick_agenda_item(path: Path, index: int) -> None:
    """Marks the `index`-th checkbox (0-based, in file order) not done
    -- an item left behind by a "back"/"item N" command."""
    _set_agenda_checkbox(path, index, False)


class AgendaController:
    """Feeds the `timer` and `speaker-queue` blocks from `agenda.md` and
    reads their view intents back into the stage's `agenda` field.

    Owns `agenda.md` (read once at start-up, ticked on a "naechster
    Punkt"/"naechste Frage" command). Which item is current, since when,
    and which speaker is up live ONLY in the blocks' own state (`timer.json`,
    `round.json` under the session's `.blocks/`), restored by the blocks on
    start. The controller keeps the latest `timer:current` / `queue:room`
    payloads it was handed through `observe()` and nothing else.

    Wiring order (see `_main`): `block_configs()` goes into
    `build_session_blocks`, `observe` is part of its sink, then `attach()`
    hands the finished host back for the voice commands.
    """

    def __init__(self, agenda_path: Path, session_dir: Path, *,
                 people: list[str] = (),
                 now: Callable[[], float] = time.time,
                 log: Callable[..., None] = log) -> None:
        self.agenda_path = agenda_path
        self._now = now
        self.log = log
        self.people = list(people)
        self.blocks = None
        self.doc = parse_agenda(agenda_path.read_text(encoding="utf-8"))
        # The meeting's start is the timer block's persisted anchor, so a
        # restart keeps the same `Dauer` deadline instead of re-basing it.
        try:
            anchor = StateStore(Path(session_dir) / ".blocks" / "timer").read(
                "timer.json").get("anchor")
        except (OSError, ValueError, AttributeError) as e:
            self.log("timer state unreadable, starting a fresh anchor:", repr(e))
            anchor = None
        self.meeting_started_at = (anchor if isinstance(anchor, (int, float))
                                   else now())
        self.deadline = meeting_deadline(self.doc, self.meeting_started_at)
        self._timer: dict | None = None
        self._queue: dict | None = None

    def block_configs(self) -> dict:
        """The timer block's items (label + minutes) and deadline."""
        return {"timer": {
            "items": [{"label": i.text,
                       "minutes": (i.budget_s / 60 if i.budget_s is not None
                                   else None)}
                      for i in self.doc.items],
            "deadline": self.deadline,
        }}

    def observe(self, intent) -> bool:
        """Takes the view intents the agenda zone is built from; True when
        `intent` was one of them.

        Ticks/unticks `agenda.md` here, not in `handle_utterance`: a
        `timer:current` move can come from a voice command OR straight
        from a stage tap (`gateway/actions_reader.py` dispatches a tap
        to the block host directly, bypassing this controller's own
        `_dispatch`) -- this is the one place both paths go through.
        """
        if intent.id == "timer:current":
            before = self._timer.get("current") if self._timer else None
            self._timer = dict(intent.payload)
            after = self._timer.get("current")
            if (isinstance(before, int) and isinstance(after, int)
                    and before != after):
                try:
                    self._sync_ticks(before - 1, after - 1)
                except Exception as e:     # noqa: BLE001
                    self.log("agenda.md checkbox not updated:", repr(e))
            return True
        if intent.id == "queue:room":
            self._queue = dict(intent.payload)
            return True
        return False

    def _sync_ticks(self, before: int, after: int) -> None:
        """Ticks every item the current position passed moving forward
        (`before` .. `after`), or unticks every item it passed moving
        back (`after` .. `before`) -- a jump of more than one step ticks
        or unticks all of them, not just the endpoints."""
        n = len(self.doc.items)
        if after > before:
            for i in range(max(before, 0), min(after, n)):
                tick_agenda_item(self.agenda_path, i)
        elif after < before:
            for i in range(max(after, 0), min(before, n)):
                untick_agenda_item(self.agenda_path, i)

    def attach(self, blocks) -> None:
        """Starts the round-robin from the participants. Idempotent: the
        block keeps its persisted position for the same list."""
        self.blocks = blocks
        if self.people:
            self._dispatch("queue:room",
                           {"op": "round_start", "people": self.people})

    def _dispatch(self, intent_id: str, payload: dict) -> None:
        self.blocks.dispatch("action", {
            "intent_id": intent_id, "kind": "voice", "person": None,
            "payload": payload, "at": self._now()})

    def _current_index(self) -> int:
        return (self._timer["current"] - 1
                if self._timer and self._timer.get("current") else 0)

    def handle_utterance(self, text: str) -> bool:
        """True when `text` is a recognized command -- it must then never
        reach the block builder or the agent. NEVER RAISES: a command
        phrase must not turn into conversational text because dispatching
        it failed. Checkbox ticking happens in `observe()`, not here --
        see its docstring."""
        kind = detect_agenda_command(text)
        if kind is None:
            return False
        try:
            if kind == "person":
                self._dispatch("queue:room", {"op": "next_person"})
            elif kind == "item":
                self._dispatch("timer:current", {"op": "next"})
            elif kind == "back":
                self._dispatch("timer:current", {"op": "back"})
            else:   # ("jump", n)
                self._dispatch("timer:current",
                               {"op": "goto", "index": kind[1] - 1})
        except Exception as e:         # noqa: BLE001
            self.log("agenda command not applied:", repr(e))
        return True

    def payload(self) -> dict:
        """The stage's `agenda` field: the latest `timer:current` payload
        plus the round list from the latest `queue:room` payload and the
        whole-agenda list (issue #129: a surface shows every item, not
        just current/next -- lost otherwise when this branch's base
        predates that issue)."""
        out = dict(self._timer) if self._timer else {
            "current": 0, "current_text": "", "next": None, "next_text": None,
            "total": 0, "item_started_at": None, "item_budget_s": None,
            "meeting_deadline": self.deadline}
        rnd = (self._queue or {}).get("round") or {}
        out["people"] = list(rnd.get("people") or [])
        out["speaker"] = rnd.get("speaker")
        out["next_speaker"] = rnd.get("next_speaker")
        idx = self._current_index()
        out["items"] = ([{"title": item.text, "budget_s": item.budget_s,
                          "status": ("done" if i < idx else "current" if i == idx
                                     else "next" if i == idx + 1 else "open")}
                         for i, item in enumerate(self.doc.items)]
                        if self.doc.items else None)
        return out


def build_agenda_controller(session_dir: Path, participants: str,
                           *, log: Callable[..., None] = log,
                           now: Callable[[], float] = time.time
                           ) -> AgendaController | None:
    """None, and never raises, when `session_dir/agenda.md` is absent or
    unreadable -- the feature is simply off, exactly as before it existed."""
    agenda_path = session_dir / SESSION_FILES["agenda"]
    if not agenda_path.is_file():
        return None
    people = [p.strip() for p in participants.split(",") if p.strip()]
    try:
        return AgendaController(agenda_path, session_dir, people=people,
                                now=now, log=log)
    except (OSError, UnicodeDecodeError) as e:
        log("agenda off: agenda.md not readable:", repr(e))
        return None


# ======================================================= session files
def _flock(f) -> bool:
    """File lock across process boundaries, where the platform has one."""
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return True
    except Exception:
        return False


class SessionGit:
    """Commits (and syncs) the session's files with git.

    A concern of the session host, nothing to do with rendering the minutes: it
    commits the protocol file and whatever else the session wrote.
    """

    def __init__(self, path: Path, repo: Path,
                 commit_scope: Iterable[str] = ()):
        self.path = Path(path)
        self.repo = Path(repo)
        # Extra git pathspecs committed together with the protocol (e.g.
        # files the call lane wrote in a COTHINKER_PROJECT_DIR repo).
        # Empty = only the protocol, as before.
        self.commit_scope = [str(s) for s in commit_scope]
        self._git_faden: threading.Thread | None = None
        self._git_sperre = threading.Lock()
        self._fernstelle: bool | None = None

    def _git(self, *args: str, timeout: float = 180) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True,
            timeout=timeout, check=False,
            # English messages (parseable) and never a password prompt that
            # would hang the thread until the timeout.
            env=dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0"))

    def has_remote(self) -> bool:
        if self._fernstelle is None:
            try:
                self._fernstelle = bool((self._git("remote", timeout=20).stdout or "").strip())
            except Exception:
                self._fernstelle = False
        return self._fernstelle

    def _git_run(self, msg: str) -> bool:
        """Commit (and if there is a remote: sync).

        Errors are reported rather than swallowed: a silent push failure
        means everyone is reading a version that only exists on this
        machine. `--autostash` is needed because the agent writes to other
        files in the same vault meanwhile, and a `pull --rebase` would
        otherwise fail on the dirty working copy.
        """
        specs = [str(self.path), *self.commit_scope]
        schritte: list[list[str]] = [
            ["add", "-A", "--", *specs],
            ["commit", "-q", "-m", msg, "--", *specs],
        ]
        if self.has_remote():
            schritte += [["pull", "--rebase", "--autostash", "-q"], ["push", "-q"]]
        for args in schritte:
            try:
                r = self._git(*args)
            except subprocess.TimeoutExpired:
                log(f"  git {args[0]}: time limit exceeded")
                return False
            except OSError as e:
                log(f"  git {args[0]}: {e}")
                return False
            if r.returncode == 0:
                continue
            aus = ((r.stdout or "") + (r.stderr or "")).strip()
            if args[0] == "commit" and ("nothing to commit" in aus
                                        or "nothing added to commit" in aus
                                        or "no changes added" in aus
                                        or not aus):
                continue                  # nothing changed is not an error
            log(f"  git {args[0]} failed: {aus[-300:] or '(no output)'}")
            return False
        return True

    def git(self, msg: str) -> bool:
        """Kicks off the git run in the background.

        The SSE reader calls this and must not stall: a hanging `push`
        would otherwise block reading the stream for up to three minutes —
        and the STT server would fill up our buffer in the meantime.
        """
        with self._git_sperre:
            if self._git_faden is not None and self._git_faden.is_alive():
                return False              # already running; this commit is skipped
            self._git_faden = threading.Thread(
                target=self._git_run, args=(msg,), daemon=True, name="git")
            self._git_faden.start()
        return True

    def git_wait(self, timeout: float = 60) -> None:
        faden = self._git_faden
        if faden is not None:
            faden.join(timeout)


class MinutesPort:
    """The session host's adapter to the minutes-document block.

    The minutes document's face for the rest of the host: `add_entry`,
    `open_topic`, `current_topic*`, `is_v2`, `mode`. The block renders the sections; this
    class sends it events and carries its rendered head into the file through
    `MinutesFile.write_sections` -- the host stays the file's only writer.

    The block host dispatches synchronously on the caller's thread, so the
    block's `entry_result` answer and the `_tl.write_failed` flag set by
    `_on_output` belong to the thread that is inside `_send`. The port calls
    no method of the block: it reads the block's output events.

    Every call holds `file.lock` across the dispatch (the same span
    the old in-consumer minutes class held); `write_sections` re-enters it. The lock order
    stays file.lock -> StageWriter._lock (via report).
    """

    def __init__(self, file: MinutesFile, blocks: SessionRuntime, *,
                 session_dir: Path, mode: str = DEFAULT_MODE,
                 block_id: str = "minutes-document", log=log):
        self.file = file
        self.blocks = blocks
        self.session_dir = Path(session_dir)
        self.block_id = block_id
        self._log = log
        self._tl = threading.local()
        blocks.on_output(self._on_output)
        with file.lock:
            blocks.dispatch("block_output", {"minutes_open": {
                "path": str(file.path), "mode": mode}})

    # --------------------------------------------------------- properties
    @property
    def path(self) -> Path:
        return self.file.path

    @property
    def lock(self):
        return self.file.lock

    def _state(self) -> dict:
        """The block's `minutes_state` answer; {} when there is none."""
        with self.file.lock:
            got = self.blocks.collect("block_output", {"minutes_query": {}},
                                      self.block_id)
        for out in reversed(got):
            if out.get("kind") == "minutes_state":
                return out
        return {}

    @property
    def is_v2(self) -> bool:
        return bool(self._state().get("is_v2", False))

    @property
    def mode(self) -> str:
        return self._state().get("mode") or DEFAULT_MODE

    # -------------------------------------------------------------- calls
    def append_raw(self, text, speaker=None) -> bool:
        """Host-only convenience: the raw line, never through a block."""
        return self.file.append_raw(text, speaker)

    def current_topic(self) -> tuple[str, str]:
        """(key, title) of the last topic block; ("", "") if none. Never raises."""
        try:
            st = self._state()
            return st.get("topic_key", ""), st.get("topic_title", "")
        except Exception as e:            # noqa: BLE001 -- see the docstring
            self._log("protocol not read:", repr(e))
            return "", ""

    def current_topic_title(self) -> str:
        return self.current_topic()[1]

    def add_entry(self, tag, text, *, stamp=None,
                  names: Iterable[str] | None = None) -> "minutes_block.EntryResult":
        """Never raises; see `blocks.minutes_document.minutes.EntryResult`."""
        # `names` goes through untouched: the block's own never-raise guard
        # covers a stray value (not iterable, not a string) in it.
        return self._send("entry", {
            "tag": tag, "text": text, "stamp": stamp, "names": names})

    def open_topic(self, title, stamp=None) -> bool:
        return self._send("topic", {"title": title, "stamp": stamp}).reason == ""

    def _send(self, key: str, body: dict) -> "minutes_block.EntryResult":
        res = minutes_block.EntryResult("not written")
        with self.file.lock:
            self._tl.write_failed = False
            try:
                answers = self.blocks.collect(
                    "block_output", {key: body}, self.block_id)
                got = [o for o in answers if o.get("kind") == "entry_result"]
                if got:
                    o = got[-1]
                    res = minutes_block.EntryResult(
                        o.get("reason", ""), o.get("links_seen", 0),
                        o.get("links_dead", 0), attempted=o.get("attempted", True))
            except Exception as e:        # noqa: BLE001 -- never raise
                self._log("protocol not written:", repr(e))
            if res.reason == "" and getattr(self._tl, "write_failed", False):
                res = minutes_block.EntryResult(
                    "not written", res.links_seen, res.links_dead)
        ok = {"": True, "not written": False}.get(res.reason)
        if not getattr(res, "attempted", True):
            ok = None       # failed before any write: nothing to report
        if ok is not None:
            try:
                self.file.report(ok)
            except Exception as e:        # noqa: BLE001 -- never fail a write
                self._log("protocol report failed:", repr(e))
        return res

    def _on_output(self, payload) -> None:
        """Splice the block's rendered head into the file, then drop the artefact."""
        try:
            changed = payload.get("minutes_changed") if hasattr(payload, "get") else None
            if not changed:
                return
            art = (self.session_dir / "artefacts" / self.block_id
                   / str(changed.get("name", "protokoll.md")))
            try:
                with open(art, encoding="utf-8", newline="",
                          errors="surrogateescape") as f:
                    text = f.read()
                self.file.write_sections(text)
            except Exception as e:        # noqa: BLE001 -- see below
                self._log("protocol not written:", repr(e))
                self._tl.write_failed = True
                return
            try:
                art.unlink()
            except OSError:
                pass
        except Exception as e:            # noqa: BLE001 -- listeners never raise
            self._log("minutes output not handled:", repr(e))
            self._tl.write_failed = True


def open_minutes(path: Path, *, mode: str = DEFAULT_MODE,
                 report: Callable[[bool], None] | None = None,
                 session_dir: Path | None = None,
                 log=lambda *a: None) -> MinutesPort:
    """A MinutesPort over a private block host holding only the minutes
    block. For tests and helpers; `main()` builds its own."""
    path = Path(path)
    file = MinutesFile(path, threading.RLock(), report)
    sdir = Path(session_dir) if session_dir is not None else path.parent
    blocks = build_session_blocks(
        session_dir=sdir, sink=lambda i: None, log=log,
        load=load_from({"minutes-document": MinutesDocumentBlock}))
    return MinutesPort(file, blocks, session_dir=sdir, mode=mode)


# ============================================================ prefilter
# German: this lands on a surface a meeting reads (AGENTS.md naming
# convention). Distinct from correction.HEADER on purpose: that one
# names a language model ("das sich irren kann"), and attaching it to
# rule-based output would misname what actually touched the text.
PREFILTER_HEADER = (
    "# Korrigiertes Transkript\n"
    "\n"
    "⚠️ **Regelbasiert bereinigt, nur zur Anzeige.** Verbindlich ist "
    "das Rohtranskript in `protokoll.md`; hier wurden Fuelllaute und "
    "Wortwiederholungen nach festen Regeln entfernt — kein "
    "Sprachmodell, keine Bedeutungsaenderung.\n"
    "\n")


def write_prefiltered(path: Path, text, speaker=None, stamp: str | None = None) -> None:
    """Append one rule-filtered line to the eye-only corrected transcript.

    Independent of --correct: correction_prefilter.py
    is a pure function, no I/O, no model, so unlike CorrectionWorker this
    needs no queue and no worker thread -- it runs synchronously in the
    SSE reader, right after the raw line is already on disk.

    Never raises: a broken prefilter must cost this display file a line,
    never the raw transcript that `MinutesFile.append_raw` already wrote
    (AGENTS.md -- every utterance lands in the raw transcript first).
    On failure the raw text is written instead, so the display file
    never gets gaps.
    """
    try:
        gefiltert, _ = correction_prefilter.prefilter(text)
    except Exception as e:                    # noqa: BLE001 -- see docstring
        log("prefilter skipped:", type(e).__name__)
        gefiltert = text
    zeile = MinutesFile.line(gefiltert, speaker, stamp)
    try:
        neu = not path.exists() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8") as f:
            _flock(f)
            if neu:
                f.write(PREFILTER_HEADER)
            f.write(zeile)
            f.flush()
    except (OSError, UnicodeError) as e:
        log("prefilter file not writable:", type(e).__name__)


# =================================================================== prompt
POLICY_ASK_LINE = ("  ? {id} — Robo-Rueckfrage: wertet Robo selbst aus -- "
                   "nicht aufloesen, nicht ausfuehren.")


def open_state_block(state, *, policy_ask_id: str = "") -> str:
    """The open entries, with their identities, for the prompt.

    The consumer owns the identities; the agent only ever references them.
    Whether a language model reproduces a stable id over four hours is not
    answered by the evidence, and this design does not bet on it.

    What fell off the stage (parked notices, expired questions) is not
    listed: the host files it into the minutes itself
    (`prepare_turn`). ASCII transliteration, like the rest of the prompt.

    `policy_ask_id` names level 2's own open question (MM-B). It sits in
    the ordinary question slot, but it is Robo's to evaluate: listed as
    resolvable, the agent could resolve it, or act on the room's "Ja"
    itself and file a second, unmarked document. So it leaves the
    resolvable list and gets its own line saying exactly that. "" (the
    default) leaves the block byte for byte as it was.
    """
    # A snapshot taken under the state's own lock: the ticker sweeps these
    # lists twice a second, and this runs on the agent thread.
    offen = state.open_entries()
    eigene = bool(policy_ask_id) and any(e.id == policy_ask_id for e in offen)
    lines = [f"  {'?' if e.id.startswith('Q') else '⚠'} {e.id} — {e.text}"
             for e in offen if not (eigene and e.id == policy_ask_id)]
    block = ("\nOFFEN AUF DER BUEHNE (nur du kannst das aufloesen):\n"
             + "\n".join(lines) + "\n") if lines else ""
    if eigene:
        block += ("" if lines else "\n") + \
            POLICY_ASK_LINE.format(id=policy_ask_id) + "\n"
    return block


def prompt_file_list(names) -> str:
    """The `DATEIEN IM PROJEKTORDNER` lines of the v2 prompt.

    Names only, one per line, at most PROMPT_FILE_LIMIT. A name that could
    not be written as a `[[wikilink]]` (line break, control character,
    `[`, `]`, `|`) is left out: offering it would invite a link the
    consumer then has to strip again.
    """
    shown = []
    for name in names or ():
        if not isinstance(name, str) or not name.strip():
            continue
        if re.search(r"[\x00-\x1f\x7f\[\]|]", name):
            continue
        shown.append("     " + name.strip())
        if len(shown) >= PROMPT_FILE_LIMIT:
            break
    return "\n".join(shown) if shown else "     (keine)"


def enrollment_mirror(control: str | None, enrolled: str | None,
                      names: list[str]) -> str | None:
    """The stage mirror line for an intro-round event, or None.

    `control` is enroll.classify_control()'s verdict for this
    utterance, `enrolled` the name maybe_enroll() just registered,
    `names` everyone enrolled so far, in enrollment order.
    """
    if control == "start":
        text = ("Vorstellungsrunde läuft — bitte nacheinander: „Ich bin …“ "
                "und ca. 10 Sekunden allein sprechen. Zum Schluss: "
                "„Hey Robo, Vorstellungsrunde fertig“.")
        return f"{text} Erkannt: {', '.join(names)}" if enrolled else text
    if control == "end":
        if names:
            return f"Vorstellungsrunde beendet — erkannt: {', '.join(names)}"
        return "Vorstellungsrunde beendet — niemand erkannt, bitte wiederholen"
    if enrolled:
        return f"Erkannt: {', '.join(names)}"
    return None


def order_note_for(wake_word: bool) -> str:
    """The order note of a turn: none without the wake word."""
    return ORDER_NOTE if wake_word else ""


def refresh_link_names(writer, folder, log) -> list[str]:
    """Re-read the project's file names; set the FULL set on the writer.

    Returns the sorted list (the prompt shows the first PROMPT_FILE_LIMIT).
    Never raises: on failure the previous set stays and [] comes back.
    """
    try:
        names = project_md_names(Path(folder))
    except Exception as e:                # noqa: BLE001 -- agent thread
        log("project file list skipped:", repr(e))
        return []
    writer.link_names = set(names)
    return names


def prepare_turn(writer, state, folder, unrecorded, log) -> list[str]:
    """What a turn does before its prompt is built. Never raises.

    Refreshes the file names for the wikilink check and the prompt, and
    files what fell off the stage (`unrecorded`) as `frage` entries --
    the consumer owns the protocol, so it does this itself, independent
    of how the run goes, and marks exactly those that landed. A failure
    in either step is logged; it must not cost the turn. Returns the
    prompt's file names (at most PROMPT_FILE_LIMIT).
    """
    names = refresh_link_names(writer, folder, log)
    try:
        state.mark_recorded(writer.file_unrecorded(unrecorded))
    except Exception as e:                # noqa: BLE001 -- agent thread
        log("unrecorded entries not filed:", repr(e))
    return names[:PROMPT_FILE_LIMIT]


def build_prompt(protocol_path, block, state,
                 backlog_note: str = "", order_note: str = "",
                 project_dir=None, first_turn: bool = True,
                 file_names=None, topic_criterion: bool = False,
                 thread: list | None = None,
                 *, policy_ask_id: str = "") -> str:
    """The prompt for one run.

    `topic_criterion` (C2 measure 1, default OFF): on the first turn only,
    splices TOPIC_CRITERION in right after the `topic` bullet's anchor
    line. With the default, rendering is byte-identical to f1bf253 (pinned
    in tests/test_consumer.py). On a delta turn the flag has no effect --
    the criterion, once sent, lives on in the worker session's history
    (B5), so it is never repeated.

    `thread` (C2 measure 2, default OFF/None, D5): a list of Verlauf
    bullet strings (most recent last). When it is not None AND
    `first_turn` is False, the delta gets a block with the current
    headline (`state.headline`, or "(noch keine)" when empty) and the
    last THREAD_BULLETS bullets ("(noch keine)" when the list is empty),
    inserted between the open state and "Neu gesprochen ...". With the
    default (None), rendering is byte-identical to f1bf253/06bdb64. On
    the first turn `thread` has no effect: B5's delta mechanism carries
    the thread onward, nothing goes back to a full prompt per turn.

    B5: the worker holds a running session (gateway/worker.py, one
    process, turns as JSON lines) -- the instruction, the paths and the
    reply format only have to go out ONCE. `first_turn=True` (the
    default, and everything that calls this without the flag) still
    builds the full AGENT_PROMPT. Every later turn in the SAME session
    gets only what actually changed: the spoken block, the situational
    notes, the open entries. Measured against the real session
    (docs/roadmap.md B5): 233 of 233 turns carried the full prompt.

    No context management is touched here -- the worker still passes the
    FULL conversation history to every call; this function only stops
    minting new copies of the static part.

    The first turn is AGENT_PROMPT (the JSON-reply instruction) with the
    project's file names (at most PROMPT_FILE_LIMIT) for the wikilink rule;
    later turns are DELTA_PROMPT. The order note is the caller's choice
    (`run_turn` passes `order_note_for(wake_word)`).

    `policy_ask_id`: the stage id of level 2's own open question
    (`DecisionsBlock.open_ask_id()`), passed on to `open_state_block`. ""
    (the default, and every caller without `--policy`) changes nothing.
    """
    open_state = open_state_block(state, policy_ask_id=policy_ask_id)
    if not first_turn:
        thread_block = ""
        if thread is not None:
            heading = state.headline if state.headline else "(noch keine)"
            bullets = thread[-THREAD_BULLETS:] if thread else []
            body = "\n".join(bullets) if bullets else "(noch keine)"
            thread_block = (
                "\nDER FADEN BISHER (zum Vergleichen, nicht zum Wiederholen):\n"
                f"Ueberschrift jetzt: {heading}\n"
                "Letzte Verlauf-Punkte:\n"
                f"{body}")
        return DELTA_PROMPT.format(
            block=block.text,
            situation=backlog_note + order_note + state.follow_up_note(),
            open_state=open_state,
            thread=thread_block)
    template = AGENT_PROMPT
    if topic_criterion:
        if template.count(TOPIC_CRITERION_ANCHOR) != 1:
            # Fail loudly (D4/Task 1): a silent no-op here would ship a
            # variant that measures nothing.
            raise ValueError(
                "TOPIC_CRITERION_ANCHOR not found exactly once in "
                "the selected template -- refusing to splice the criterion in")
        template = template.replace(
            TOPIC_CRITERION_ANCHOR, TOPIC_CRITERION_ANCHOR + TOPIC_CRITERION)
    return template.format(
        files=prompt_file_list(file_names),
        path=protocol_path, block=block.text,
        # The folder, so the agent can look up the meeting's own background
        # material (Leitfaden, earlier protocol, notes) when a section
        # actually demands it. Derived from the protocol path when nothing
        # else is given, so the two can never drift apart -- but the caller
        # passes the PROJECT folder, because since the session folders the
        # protocol's own parent holds only this run's machine files and
        # none of the background the agent is being pointed at.
        folder=Path(project_dir if project_dir is not None
                    else Path(protocol_path).parent),
        situation=backlog_note + order_note + state.follow_up_note(),
        open_state=open_state)


# ============================================================ the reply
# The main turn's reply reaches the host as an `agent_reply` event; the
# `capture-bullets` block parses it and answers with `block_output` events.
# These pieces are the host's side of that: it collects the outputs, files
# the minutes items through the minutes-document block, appends the stage
# proposals to the proposal file (the host alone writes it and, through
# `StageWriter.ingest`, the stage file) and keeps an unreadable reply.
CAPTURE_BLOCK_ID = "capture-bullets"
# The reply's minutes lists and the tag each item is filed under.
BULLET_TAGS = {"verlauf": "notiz", "entscheidungen": "beschluss",
               "aufgaben": "aufgabe", "offene_fragen": "frage"}


@dataclass
class TurnReply:
    """What one `agent_reply` event came to: the minutes items and the stage
    actions in the order the agent gave them, or the failure payload."""
    bullets: list
    proposals: list
    failure: dict | None = None


DECISIONS_BLOCK_ID = "decisions-tasks-questions"


def turn_opened(blocks: SessionRuntime, b, turn: int) -> dict:
    """Tells the blocks a turn opens (`turn_open`) and returns what the
    decisions block answered (`turn_orders`): the order note, the spoken
    text and the id of level 2's open question. Without the block: the
    block's own text and nothing else."""
    got = blocks.collect("block_output", {"turn_open": {
        "turn": turn, "text": b.text, "wake_word": bool(b.wake_word)}},
        DECISIONS_BLOCK_ID)
    for out in reversed(got):
        if out.get("kind") == "turn_orders" and out.get("turn") == turn:
            return {"note": out.get("note") or "",
                    "spoken": out.get("spoken") or b.text,
                    "open_ask_id": out.get("open_ask_id") or ""}
    return {"note": "", "spoken": b.text, "open_ask_id": ""}


def turn_closed(blocks: SessionRuntime, turn: int, *, ok: bool,
                answered: bool) -> None:
    """Tells the blocks the turn is over (`turn_closed`)."""
    blocks.dispatch("block_output", {"turn_closed": {
        "turn": turn, "ok": ok, "answered": answered}})


# --lanes --policy (ADR 0026): the reason of a block that exists only to
# carry level 2's finished orders into a turn of the serial worker.
ORDERS_REASON = "orders"
# --lanes (ADR 0026): what an orders turn may put on the stage. The topic
# and the answer to a call have their own owners under --lanes (the topics
# block with the call lane, and the call lane), so a `topic` or `main`
# proposal of an orders turn is not applied; its order settles
# `unanswered` at turn_closed like any order that brought nothing.
LANE_ORDER_ACTIONS = frozenset({"decision", "tag_entry"})


def order_answers(actions: list) -> list:
    """The proposals of an orders turn under --lanes that answer a level-2
    order: a `decision` or `tag_entry` carrying an `order` id. Everything
    else the agent proposed in that turn is dropped (ADR 0026)."""
    return [a for a in actions if isinstance(a, dict)
            and a.get("action") in LANE_ORDER_ACTIONS
            and is_token(a.get("order"))]


def orders_turn_hook(orders_waiting: Callable[[], bool],
                     enqueue: Callable[[Block], int],
                     log: Callable[..., None] = log) -> Callable:
    """The decider result hook `--lanes --policy` installs (ADR 0026).

    Under `--lanes` no ordinary block reaches `RunQueue`, so nothing would
    ever open a turn for level 2's orders. The hook runs on the decider
    thread AFTER the policy runner has handled the same result -- every
    order that result queued is already waiting -- and enqueues one
    `orders` block (the decided block's text) when any order waits. A
    result that queued nothing costs nothing: no block, no agent turn.

    Takes no stage lock; `enqueue` takes only the queue's own condition.
    Never raises: a failing hook must not cost the decider its result."""
    def hook(result, snap, reason) -> None:
        try:
            if not orders_waiting():
                return
            text = str(getattr(snap, "block", "") or "")
            enqueue(Block(sentences=[text] if text else [],
                          reason=ORDERS_REASON, wake_word=False))
        except Exception as e:            # noqa: BLE001 -- decider thread
            log("orders turn not enqueued:", repr(e))
    return hook


class ReplyIntake:
    """Dispatches a reply and collects the capture block's outputs.

    The answers come back through `SessionRuntime.collect`.
    """

    def __init__(self, blocks: SessionRuntime) -> None:
        self.blocks = blocks

    def read(self, text: str, turn: int) -> TurnReply:
        seen = self.blocks.collect(
            "agent_reply", {"text": text, "turn": turn}, CAPTURE_BLOCK_ID)
        reply = TurnReply([], [])
        for out in seen:
            kind = out.get("kind")
            if kind == "parse_failed":
                reply.failure = out
            elif kind == "bullet":
                reply.bullets.append(out)
            elif kind == "stage_proposal" and isinstance(out.get("action"), dict):
                reply.proposals.append(out["action"])
        if reply.failure is not None:      # a failed reply files nothing
            reply.bullets, reply.proposals = [], []
        return reply


def append_proposals(path: Path, actions: list[dict]) -> int:
    """Append the reply's stage actions to the proposal file, one JSON
    object per line, in ONE write. Returns how many lines were written;
    raises `OSError` when the file cannot be written."""
    lines = [json.dumps(a, ensure_ascii=False) + "\n" for a in actions]
    if lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(lines))
    return len(lines)


def file_bullets(writer, bullets: list[dict], log=log) -> int:
    """File the reply's minutes items through the writer (and so through
    the minutes-document block). Returns how many are in the minutes
    afterwards -- a duplicate counts, it is there already. A refused item
    is logged and traced; it costs that item only."""
    filed = 0
    for out in bullets:
        tag = BULLET_TAGS.get(out.get("section"))
        if tag is None:
            continue
        reason = writer.file_entry(tag, out.get("text"))
        if reason in ("", "duplicate"):
            filed += 1
            continue
        log("entry not filed:", reason)
        writer.trace("entry_failed", kind=tag, reason=reason.replace(" ", "_"))
    return filed


def keep_failed_reply(path: Path, failure: dict, trace, log=log) -> None:
    """A reply that did not parse: the structural trace gets the numbers
    and a token (it holds no speech, by design), the raw text goes to
    `path`, one JSON object per line. Never raises."""
    reason = "failure_marker" if failure.get("failed") else "no_json"
    raw = failure.get("raw")
    raw = raw if isinstance(raw, str) else ""
    try:
        trace.record("reply_unreadable", reason=reason, chars=len(raw))
        row = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "turn": failure.get("turn"), "reason": failure.get("reason"),
               "raw": raw}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:                # noqa: BLE001 -- never cost the turn
        log("failed reply not kept:", repr(e))


# =================================================================== queue
class RunQueue:
    """Accepts finished blocks and runs one agent per block.

    Three decisions, justified in the review report:

    BACKLOG: An agent run takes 43-73 s, blocks can arrive every few
    seconds. Instead of pushing out stale states, waiting blocks are merged
    into ONE run (up to `max_chars`). The stage then shows the current
    state instead of the one from twenty minutes ago, and the queue cannot
    grow unbounded.

    ORDERS (wake word) jump the queue and are NEVER mixed with anything
    else — they are an address to the agent, not a transcript.

    FAILURE: A failed run no longer loses the block; its text is prepended
    to the next run (capped at `carry_max`).
    """

    def __init__(self, run: Callable[[Block], bool],
                 max_chars: int = MAX_BATCH_CHARS,
                 carry_max: int = MAX_CARRY_CHARS) -> None:
        self.run = run
        self.max_chars = max_chars
        self.carry_max = carry_max
        self._calls: deque = deque()
        self._normal: deque = deque()
        self._cond = threading.Condition()
        self._carry: list[str] = []
        self._carry_wake = False
        self._last_was_call = False
        self.counted = 0
        self.failed = 0
        self.batched = 0

    # -------------------------------------------------------------- queue
    def enqueue(self, b: Block) -> int:
        with self._cond:
            self.counted += 1
            (self._calls if b.wake_word else self._normal).append(b)
            self._cond.notify()
            return len(self._calls) + len(self._normal)

    def backlog(self) -> int:
        with self._cond:
            return len(self._calls) + len(self._normal)

    def next_batch(self, timeout: float | None = None) -> Block | None:
        """Fetches the next item. Merges on backlog."""
        with self._cond:
            if not self._calls and not self._normal:
                if timeout is None or timeout > 0:
                    self._cond.wait(timeout)
            # Orders take priority — but never two in a row while a
            # transcript is waiting. Otherwise it starves: in the load test,
            # orders arrived faster than the agent ran, and the queue of
            # normal blocks grew to 1988 without ever being worked off. This
            # way an order waits at most one extra run.
            if self._calls and not (self._last_was_call and self._normal):
                self._last_was_call = True
                return self._calls.popleft()          # never mix
            if not self._normal:
                return None
            self._last_was_call = False
            teile = [self._normal.popleft()]
            zeichen = teile[0].chars
            while (self._normal
                   and zeichen + self._normal[0].chars <= self.max_chars):
                b = self._normal.popleft()
                teile.append(b)
                zeichen += b.chars
            if len(teile) == 1:
                return teile[0]
            self.batched += len(teile)
            log(f"  backlog: {len(teile)} blocks merged into one run "
                f"({zeichen} chars)")
            return Block(sentences=[t.text for t in teile],
                         start=teile[0].start, end=teile[-1].end,
                         reason="batch", wake_word=False)

    # ------------------------------------------------------------ process
    def carry_text(self) -> str:
        return " ".join(self._carry)

    def _trim(self, saetze: list[str]) -> list[str]:
        """Fill from the back — what was said last matters most."""
        raus: list[str] = []
        summe = 0
        for s in reversed(saetze):
            if raus and summe + len(s) > self.carry_max:
                raus.insert(0, "[… aeltere unverdichtete Abschnitte ausgelassen]")
                break
            raus.insert(0, s)
            summe += len(s)
        return raus

    def process(self, b: Block) -> bool:
        if self._carry:
            b = Block(sentences=[*self._carry, *b.sentences],
                      start=b.start, end=b.end, reason=b.reason,
                      wake_word=b.wake_word or self._carry_wake)
        try:
            erfolg = bool(self.run(b))
        except Exception as e:            # noqa: BLE001 — the thread keeps running
            log("agent error:", repr(e))
            erfolg = False
        if erfolg:
            self._carry = []
            self._carry_wake = False
        else:
            self.failed += 1
            self._carry = self._trim(b.sentences)
            self._carry_wake = b.wake_word
            log(f"  not processed — {len(self.carry_text())} chars carry "
                f"into the next run")
        return erfolg

    def work_loop(self, stop: threading.Event) -> None:
        """Worker thread: runs until `stop` is set AND nothing is waiting."""
        while True:
            b = self.next_batch(timeout=0.5)
            if b is None:
                if stop.is_set():
                    return
                continue
            self.process(b)

    def finish(self) -> None:
        """At the end: try once more whatever was left after a failure.
        Otherwise exactly the section the agent stumbled on falls through
        the cracks."""
        if not self._carry:
            return
        log("last attempt for the unprocessed remainder …")
        rest = Block(sentences=self._carry, reason="carry",
                     wake_word=self._carry_wake)
        self._carry = []
        self._carry_wake = False
        self.process(rest)


def write_stage(path: Path, data: dict) -> None:
    """Replace the stage file in one go — a half-written file would be
    unreadable for everyone reading it at the same time."""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def applied_event(state: StageState, action: dict) -> dict | None:
    """The trace event for ONE accepted proposal action, or None.

    The identities are read back off the state, not out of the action:
    the consumer assigns them, the agent only ever references them. Only
    structure travels -- an id, a notice kind, a duration, and for a
    call-out the instruction, which `EventLog` hashes before it writes.

    CALLER'S DUTY: this must run under `StageState._lock`, in the SAME
    critical section as the `apply()` whose result it describes -- see
    `StageWriter.ingest()`. The read-back (`notices[-1]`, `main.id`) is
    only this action's entry as long as nobody else can apply in between.

    A defensive read: the ticker sweeps the same lists twice a second, so
    the entry this call is looking for can in principle be gone again. A
    missing trace line is a lost measurement, a raised exception in the
    agent thread is a lost turn.
    """
    kind = action.get("action") if isinstance(action, dict) else None
    try:
        if kind == "decision":
            return {"event": "decision_added", "id": state.decisions[-1].id,
                    "count": len(state.decisions)}
        if kind == "notice":
            notice = state.notices[-1]
            return {"event": "notice_raised", "id": notice.id,
                    "kind": notice.kind}
        if kind == "question":
            return {"event": "question_raised", "id": state.question.id}
        if kind == "main":
            # The one event that carries an instruction. It is the measure
            # of call-out fulfilment: how many orders ended in a visible
            # result. The quote is hashed, never stored.
            return {"event": "call_answered", "id": state.main.id,
                    "quote": state.main.quote,
                    "collapsed_lines": state.main.collapsed_lines}
        if kind == "topic":
            # The counterpart without an order: the surface followed the
            # conversation on its own. This is the line the two withdrawal
            # signals of the design are read off -- how densely the
            # changes follow each other, and how often a call-out only
            # fetched up what should have happened by itself.
            #
            # NO TITLE. A topic title is a sentence out of the meeting and
            # therefore not a token; the trace takes its LENGTH, in an
            # existing numeric field, and nothing else. The filter in
            # shared/events.py would drop it anyway -- it is not relaxed,
            # and nothing here relies on it catching this.
            return {"event": "topic_changed", "id": state.main.id,
                    "chars": len(state.main.title),
                    "collapsed_lines": state.main.collapsed_lines}
        if kind == "graph":
            # Numbers only, like every other line of the trace: how many
            # nodes the surface showed, never which ones. A node label is
            # a file name, and file names in this vault are names.
            return {"event": "graph_changed", "id": state.main.id,
                    "count": len(action.get("graph", {}).get("nodes", []))}
        if kind == "resolve":
            entry_id = str(action.get("id") or "").strip()
            for entry in [*state.notices, state.question]:
                if entry is None or entry.id != entry_id:
                    continue
                name = ("question_resolved" if entry_id.startswith("Q")
                        else "notice_resolved")
                return {"event": name, "id": entry_id,
                        "age_s": round(entry.settled_at - entry.raised_at, 1)}
    except (AttributeError, IndexError):
        return None
    return None                          # mirror, headline, links, none


def answered_the_call(main_id: str, before_id: str, quote: str) -> bool:
    """Did THIS surface answer the call-out, or did it just move?

    Section 7 counts three outcomes of an order, and two of them are
    written from here: a visible result (`call_answered`, emitted by
    `applied_event()`) and the absence of one (`call_unanswered`).

    THE SURFACE STOPPED BEING EVIDENCE when the automatic topic change
    arrived. `main.id` now moves on its own, so an order the agent
    answered with a `topic` instead of a `main` passes an id comparison
    as "answered" and leaves NEITHER event -- no `call_answered`, that
    is the call-out path, and no `call_unanswered` either. That is a
    silent hole in exactly the measure the core motivation names as its
    withdrawal signal: a call-out that orders nothing and only fetches
    up what should have happened by itself.

    The quote decides instead. Only `_main()` puts one on the surface --
    `_topic()` sets it empty on purpose, because nobody gave an order --
    so an empty quote means the surface on display is not this order's
    answer, however recently it moved.
    """
    return main_id != before_id and bool(quote)


def unanswered_kind(main_id: str, before_id: str, mirror: str,
                    before_mirror: str, headline: str,
                    before_headline: str) -> str:
    """What the agent did INSTEAD of answering. A token, never text.

    Without this an agent that answered an order with a mirror line ("I
    cannot do that") and one that answered it with nothing at all looked
    exactly the same in the trace. `topic` is the fourth case and the new
    one: the surface moved, but on the automatic path -- the topic change
    overtook the order.
    """
    if main_id != before_id:
        return "topic"
    if mirror != before_mirror:
        return "mirror"
    if headline != before_headline:
        return "headline"
    return "none"


class CallOutAnnouncements:
    """Which announcement belongs to which call-out block.

    `block_done()` announces the moment a call-out block closes, BEFORE it
    is queued; `run_turn()` ends that announcement when the block's run
    is over. Between the two lie the queue and 40-180 s of a run -- longer
    than the 60 s timeout -- so neither "the oldest `main`" nor "every
    `main` standing when the run starts" names the right one: RunQueue
    hands out orders ONE at a time, and a second call-out still waiting in
    the queue has its announcement standing already. Only the block
    itself says which announcement is its own.

    KEYED BY `(start, end)`, not by the object. `RunQueue.process()` wraps
    a block in a NEW `Block` when a failed run's text is carried over, and
    that copy keeps `start` and `end` -- the times of the block's own
    first and last sentence, which no second block shares. A block with
    no entry here -- the carry remainder of `RunQueue.finish()`, a merged
    backlog (orders are never merged) -- settles nothing: its call-out's
    announcement was already ended by that call-out's own run, or it
    times out.

    Two threads: `block_done()` runs in the SSE reader or the ticker,
    `run_turn()` in the agent thread -- hence the lock. It is a leaf: the
    writer is called OUTSIDE it, so the documented lock order is untouched.
    """

    # The fallback only: a block with no content word at all (MM-L).
    LABEL = "Auftrag angenommen"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: dict[tuple[float, float], str] = {}

    def announce(self, writer: "StageWriter", b: Block) -> str:
        """Announce `b` on the stage and remember the id for its run.
        The label names the WHAT ("Zuruf:" + <= 3 content words, MM-L);
        it reaches buehne.json only, never the trace."""
        pending_id = writer.announce(
            "main", pending_label("main", b.text, fallback=self.LABEL))
        if pending_id:
            with self._lock:
                self._ids[(b.start, b.end)] = pending_id
        return pending_id

    def take(self, b: Block) -> str:
        """The announcement of `b`, removed from the map. "" when none."""
        with self._lock:
            return self._ids.pop((b.start, b.end), "")


def settle_call_out(state: StageState, pending_id: str, *, answered: bool,
                    ok: bool) -> str:
    """End one call-out's announcement after its run. Returns the outcome.

    Three outcomes the trace can tell apart: `arrived` (a surface with
    this order's quote came up), `failed` (the run itself failed) and
    `unanswered` (it came back without answering). An id that is gone
    already -- timed out, displaced -- is skipped by `settle()` itself.
    """
    outcome = "arrived" if answered else ("unanswered" if ok else "failed")
    if pending_id:
        state.settle(pending_id, outcome)
    return outcome


# ============================================================ stage writer
def make_session_id(project_slug: str, started: datetime) -> str:
    """A filesystem-safe session id, one per meeting.

    Hermes uses the session id as a file name, so it has to pass the path
    guard: no `/`, no `..`, no leading drive letter. Deriving it from the
    project and the start time makes a restart land on the SAME session,
    which is exactly what re-attaching needs.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", project_slug.lower()).strip("-")
    return f"cothinker-{slug or 'meeting'}-{started:%Y%m%d-%H%M}"


def worker_interpreter() -> str:
    """The Python that can import Hermes.

    The worker must run under Hermes' own interpreter, not the
    consumer's -- the consumer deliberately has no Hermes in its
    environment. `hermes` normally lives in that venv's bin directory.
    """
    exe = shutil.which("hermes")
    if exe:
        candidate = Path(exe).resolve().parent / "python3"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def default_lane_command(*, python_exe: str, worker_py: Path, model: str,
                         provider: str, hermes_home: Path) -> list:
    """The argv `--lanes` builds for a lane worker when neither
    `--lane-call-worker-command` nor the mirroring-coach block's
    `lane_coach_worker_command` config key overrides it.

    Always carries `--no-history`: a lane's prompt
    (`blocks/mirroring_coach/context.py`'s `build_prompt()`) already carries the
    full bounded window + Stand summary every turn, so letting
    `gateway/worker.py` ALSO replay the ever-growing per-session
    SessionDB conversation on top of it would defeat the whole point of
    the window -- measured growing unboundedly over a meeting instead of
    staying near `window_size`. The other option considered,
    restarting the worker on a fresh session id every window roll, was
    rejected: a restart costs ~19s per lane (point 3 below), and a roll
    can happen every `roll_every` (20) utterances -- far too often for
    that cost to be acceptable mid-meeting.
    """
    return [python_exe, str(worker_py), "--model", model, "--provider",
           provider, "--hermes-home", str(hermes_home), "--no-history"]


def build_lanes(a, log: Callable[..., None], *, call_command: list,
                session_id: str, project_agenda: str,
                state, writer, folder: Path, path: Path,
                seam=None):
    """The meeting's `call_lane.CallLane` (the coach lane is the
    `mirroring-coach` block; its agent comes from
    `block_host_wiring.make_agent_for`), including the agent-seam wiring for
    `COTHINKER_AGENT=api` (`seam` is `agent_seam(log)`, computed once by the
    caller) -- the call lane stays on the worker unless
    `COTHINKER_AGENT_CALL=api` too.

    Extracted out of `main()` so it is covered by a test that does not need
    a full `main()` run.
    """
    call_lane_factory = None
    if seam is not None and os.environ.get("COTHINKER_AGENT_CALL", "").strip() == "api":
        call_lane_factory = seam[0]
    extra_dirs = call_lane_mod.extra_dirs_from_env(
        os.environ.get("COTHINKER_EXTRA_DIRS", ""))
    return call_lane_mod.CallLane(
        call_command=call_command,
        call_session_id=f"{session_id}-call",
        project_agenda=project_agenda, state=state, writer=writer,
        call_paths=call_lane_mod.call_paths_text(folder, path, extra_dirs),
        image_dirs=stage_image_mod.allowed_dirs(folder, extra_dirs),
        call_lane_factory=call_lane_factory,
        log=log)


def agent_seam(log: Callable[..., None]):
    """`COTHINKER_AGENT=api` (default: worker): the agent seam
    (gateway/agent_lane.py, a Hermes API server, e.g. on another machine
    through an SSH tunnel) as `(lane_factory, coach_briefing)`, else None.
    The seam's one shared server has no per-session profile selector
    (gateway/agent_lane.py's module docstring) -- the coach's own SOUL.md is
    sent as this session's briefing instead of trusting the server's
    ambient profile to be this one."""
    if os.environ.get("COTHINKER_AGENT", "worker").strip() != "api":
        return None
    import agent_lane
    agent_url = os.environ.get("COTHINKER_AGENT_URL", "").strip()
    key_env = (os.environ.get("COTHINKER_AGENT_KEY_ENV", "").strip()
               or "API_SERVER_KEY")
    agent_key = os.environ.get(key_env, "").strip()
    if not agent_url or not agent_key:
        raise SystemExit("COTHINKER_AGENT=api needs COTHINKER_AGENT_URL "
                         f"and the key in ${key_env}")
    factory = agent_lane.api_lane_factory(agent_url, agent_key, log=log)
    briefing = agent_lane.coach_soul_briefing(
        Path.home() / ".hermes/profiles/cothinker-coach")
    log(f"agent seam: coach=api"
        f" call={'api' if os.environ.get('COTHINKER_AGENT_CALL', '').strip() == 'api' else 'worker'}"
        f" url={agent_url} coach_briefing_chars={len(briefing)}")
    return factory, briefing


class StageWriter:
    """The only writer of `buehne.json`.

    The agent proposes, this class curates: it applies the caps, keeps the
    clocks and writes atomically. That also removes the hazard the stage
    had to tolerate before -- a half-written file from a process we do not
    control.
    """

    def __init__(self, project_dir: Path, state: StageState, *,
                 proposals: Path,
                 log: Callable[..., None] = log,
                 events: EventLog | None = None) -> None:
        self.path = Path(project_dir) / "buehne.json"
        self.state = state
        self.log = log
        # Optional: without a trace the writer works exactly as before.
        self.events = events
        # No default. The flat `vorschlag.jsonl` was removed because the
        # first run of the next meeting replayed it from byte 0 -- and a
        # default is how it would come back, silently, on the one call
        # site that forgot to pass the dated file.
        self.proposals = Path(proposals)
        self._status = {"state": "listening", "text": "", "ticking": False}
        self._since = ""
        self._warning = ""
        # The failed run of section 3.2. It shares the second header line
        # with the warnings instead of taking the operating line, which
        # for 40-180 s after every failure left the round without any
        # answer to "did you hear me".
        self._failed = False
        # Start at the END of the proposal file, not at byte 0. Whatever
        # is already in it has either been applied by an earlier run of
        # this consumer (a restart mid-meeting must not replay the whole
        # stage) or belongs to another meeting entirely -- and yesterday's
        # question would sit in today's single question slot for ten
        # minutes while today's real notices get parked.
        try:
            self._offset = self.proposals.stat().st_size
        except OSError:
            self._offset = 0
        self._last_hash = ""
        self._last_stamp: tuple | None = None
        # Accepted actions that actually moved the surface, counted over
        # the whole meeting. It is what makes the latency measure of
        # section 7 usable: latency runs from the last sentence of a block
        # to the EFFECTIVE change, and a turn that changed nothing has no
        # such change to measure to. Read before and after a turn.
        self.applied = 0
        # Three threads write the stage: the ticker (twice a second), the
        # SSE reader (on every utterance) and the agent thread (per turn).
        # They share ONE temporary file name, so two interleaved writes
        # would publish a wreck through os.replace() -- exactly the
        # half-written file this class exists to make impossible.
        #
        # The same lock guards the status and the warning: without it two
        # threads can publish a combination that never existed, or one
        # thread's status is overwritten before it is ever written out.
        # LOCK ORDER: this lock first, then `StageState._lock` (write()
        # takes it while calling content()). Never the other way round.
        self._lock = threading.RLock()
        # No vault configured means no graph -- never a guessed path.
        # `ENTITIES` cannot stand in for it: its fallback
        # `~/vault/entities` does not exist (the symlink is called
        # `30-Entities`), so deriving one from the other would point the
        # scan at nothing while reporting success.
        self.graph_source: GraphSource | None = None
        self._graph_session: Path | None = None
        self._graph_datum = ""
        self._graph_teilnehmende = ""
        # The protocol the consumer files entries into itself (`main()`
        # sets it at start-up). None means there is none -- a `tag_entry`
        # proposal is refused. `link_names` is the set of
        # project file stems every entry's wikilinks are checked against,
        # refreshed by `run_turn` at the start of a turn and again right
        # before its proposals are read; None means "no check".
        self.protocol: MinutesPort | None = None
        self.link_names: set[str] | None = None
        # Level 2 (the decisions-tasks-questions block) when `--policy` is
        # on: gives an order id's marker and hears when it arrived. None
        # keeps every proposal exactly as it was before level 2.
        self.policy = None
        # The knowledge-link block (blocks/knowledge_link), which owns the
        # wiki layer: a new topic block gets the related notes as
        # wikilinks. Until `main()` hands over the session host's own
        # instance this one has no vault and writes the block exactly as
        # before. Interim handle (Wave B.5).
        self.knowledge_link = KnowledgeLinkBlock()
        # MM-G: at most ONE topic page per protocol topic block per run.
        # Block key (`MinutesPort.current_topic`) -> the page. Both threads
        # that create pages (the decider via level 2, the agent via the
        # Zuruf) meet in `file_topic_page`, which holds `_page_lock` from
        # the check to the registration. LOCK ORDER: `_page_lock` FIRST,
        # then `MinutesPort.lock` / this writer's `_lock` -- nobody takes it
        # while holding either.
        self._page_lock = threading.Lock()
        self._topic_pages: dict[str, Path] = {}

    def attach_vault(self, root, *, session_dir, datum: str,
                     teilnehmende: str, include_network: bool = False) -> None:
        """Turn the graph surface on. Without this call it stays off."""
        exclude = () if include_network else DEFAULT_EXCLUDE
        self.graph_source = GraphSource(root, exclude=exclude)
        self._graph_session = Path(session_dir)
        self._graph_datum = datum
        self._graph_teilnehmende = teilnehmende

    def build_graph_action(self, action: dict) -> str:
        """Fill a `graph` action's payload in. "" when it can be shown.

        The agent names a TOPIC, never a path and never a node list: it
        has no vault access through this route, and the surface must not
        be able to claim a note that does not exist. Everything else
        happens here -- seed note, scan, selection.
        """
        if self.graph_source is None:
            return "no vault"
        titel = str(action.get("title") or "").strip()
        if not titel:
            return "no title"
        self.graph_source.rescan()          # the seed may be seconds old
        try:
            rel, lage = find_or_create_seed(
                self.graph_source.index, titel,
                session_dir=self._graph_session, vault_root=self.graph_source.root,
                file_name=slugify(str(action.get("seed") or titel)),
                raw_name=str(action.get("seed") or titel).strip(),
                datum=self._graph_datum, teilnehmende=self._graph_teilnehmende)
        except (OSError, ValueError):
            # The session folder is reached through the vault root only
            # via a symlink (the real vault: five of them) -- and
            # `Path.resolve()` follows symlinks before `relative_to()`
            # compares them. A session folder that resolves outside the
            # resolved vault root raises ValueError here; that is not a
            # bug in the seed note, it is a caller that isn't under the
            # vault it named. No graph beats a wrong or guessed one.
            return "no graph"
        if lage == "ambiguous":
            # More than one note carries this name (measured: 12 files
            # called README, 5 `kontaktrecherche`). Picking one would
            # invent, creating one would duplicate.
            return "ambiguous seed"
        if lage == "created":
            self.graph_source.rescan()      # the new note is a node now
        try:
            action["graph"] = self.graph_source.build(rel)
        except (KeyError, OSError, ValueError):
            return "no graph"
        return ""

    def trace(self, event: str, **fields) -> None:
        """One line into the event trace, if there is one."""
        if self.events is not None:
            self.events.record(event, **fields)

    def announce(self, kind: str, label: str) -> str:
        """Tell the stage NOW that something of `kind` is on its way.

        The entry point for code that knows something will come -- today
        the call-out in `block_done()`, later the classifier. The label is
        a short German phrase, never a model's; by design (MM-L) it carries
        up to three content words of speech. buehne.json only, never the
        trace.

        Written immediately, not left to the ticker: the promise is that
        the page shows it within half a second of the decision, and the
        ticker alone can eat half of that. The `pending_start` goes into
        the trace in the same step (id and kind only -- the state never
        puts the label into an event). Returns the id, "" when refused.
        """
        pending_id = self.state.announce(kind, label)
        if pending_id:
            if self.events is not None:
                self.events.record_all(self.state.tick())
            self.write()
        return pending_id

    def settle(self, pending_id: str, outcome: str) -> bool:
        """End the pending `pending_id`. False when the state had already
        settled it (a topic arrives as `topic` through StageState.apply) --
        harmless. The caller flushes (`write()`) after a True."""
        return self.state.settle(pending_id, outcome)

    # ------------------------------------------------ protocol v2 entries
    def file_entry(self, tag, text, stamp=None, mark: str = "",
                   also=()) -> str:
        """File one tagged entry into the v2 protocol. "" when it landed.

        Traces `entry_added kind=<tag>` and the wikilink counts -- numbers
        and tokens only, the entry's text never reaches the trace. The
        caller decides what a refusal is called in the trace. Minutes
        writes are not surface changes: `self.applied` is not touched.

        `mark` is a level-2 marker (`*(autonom)*`, `*(bestätigt HH:MM)*`):
        the protocol says who acted. Without one the text goes through
        untouched.

        `also`: stems that count as existing for the wikilink check on top
        of `link_names` -- a file the consumer created a moment ago in the
        session folder, which the project file list never includes.
        """
        if self.protocol is None:
            return "v1 protocol"
        if mark and isinstance(text, str):
            text = with_marker(text, mark)
        names = self.link_names
        if names is not None and also:
            names = set(names) | set(also)
        result = self.protocol.add_entry(tag, text, stamp=stamp, names=names)
        if result.links_seen:
            self.trace("wikilink_seen", count=result.links_seen)
            if result.links_dead:
                self.trace("wikilink_dead", count=result.links_dead)
        if not result.reason:
            self.trace("entry_added", kind=tag)
        return result.reason

    def page_title(self) -> str:
        """The title a topic page is about: the protocol's CURRENT topic
        block, without the link suffix and the marker the consumer added
        to it -- so page and backlink name the same block. Without a topic
        block the stage's main title stands in; without that, ""
        (`seite-HHMM`). Never raises.

        Returned CLEANED (`clean_line`, `title_max()`): the level-2
        guard slugs this very string, and `file_topic_page` passes it on
        unchanged to the links and the page (review item 8)."""
        try:
            lesen = getattr(self.protocol, "current_topic_title", None)
            titel = (self.knowledge_link.bare_title(lesen())
                     if callable(lesen) else "")
            titel = clean_line(titel if isinstance(titel, str) else "",
                               self.knowledge_link.title_max())
        except Exception:                 # noqa: BLE001 -- never raise
            titel = ""
        if not titel:
            try:
                titel = clean_line(self.state.main.title or "",
                                   self.knowledge_link.title_max())
            except Exception:             # noqa: BLE001 -- never raise
                titel = ""
        return titel if isinstance(titel, str) else ""

    def file_topic_page(self, titel, *, session_dir, project_dir, mark: str,
                        created, report=None) -> Path | None:
        """Create a topic page and file its backlink (MM-G D5/D6).

        THE page path: level 2's `document` action and the Zuruf "Robo,
        leg dafuer eine Seite an" both end here. The page is created by
        the knowledge-link block's `create_topic_page`; then the consumer
        itself -- not the agent -- files `[notiz] Themenseite [[<stem>]]` into the current
        topic block, and traces `wiki_create links=<n>`. None when no page
        was created. Never raises: a failure costs the page or the entry,
        never the turn.

        AT MOST ONE PAGE PER PROTOCOL TOPIC BLOCK PER RUN: a second request
        for the block that already has one (the Zuruf after a confirmed
        question, or the other way round) creates nothing and reports
        `page_exists` -- to `report`, else as `policy_refused` in the trace.
        `topic_page()` names the page that is there. Check, creation and
        registration run under `_page_lock`: the decider thread and the
        agent thread both call this.

        CALLER'S DUTY: hold neither `StageWriter._lock` nor
        `StageState._lock` -- filing takes `MinutesPort.lock`, whose report
        takes the writer's lock.
        """
        with self._page_lock:
            vorher = self._block_key()
            if vorher and vorher in self._topic_pages:
                try:
                    if report is not None:
                        report("page_exists")
                    else:
                        self.trace("policy_refused", kind="document",
                                   reason="page_exists")
                except Exception:         # noqa: BLE001 -- never raise
                    pass
                return None
            pfad = self._create_topic_page(
                titel, session_dir=session_dir, project_dir=project_dir,
                mark=mark, created=created, report=report)
            if pfad is not None:
                # Registered under the block the backlink went into, too:
                # without a block, the backlink opens `### HH:MM Beginn`.
                for key in {vorher, self._block_key()} - {""}:
                    self._topic_pages[key] = pfad
            return pfad

    def _block_key(self) -> str:
        """The current protocol topic block's key, "" without one."""
        try:
            lesen = getattr(self.protocol, "current_topic", None)
            key = lesen()[0] if callable(lesen) else ""
        except Exception:                 # noqa: BLE001 -- never raise
            key = ""
        return key if isinstance(key, str) else ""

    def topic_page(self) -> Path | None:
        """The page this run created for the CURRENT protocol topic block,
        None when it has none. Never raises."""
        key = self._block_key()
        return self._topic_pages.get(key) if key else None

    def _create_topic_page(self, titel, *, session_dir, project_dir,
                           mark: str, created, report) -> Path | None:
        """`file_topic_page` without the one-per-block guard; the caller
        holds `_page_lock`."""
        try:
            block = self.knowledge_link
            # Cleaned ONCE: links and page are about the same string
            # (idempotent on a `page_title()`, which is already clean).
            titel = clean_line(titel if isinstance(titel, str) else "",
                               block.title_max())
            links = block.page_links(titel, session_dir=session_dir)
            protokoll = getattr(self.protocol, "path", "") or ""
            pfad = block.create_topic_page(
                session_dir, project_dir, titel, links, mark, created,
                protokoll, report=report)
        except Exception as e:            # noqa: BLE001 -- never raise
            self.log("topic page not created:", repr(e))
            return None
        if pfad is None:
            return None
        try:
            grund = self.file_entry("notiz", f"Themenseite [[{pfad.stem}]]",
                                    mark=mark, also=(pfad.stem,))
            if grund and grund != "duplicate":
                self.log("topic page backlink not filed:", grund)
                self.trace("entry_failed", kind="notiz",
                           reason=grund.replace(" ", "_"))
        except Exception as e:            # noqa: BLE001 -- never raise
            self.log("topic page backlink not filed:", repr(e))
        try:
            self.trace("wiki_create", links=len(links))
        except Exception:                 # noqa: BLE001 -- never raise
            pass
        return pfad

    def file_unrecorded(self, entries: list[dict]) -> list[dict]:
        """File what fell off the stage as `frage` entries.

        Parked notices and expired questions (`StageState.unrecorded()`).
        The consumer owns the protocol and does it itself. Returns the
        entries that actually landed -- exactly those, and only those, may
        be marked recorded. Without a protocol nothing is filed and the
        list is empty.
        """
        if self.protocol is None:
            return []
        filed: list[dict] = []
        for entry in entries:
            prefix = ("abgelaufen: " if entry.get("kind") == "expired"
                      else "geparkt: ")
            reason = self.file_entry("frage",
                                     prefix + str(entry.get("text", "")))
            # A duplicate IS in the protocol already -- it counts as filed.
            if reason in ("", "duplicate"):
                filed.append(entry)
            else:
                self.log("entry not filed:", reason)
                self.trace("entry_failed", kind="frage",
                           reason=reason.replace(" ", "_"))
        return filed

    def _order(self, action: dict) -> tuple[str, str]:
        """(order id, marker) of a proposal answering a level-2 order;
        ("", "") when there is none or it is unknown."""
        oid = action.get("order") if isinstance(action, dict) else None
        if self.policy is None or not is_token(oid):
            return "", ""
        mark = self.policy.marker_for(oid)
        return (oid, mark) if mark else ("", "")

    def _delivered(self, oid: str) -> None:
        """Tell level 2 its order arrived. Never under `StageState._lock`:
        settling the pending writes the stage, writer lock first."""
        if not oid or self.policy is None:
            return
        try:
            self.policy.delivered(oid)
        except Exception as e:            # noqa: BLE001 -- agent thread
            self.log("policy delivered failed:", repr(e))

    def _task_filed(self, oid: str, action: dict) -> None:
        """Hand a filed `tag_entry` that answered order `oid` to level 2,
        which turns an `aufgabe` into a task (issue #129). Only a policy
        that has the `task_filed` hook gets it. Never under
        `StageState._lock`: the task goes out as a view intent and the
        stage surface writes the stage, writer lock first."""
        hook = getattr(self.policy, "task_filed", None)
        if not oid or hook is None:
            return
        try:
            hook(oid, action)
        except Exception as e:            # noqa: BLE001 -- agent thread
            self.log("policy task_filed failed:", repr(e))

    def _protocol_step(self, action: dict, mark: str = "") -> tuple | None:
        """What an ACCEPTED surface action means for the v2 protocol.

        CALLER'S DUTY: under `StageState._lock`, right after the `apply()`
        -- the title and the decision are read back off the state. Only
        reads here; the write happens in `_apply_protocol_step()`, outside
        the state's lock.

        A `topic` opens a block (the one and only trigger: the same accepted
        action that changes the stage). A `decision` becomes a `beschluss`.
        `mark` goes to the protocol only: the stage keeps the title and the
        decision as the agent wrote them.
        """
        kind = action.get("action")
        try:
            if kind == "topic":
                return ("topic", self.state.main.title, mark)
            if kind == "decision":
                last = self.state.decisions[-1]
                return ("decision", last.text, last.time, mark)
        except (AttributeError, IndexError):
            return None
        return None

    def _apply_protocol_step(self, step: tuple) -> bool:
        """Write what `_protocol_step()` read. Never raises.

        True when the protocol holds it afterwards -- a duplicate decision
        counts, it is there already."""
        try:
            if step[0] == "topic":
                # Title, then the related notes, then the marker -- fitted
                # into the title line's 120 characters by `topic_title`,
                # which never raises: a failing wiki costs the links only.
                # The protocol's folder is this run's session folder: its
                # notes are not linked, as for the topic page (page_links).
                titel, links = self.knowledge_link.topic_title(
                    step[1], step[2],
                    session_dir=Path(self.protocol.path).parent)
                if not self.protocol.open_topic(titel):
                    self.log("topic block not written")
                    self.trace("entry_failed", kind="topic",
                               reason="not_written")
                    return False
                if self.knowledge_link.has_vault:
                    self.trace("wiki_link", links=links)
                return True
            if step[0] == "decision":
                reason = self.file_entry("beschluss", step[1], stamp=step[2],
                                         mark=step[3])
                if reason and reason != "duplicate":
                    self.log("decision not filed:", reason)
                    self.trace("entry_failed", kind="beschluss",
                               reason=reason.replace(" ", "_"))
                    return False
                return True
        except Exception as e:            # noqa: BLE001 -- agent thread
            self.log("protocol step failed:", repr(e))
        return False

    def _tag_entry(self, action: dict) -> None:
        """A `tag_entry` proposal. Handled before `StageState.apply()`,
        which knows only surface actions and would call it unknown."""
        oid, mark = self._order(action)
        if self.protocol is None:
            reason = "v1 protocol"
        else:
            reason = self.file_entry(action.get("tag"), action.get("text"),
                                     stamp=action.get("time"), mark=mark)
        if reason in ("", "duplicate"):
            # A duplicate is in the protocol already: the order is answered.
            self._task_filed(oid, action)
            self._delivered(oid)
        if reason:
            self.log("proposal rejected:", reason, str(action)[:120])
            self.trace("proposal_rejected", reason=reason.replace(" ", "_"))

    @staticmethod
    def _counts_as_change(action: dict) -> int:
        """1 for an accepted action that changes the stage, else 0.

        `none` is the agent's way of saying "this section brings nothing
        new". It is accepted and correct, and it leaves the surface
        exactly as it was -- counting it would make every silent turn
        look like a change."""
        kind = action.get("action") if isinstance(action, dict) else None
        return 0 if kind == "none" else 1

    def set_status(self, state: str, *, at: str = "", count: int = 0,
                   failed: bool | None = None) -> None:
        with self._lock:
            neu = render_status(state, at=at, count=count)
            if failed is not None:
                self._failed = failed
            # `since` only moves when the STATE changes. The page counts
            # the running duration of `working` from it, and the
            # listening refresh fires on every utterance -- restamping it
            # each time would reset the counter to zero while the agent
            # is still thinking, which is precisely the number the round
            # uses to tell a thinking agent from a crashed one.
            #
            # WITH the UTC offset: the page reads it via `Date.parse`. A
            # naive timestamp is taken there as the VIEWER's local time,
            # and this round meets online -- a reader one timezone east
            # would be shown an hour of "Robo werkelt seit".
            # `StageState._label()` carries the offset for the same reason.
            if not self._since or neu["state"] != self._status.get("state"):
                self._since = (datetime.now().astimezone()
                               .isoformat(timespec="seconds"))
            self._status = neu

    def warn(self, text: str) -> None:
        with self._lock:
            self._warning = text

    def clear_warning(self, only: str = "") -> None:
        """Withdraw the warning -- or only this one.

        Several conditions raise warnings and they clear at different
        moments: the stream comes back, the conflict files are deleted, a
        successful run retires the discard note. Clearing unconditionally
        from one of those call sites used to withdraw somebody else's
        still-true warning.
        """
        with self._lock:
            if not only or self._warning == only:
                self._warning = ""

    def warning(self) -> str:
        with self._lock:
            return self._warning

    def ingest(self, proposal_path: Path | None = None,
               spoken: str = "") -> int:
        """Read what the agent appended and apply it. Returns discards.

        `spoken` is what was actually said in the run these proposals
        belong to. It is the evidence a `main` action is checked against:
        the consumer does not judge language, it demands the instruction
        verbatim AND verifies that it occurred. Without it no call-out can
        be proven, so no `main` action is accepted.
        """
        path = Path(proposal_path) if proposal_path is not None else self.proposals
        with self._lock:
            offset = self._offset
            actions, self._offset, discarded = read_proposals(path, offset)
        self.trace("proposals_read", count=len(actions), discarded=discarded)
        for action in actions:
            # INVARIANT: the apply and the read-back of what it did are ONE
            # critical section under the state's own lock. `applied_event()`
            # recovers the assigned id by reading the state back
            # (`notices[-1].id`), and that is this action's entry only while
            # no other thread can apply in between -- today one thread
            # applies, but nothing except this lock says so, and a wrong id
            # in a file whose whole value is joining ids over time is a
            # silent, unnoticeable error. The lock is reentrant, so the
            # `apply()` inside may take it again.
            #
            # LOCK ORDER (gateway/stage_state.py): `StageWriter._lock`
            # first, then `StageState._lock`. Held here is only the
            # state's lock, and nothing inside reaches back into the
            # writer's -- `self.log` and `self.trace` are left outside on
            # purpose -- so the documented order is unchanged.
            if isinstance(action, dict) and action.get("action") == "tag_entry":
                # Protocol v2: an entry for the protocol, not the surface.
                # Never under a lock of ours -- the minutes port takes its own.
                self._tag_entry(action)
                continue
            if isinstance(action, dict) and action.get("action") == "graph":
                # The payload is built OUTSIDE the state's lock: it scans
                # the vault, and holding a lock the ticker needs across a
                # file walk would stall the surface twice a second.
                fehlend = self.build_graph_action(action)
                if fehlend:
                    self.log("proposal rejected:", fehlend, str(action)[:120])
                    self.trace("proposal_rejected",
                               reason=fehlend.replace(" ", "_"))
                    continue
            # Before the state's lock: `marker_for()` takes level 2's own.
            oid, mark = self._order(action)
            with self.state._lock:        # noqa: SLF001 -- no public guard
                # The oldest decision BEFORE the action: the stage keeps
                # three, and a fourth pushes one off. That drop is one of
                # the movements section 7 counts against the pure appends.
                oldest = (self.state.decisions[0].id
                          if self.state.decisions else "")
                reason = self.state.apply(action, spoken)
                event = None if reason else applied_event(self.state, action)
                after = (self.state.decisions[0].id
                         if self.state.decisions else oldest)
                # What the v2 protocol gets from this action, read back in
                # the same critical section (the ticker may move on).
                protocol_step = (None if reason or self.protocol is None
                                 else self._protocol_step(action, mark))
            dropped = oldest if (not reason and oldest and after != oldest) else ""
            if reason:
                self.log("proposal rejected:", reason, str(action)[:120])
                # `reason` is one of a handful of fixed English words from
                # `StageState.apply()` -- "parked" among them, which is how
                # a held-back notice becomes countable. Never foreign text;
                # the space becomes an underscore because the trace takes
                # tokens only ("no text" -> "no_text", see shared/events.py).
                self.trace("proposal_rejected", reason=reason.replace(" ", "_"))
                # A parked notice IS a change of the surface: a dimmed line
                # appears and the entry goes off to the protocol. It is a
                # rejection only from the agent's side, and counting it as
                # "nothing happened" made the latency measure of section 7
                # drop exactly the runs that were busiest.
                if reason == PARKED:
                    self.applied += 1
                continue
            self.applied += self._counts_as_change(action)
            if event is not None:
                self.trace(event.pop("event"), **event)
            if dropped:
                self.trace("decision_dropped", id=dropped)
            if protocol_step is not None:
                # Outside every lock but the protocol's own (lock order:
                # nothing holding StageState._lock calls into the minutes port).
                if self._apply_protocol_step(protocol_step):
                    # Here too, outside the state's lock: delivered()
                    # settles a pending and writes the stage.
                    self._delivered(oid)
        if discarded:
            self.warn(warning_text("discarded"))
        return discarded

    def write(self) -> bool:
        """Write the stage -- but only when the content actually changed.

        The ticker calls this twice a second. Replacing the file every
        time would be ~29,000 replacements over a four-hour meeting in a
        folder that rclone bisync watches continuously -- exactly the load
        the `.conflictN` files came out of on 2026-09-07 (AGENTS.md: while
        a consumer runs, the whole project folder belongs to it, and the
        sync does not distinguish). `updated` is deliberately excluded
        from the comparison: it moves every call and would defeat the
        check. Returns True when the file was touched.

        The digest is not the only reason to write: at rest it never
        changes, so a file that DISAPPEARS underneath us -- renamed aside
        by the sync service, replaced by a foreign version, which is the
        exact scenario this design exists to survive -- would never be
        rewritten again and the page would serve a stale state for the
        rest of the meeting. The identity of the file we last wrote is
        therefore part of the guard.

        AND IT NEVER RAISES. Three threads call this, one of them the SSE
        reader, whose enclosing `except` reads any exception as a dropped
        stream: an unwritable folder, a full disk or one unencodable
        character used to send the reader into the reconnect backoff on
        every utterance, and everything spoken during those waits was lost
        from the raw transcript for good. A stage that cannot be written
        is bad; a transcript that stops is unrecoverable.
        """
        with self._lock:
            # The warning and the failed run share the SECOND line and
            # leave the operating line alone -- see stage_texts.trouble().
            data = {**self.state.content(),
                    "status": {**self._status, "since": self._since,
                               "warning": trouble_text(self._warning,
                                                       self._failed)}}
            signature = json.dumps(
                {k: v for k, v in data.items() if k != "updated"},
                ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
            if digest == self._last_hash and self._stamp() == self._last_stamp:
                return False
            try:
                write_stage(self.path, data)
            except Exception as e:        # noqa: BLE001 -- see the docstring
                self.log("stage not written:", repr(e))
                self.trace("stage_write_failed")
                # No digest is remembered: the next call tries again
                # instead of assuming this content is on disk.
                return False
            self._last_hash = digest
            self._last_stamp = self._stamp()
            # Every surface change, and only the real ones: the digest
            # guard above has already dropped the ticker's idle calls.
            self.trace("stage_written", chars=len(signature))
            return True

    def _stamp(self) -> tuple | None:
        """Identity of the stage file on disk, or None when it is gone.

        Inode and device catch the rename-aside, size and mtime catch a
        foreign write into the same inode. `os.replace()` gives every one
        of our own writes a new inode, so our own stamp always matches
        what we last published.
        """
        try:
            s = self.path.stat()
        except OSError:
            return None
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)


def restore_stage(writer: StageWriter, state: StageState, day: str,
                  log: Callable[..., None] = log) -> dict | None:
    """Take the existing `buehne.json` back over, if it is today's.

    A restart mid-meeting is the normal case after a crash, and it used to
    cost the round everything on the surface. What can be restored is
    restored; what cannot is said out loud rather than passed over:

    * no file -- a new meeting, nothing to do, no warning;
    * a file from another day -- not this meeting; ignored, and the first
      write replaces it;
    * a file from today that cannot be read or cannot be taken over --
      the round is TOLD, because the stage is now empty although the agent
      still remembers the identities that stood on it.

    Returns the restore summary, or None when nothing was taken over.
    """
    try:
        raw = writer.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        log("stage file not readable:", repr(e))
        writer.warn(warning_text("stage"))
        writer.trace("stage_restore_failed", reason="unreadable")
        return None
    try:
        data = json.loads(raw)
    except ValueError as e:
        log("stage file not parseable:", repr(e))
        writer.warn(warning_text("stage"))
        writer.trace("stage_restore_failed", reason="unparseable")
        return None
    stamp = data.get("updated") if isinstance(data, dict) else None
    if not (isinstance(stamp, str) and stamp.startswith(day)):
        log("stage file belongs to another meeting — starting fresh")
        return None
    try:
        summary = state.restore(data)
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        log("stage could not be restored:", repr(e))
        writer.warn(warning_text("stage"))
        writer.trace("stage_restore_failed", reason="rejected")
        return None
    log(f"stage taken over from the running meeting: "
        f"{summary['decisions']} decisions, {summary['notices']} notices, "
        f"{summary['question']} question, "
        f"{len(state.history)} surfaces in the history")
    writer.trace("stage_restored",
                 count=sum(summary.values()))
    return summary


# =================================================================== main
def _sigterm_to_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def install_sigterm_handler() -> None:
    """`scripts/cothinker-start.sh --stop` sends SIGTERM. Python's default
    for it is to die on the spot -- no `lanes.stop()`, no `queue.finish()`,
    no closing note, no final commit. Turned into a KeyboardInterrupt in
    the main thread (Python runs signal handlers there), it takes exactly
    the Ctrl-C shutdown path; a second SIGTERM acts like a second Ctrl-C.
    """
    signal.signal(signal.SIGTERM, _sigterm_to_keyboard_interrupt)


def final_speaker(ev) -> str | None:
    """Speaker name for a `final` event: the attributed `speaker` (e.g. the
    per-track display name) wins; the raw `recognizer_id` is the fallback.
    "default"/"mic_gate" are channel names, not people -> None."""
    spk = ev.speaker or ev.recognizer_id
    return str(spk) if spk and spk not in ("default", "mic_gate") else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", required=True, help="base URL of the STT server")
    ap.add_argument("--project", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--participants", default="")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--provider", default="custom:anthropic_plan",
                    help="empty string = profile default (careful: may land "
                         "on OpenRouter instead of the plan proxy)")
    ap.add_argument("--pause", type=float, default=2.5,
                    help="seconds of silence that end a block")
    ap.add_argument("--max-block", type=float, default=90.0,
                    help="cut here at the latest, even without a pause")
    ap.add_argument("--call-silence-s", type=float, default=8.0,
                    help="seconds of silence that close an open call window "
                         "(issue #180)")
    ap.add_argument("--call-max-s", type=float, default=90.0,
                    help="close an open call window here at the latest, "
                         "even without silence (issue #180)")
    ap.add_argument("--wake-word", default="hey robo",
                    help="Weckphrase. Mehrere durch Komma trennen; der Name "
                         "wird als WORTANFANG gesucht, damit die "
                         "Spracherkennung ihn nicht durch Anschweissen "
                         "verschluckt (gemessen 2026-09-21: 'Roboterzeuge' "
                         "fuer 'Robo, erzeuge'). Ein blosser Name ohne Gruss "
                         "feuert nicht mehr (GitHub-Issue #150: zu viele "
                         "Fehlalarme bei Gespraechen UEBER den Assistenten "
                         "und bei Verhoerern des einzelnen Worts).")
    ap.add_argument("--commit-every", type=float, default=300,
                    help="seconds between two git commits")
    ap.add_argument("--stt-timeout", type=float, default=300,
                    help="seconds without a single byte, after which the "
                         "stream is considered dead and rebuilt")
    ap.add_argument("--retry-delay", type=float, default=2.0,
                    help="base value of the wait time before the next attempt")
    ap.add_argument("--max-batch", type=int, default=MAX_BATCH_CHARS,
                    help="characters that get merged into at most ONE agent "
                         "run when there is a backlog")
    ap.add_argument("--check-every", type=float, default=60,
                    help="seconds between two checks whether raw lines have "
                         "disappeared from the protocol (0 = off)")
    ap.add_argument("--no-agent", action="store_true",
                    help="only transcribe, don't trigger an agent")
    ap.add_argument("--dry-agent", action="store_true",
                    help="only log the agent run, don't execute it")
    ap.add_argument("--worker-command", default="",
                    help="command that starts the agent worker; empty = "
                         "python3 gateway/worker.py with the interpreter "
                         "found next to `hermes`")
    ap.add_argument("--vault", default=os.environ.get("COTHINKER_VAULT", ""),
                    help="vault root for the graph surface (the folder the "
                         "directory symlinks live in). Without it the graph "
                         "surface stays off.")
    ap.add_argument("--vault-include-network", action="store_true",
                    help="also graph the person-related notes under "
                         "*/network/*. Off by default, deliberately.")
    ap.add_argument("--correct", action="store_true",
                    help="smooth every final with an Infomaniak model and "
                         "write it to transkript-korrigiert.md. DISPLAY "
                         "ONLY: the raw transcript, the agent and the stage "
                         "never see the corrected text. Off by default. "
                         "Needs INFOMANIAK_API_KEY.")
    ap.add_argument("--prefilter", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="strip filler words and stutter repeats BY RULE "
                         "from every final and write it to "
                         "transkript-korrigiert.md. DISPLAY ONLY, same as "
                         "--correct: the raw transcript, the agent and the "
                         "stage never see the filtered text. No model call "
                         "(gateway/correction_prefilter.py). On by default "
                         "for the demo profile; ignored while --correct is "
                         "on, since both target the same file. "
                         "--no-prefilter turns it off.")
    ap.add_argument("--speakers", action="store_true",
                    help="voice-embedding speaker enrollment: "
                         "an intro round ('Hey Robo, "
                         "Vorstellungsrunde' ... 'Hey Robo, Vorstellungsrunde "
                         "fertig') binds each participant's voice to their "
                         "name; afterwards every utterance is attributed "
                         "by cosine similarity instead of the recognizer "
                         "id, in protokoll.md and the agent input alike. "
                         "Needs SPEAKER_EMBED_URL (env; SPEAKER_EMBED_SECRET "
                         "for the shared-secret header) and --session-wav. "
                         "Off by default -- with it unset the meeting runs "
                         "byte for byte as before. stt/pipeline/speaker_labels/enroll.py.")
    ap.add_argument("--session-wav", default="",
                    help="16 kHz mono WAV of the full session recording "
                         "(--speakers only): each utterance's clip is cut "
                         "from it by its t_start/t_end audio-clock span "
                         "before it goes to the embedding service. Without "
                         "it, or without a usable span (older STT server), "
                         "speaker resolution stays off for that utterance.")
    ap.add_argument("--speaker-threshold", type=float,
                    default=SPEAKER_THRESHOLD_DEFAULT,
                    help="cosine similarity below this resolves to "
                         f"'unbekannt' (--speakers only; default "
                         f"{SPEAKER_THRESHOLD_DEFAULT})")
    ap.add_argument("--speaker-labels", action="store_true",
                    help="name a Speechmatics diarization label (S1, S2, "
                         "...) from a casual intro round: "
                         "RULE stage (first-person patterns, restricted to "
                         "--participants) runs always, needs no audio and "
                         "no SPEAKER_EMBED_URL -- independent of --speakers' "
                         "voice-embedding path. The async LLM candidate "
                         "stage (hand-overs, third-person introductions) "
                         "additionally needs INFOMANIAK_API_KEY; without "
                         "it, rule stage only. A resolved name replaces the "
                         "recognizer id the same way --speakers' resolution "
                         "does, unless --speakers already resolved one. Off "
                         "by default. stt/pipeline/speaker_labels/label.py.")
    ap.add_argument("--mode", choices=MODES, default=DEFAULT_MODE,
                    help="meeting mode of a NEW protocol (format v2): picks "
                         "the sections of the derived overview. An existing "
                         "protocol keeps its format.")
    ap.add_argument("--policy", action="store_true",
                    help="level 2 (docs/entwurf-meeting-modi.md §3): act on "
                         "the level-1 decider's answers -- topic blocks, "
                         "tagged entries, one yes/no question at a time. "
                         "Needs COTHINKER_DECIDER, a v2 protocol and an "
                         "agent. Off by default.")
    ap.add_argument("--thought-end", choices=("off", "combo"), default="off",
                    help="PARALLEL LANE ONLY: score every final with the "
                         "bench's combo detector (smart-turn on audio from "
                         "the STT server's thought_end events, Laya on text "
                         "here) and write the numbers to thought-end.jsonl. "
                         "Never cuts a block. Off by default.")
    ap.add_argument("--thought-end-threshold", type=float,
                    default=THOUGHT_END_THRESHOLD,
                    help="p_combo at or above this counts as a cut in the "
                         "trace (default: the bench's held-out 0.44)")
    ap.add_argument("--thought-end-laya", default="",
                    help="command that starts the Laya thought-end worker; "
                         "empty = COTHINKER_LAYA_PYTHON "
                         "gateway/thought_end_laya_worker.py --model "
                         "COTHINKER_LAYA_MODEL")
    ap.add_argument("--agenda", default="",
                    help="text file with the agenda, one item per line: "
                         "written as `## Ablauf` at the top of a NEW "
                         "protocol (the first section of the right "
                         "column). An existing protocol is left as it is.")
    ap.add_argument("--lanes", action="store_true",
                    help="konzept-spuren-architektur.md (2026-10-20): "
                         "calls get an instant non-LLM acknowledgement and "
                         "their own agent lane; a dedicated coach lane "
                         "contributes at most once per confirmed topic. "
                         "Off by default -- without it everything runs "
                         "exactly as today's serial variant C.")
    ap.add_argument("--lane-call-worker-command", default="",
                    help="overrides the call lane's worker command; empty "
                         "builds one from --model/--provider against "
                         "~/.hermes/profiles/cothinker (same profile as "
                         "the serial path)")
    ap.add_argument("--speaker-split", action="store_true",
                    help="--speakers only: an utterance >= 4 s is embedded "
                         "in 1.5 s windows; a speaker change inside it "
                         "splits it into one line per speaker turn, in "
                         "protokoll.md and the agent/lane input alike. "
                         "3 s budget per utterance, on timeout or error "
                         "the single name stays. Off by default. "
                         "stt/pipeline/speaker_labels/split.py.")
    ap.add_argument("--spool-dir", default="~/.local/state/cothinker/spool",
                    help="where the stage spools participant actions "
                         "(<spool>/<session-folder>/actions.jsonl); must "
                         "match the stage's --spool-dir")
    a = ap.parse_args()
    install_sigterm_handler()
    if os.environ.get("COTHINKER_COACH_RHYTHM") is not None:
        log("warning: COTHINKER_COACH_RHYTHM is no longer read; set "
            "`coach_rhythm = true` under `[[blocks]] id = \"mirroring-coach\"` "
            "in the template instead")

    try:
        agenda = read_agenda(a.agenda)
    except (OSError, UnicodeDecodeError) as e:
        raise SystemExit(f"agenda not readable: {a.agenda} "
                         f"({type(e).__name__})")

    folder, entity, pslug = resolve_project(a.project)
    day = date.today().isoformat()
    title = a.title or f"Live-Mitschrift {datetime.now():%d.%m.%Y %H:%M}"
    # ONE FOLDER PER MEETING. A project folder collects meetings over
    # months; flat, four meetings' protocols, proposal files and event
    # traces lie interleaved between the project's own documents and only
    # the date suffix ties a run together. In
    # `sessions/<YYYY-MM-DD>-<slug>/` a meeting is one folder, and the
    # README and the summary a human writes afterwards have a place next
    # to the machine's files. A flat run of the same day that is already
    # on disk is continued where it lies -- see `session_paths`.
    #
    # Without a `--title`, the project's own slug names the folder. A bare
    # `<date>-termin` says nothing a listing does not already show, and the
    # generated title carries the date twice over.
    orte = session_paths(folder, day, a.title, a.file, fallback=slugify(pslug, "termin"))
    run_dir = orte["folder"]
    path = orte["protocol"]
    if not orte["flat"]:
        run_dir.mkdir(parents=True, exist_ok=True)
    write_session_pointer(run_dir)
    # Restart window: marker + the sync's own lock until the stage is back
    # on screen (card MM-O, feedback-2026-09-26 pt 9). Released again just
    # after `restore_stage`; taken once more for the shutdown writes.
    sync_window = SyncWindow(run_dir, log=log)
    sync_window.acquire()
    if not path.exists():
        # New protocols are in the current format (topic blocks, tagged
        # entries, derived view).
        atomic_write_text(path, protocol_header(
            title=title, entity=entity, day=day, pslug=pslug,
            participants=", ".join(p.strip() for p in a.participants.split(",") if p.strip()),
            start=datetime.now().strftime("%H:%M"), mode=a.mode, agenda=agenda))
        log("created:", path)
    else:
        # An existing file of the old format is continued in the current
        # one below a dated marker line, never rewritten.
        try:
            if continue_protocol(path, datetime.now(), a.mode):
                log("old-format protocol continued in format 2:", path)
        except (OSError, ValueError) as e:
            log("protocol not continued in format 2:", repr(e))
        log("appending to:", path)

    # The stage: the consumer holds the state and is its only writer. The
    # agent never touches buehne.json again -- it appends proposals to the
    # proposal file and this process decides what reaches the surface.
    #
    # ONE PROPOSAL FILE PER MEETING, named after the same day as the
    # protocol so the two line up in the folder. A single shared
    # `vorschlag.jsonl` is never rotated, so the first run of the NEXT
    # meeting read yesterday's file from byte 0 and put yesterday's
    # mirror, decisions and question on today's stage, stamped as raised
    # now -- and the stale question then held the single question slot
    # for ten minutes while today's real notices were parked.
    proposals_path = orte["proposals"]
    proposals_path.touch(exist_ok=True)
    # The structural event trace, next to the protocol
    # (docs/stage-methodology.md section 7). It answers, after the meeting,
    # whether the stage was read and whether it stayed quiet -- from
    # structure alone: type, identity, timestamps, duration, outcome. The
    # stage never serves it: the file view only ever finds `*.md`.
    #
    # ONE FILE PER DAY, named like the protocol and the proposals, because
    # `meeting_ended` is written only on the clean shutdown path: a crash
    # or a power cut leaves a run without its terminator, and a consumer
    # restarted the same evening used to append a second `meeting_started`
    # to the same file with nothing tying the halves together. The unit of
    # comparison in section 7 is the meeting, and after a crash the date is
    # the only marker that still identifies it. `meeting_started` and
    # `meeting_ended` stay: within one day they still separate the restarts.
    trace = EventLog(orte["events"])
    # A separate, much simpler per-utterance timing trace, next to the
    # protocol and the event trace -- not the same class, not the same
    # purpose: this one never looks at meaning or structure, only at
    # when an utterance arrived, how long it was and whether it closed
    # a block. See gateway/timing.py.
    timing = TimingLog(orte["timing"])
    # The correction stage, off unless asked for (spec 8.2: the live
    # default is not switched on a plan, it is switched by the operator on
    # numbers). Everything about it is display-only: it writes its own
    # two files and is never read back by anything in this process.
    korrektur = None
    if a.correct:
        if not infomaniak_key():
            log("correction off: INFOMANIAK_API_KEY is not set")
        else:
            glossar = build_glossary(ENTITIES, a.participants)
            korrektur = CorrectionWorker(
                Corrector(InfomaniakClient(), glossar, log=log),
                orte["correction"], orte["correction_trace"], log=log)
            korrektur.start()
            log(f"correction on: {len(glossar)} glossary entries, "
                f"chain {' -> '.join(MODEL_CHAIN)}")
    # The rule-based prefilter: a second, independent
    # switch -- no model, nothing to turn off for lack of a key. Only
    # started while --correct is off: both target transkript-korrigiert.md,
    # and AGENTS.md's one-writer rule holds here too -- two writers into
    # the same path, even from the same process, is exactly the
    # .conflictN case the finding of 07.09.2026 describes.
    vorfilter = a.prefilter and korrektur is None
    if a.prefilter and korrektur is not None:
        log("prefilter skipped: --correct already writes "
            "transkript-korrigiert.md")
    elif vorfilter:
        log("prefilter on: rule-based, no model")
    # Speaker enrollment, off unless asked for. Unlike
    # correction above, this one IS read back: a resolved name replaces
    # the recognizer id in the raw line below and in the text handed to
    # the block builder. Everything about it degrades to today's
    # behaviour -- unchanged `spk` -- the instant something it needs is
    # missing; a meeting must never depend on a GPU service staying up.
    enrollment = None
    if a.speakers:
        embed_url = os.environ.get("SPEAKER_EMBED_URL", "")
        if not embed_url:
            log("speakers off: SPEAKER_EMBED_URL is not set")
        else:
            from stt.pipeline.speaker_labels.enroll import (
                EmbeddingClient, EnrollmentSession, SpeakerEnrollment)
            speaker_store = SpeakerEnrollment(threshold=a.speaker_threshold,
                                              store_path=orte["speakers"],
                                              log=log)
            if speaker_store.names():
                log(f"speakers reloaded: {len(speaker_store.names())}")
            enrollment = EnrollmentSession(
                speaker_store,
                EmbeddingClient(embed_url,
                                os.environ.get("SPEAKER_EMBED_SECRET", "")),
                wav_path=(a.session_wav or None), log=log)
            if not a.session_wav:
                log("speakers on without --session-wav: resolution stays "
                    "off until a session recording is configured")
            log(f"speakers on: embedding service {embed_url}, "
                f"threshold {a.speaker_threshold}")
    # --speaker-split (default off): needs the enrollment above.
    splitter = None
    if a.speaker_split:
        if enrollment is None:
            log("speaker split off: needs --speakers with SPEAKER_EMBED_URL")
        else:
            from stt.pipeline.speaker_labels.split import SpeakerSplitter
            splitter = SpeakerSplitter(
                enrollment.store, client=enrollment.embed_client,
                wav_path=enrollment.wav_path, log=log)
            log("speaker split on: 1.5 s windows, 3 s budget per utterance")
    # The thought-end lane, off unless asked for. It writes one numbers-
    # only row per utterance into its own file and is never read back:
    # the pause cut stays the only cut (plan 2026-09-26-thought-end-
    # parallelspur). Laya starts lazily on the lane's own thread -- it
    # loads for up to 16 s, and the SSE connect must not wait for it.
    gedankenende = None
    if a.thought_end == "combo":
        laya_argv = (shlex.split(a.thought_end_laya) if a.thought_end_laya
                     else default_laya_argv())
        if laya_argv is None:
            log("thought end: COTHINKER_LAYA_PYTHON / COTHINKER_LAYA_MODEL "
                "not set -- smart-turn half only")
        gedankenende = ThoughtEndLane(orte["thought_end"], laya_argv,
                                      threshold=a.thought_end_threshold,
                                      log=log)
        gedankenende.start()
        log(f"thought end on (parallel lane, never cuts): threshold "
            f"{a.thought_end_threshold:.2f}")
    state = StageState()
    writer = StageWriter(run_dir, state, proposals=proposals_path, events=trace)
    # The built-in agenda + voice-command element: on only when this
    # session's own folder has an `agenda.md`. Off (None) costs nothing --
    # the payload shape is unchanged for every session without one. Built
    # BEFORE the block host: its items become the timer block's config and
    # it taps the timer / speaker-queue view intents for the stage.
    agenda_ctrl = build_agenda_controller(run_dir, a.participants, log=log)
    # Diarization-label attribution: names a Speechmatics
    # label (S1, S2, ...) from what is said, independent of --speakers'
    # voice-embedding path above (no audio, no SPEAKER_EMBED_URL needed).
    speaker_labels = None
    if a.speaker_labels:
        from stt.pipeline.speaker_labels.label import (
            AttributionWindow, LabelAttribution, notice_text,
            rule_attribute, should_fire_llm)
        label_participants = [p.strip() for p in a.participants.split(",")
                              if p.strip()]
        label_attrib = LabelAttribution()
        label_window = AttributionWindow()
        label_worker = None
        if infomaniak_key():
            from stt.pipeline.speaker_labels.label_llm import (
                CandidateJudge, CandidateWorker)

            def _on_speaker_label_verdict(verdict) -> None:
                # Runs on the worker's own thread -- StageState.apply()
                # takes its own lock, same as every other caller.
                if verdict.status != "ok":
                    return
                try:
                    outcome = label_attrib.apply_llm_verdict(
                        verdict.label, verdict.name, verdict.confidence)
                    if outcome not in ("conflict", "suggestion"):
                        return
                    pending = label_attrib.pending[-1]
                    state.apply({"action": "notice", "kind": "speaker_conflict",
                                "text": notice_text(pending)})
                    writer.write()
                    log(f"speaker labels: {outcome} for {verdict.label} "
                        f"-> {verdict.name} ({verdict.confidence})")
                except Exception as e:         # noqa: BLE001
                    log("speaker label verdict skipped:", repr(e))

            label_worker = CandidateWorker(
                on_verdict=_on_speaker_label_verdict,
                judge=CandidateJudge(InfomaniakClient()), log=log)
            log("speaker labels on: rule stage + async LLM candidate stage")
        else:
            log("speaker labels on: rule stage only "
                "(INFOMANIAK_API_KEY not set, no LLM candidate stage)")
        speaker_labels = SimpleNamespace(
            attrib=label_attrib, window=label_window, worker=label_worker,
            participants=label_participants)
    if a.vault:
        writer.attach_vault(a.vault, session_dir=run_dir,
                            datum=day, teilnehmende=a.participants,
                            include_network=a.vault_include_network)
    if agenda_ctrl is not None:
        state.agenda = agenda_ctrl.payload()
        log("agenda on:", orte["agenda"].name,
            f"({len(agenda_ctrl.doc.items)} items)")
    trace.record("meeting_started")
    # A RESTART IS NOT A NEW MEETING. Publishing a fresh state here blanked
    # the main area, the decisions, the notices and the question in front
    # of the round -- while the worker re-attaches to the same agent
    # session and still remembers the old identities, so a stale `resolve`
    # could settle a different entry than the one it means. What belongs to
    # today is taken back over, and the identity counters are lifted above
    # every id the file mentions.
    restored_stage = restore_stage(writer, state, day)
    if restored_stage is None:
        state.apply({"action": "mirror", "text": "Robo hört mit."})
    writer.set_status("listening", at=datetime.now().strftime("%H:%M:%S"))
    sync_window.release_lock()
    session_id = make_session_id(pslug, datetime.now())

    def protocol_report(ok: bool) -> None:
        """A protocol write that failed is the round's business.

        The raw transcript is the one thing that cannot be reconstructed.
        A full disk or a read-only mount used to produce a line on stdout
        and, a minute later, "🧵 N Rohzeilen nachgetragen" -- a foreign
        writer that does not exist.
        """
        if not ok:
            writer.warn(warning_text("protocol"))
        else:
            writer.clear_warning(warning_text("protocol"))

    # konzept-spuren-architektur.md (2026-10-20), --lanes: additive only --
    # with the flag off, `lanes` stays None and nothing below in this
    # function ever looks at it, so the serial path above is byte for
    # byte unaffected. See gateway/call_lane.py for what it does.
    lanes = None
    coach_agent_for = None
    # Point 7 (2026-10-20): the --speakers resolution is per utterance,
    # but lanes see whole blocks -- this carries the last resolved name
    # (or "unbekannt") forward to block_done(). Stays "unbekannt" for
    # good when --speakers is off: an internal recognizer id ("0",
    # "left") would be meaningless to a lane agent, "unbekannt" is not.
    letzter_sprecher = ["unbekannt"]
    if a.lanes:
        def _default_lane_command(hermes_home: Path) -> list:
            # Reuse --worker-command when given (it names the interpreter
            # that can import Hermes -- live 2026-10-20 13:32: the guessed
            # interpreter could not, every lane worker failed to start),
            # swapping only its --hermes-home for this lane's profile.
            # Always carries --no-history regardless of path:
            # a lane's own prompt already carries the full
            # bounded window, so the worker must never ALSO replay its
            # ever-growing per-session store on top of it.
            if a.worker_command:
                parts = shlex.split(a.worker_command)
                if "--hermes-home" in parts:
                    i = parts.index("--hermes-home")
                    if i + 1 < len(parts):
                        parts[i + 1] = str(hermes_home)
                else:
                    parts += ["--hermes-home", str(hermes_home)]
                if "--no-history" not in parts:
                    parts.append("--no-history")
                return parts
            return default_lane_command(
                python_exe=worker_interpreter(),
                worker_py=Path(__file__).resolve().parent / "worker.py",
                model=a.model, provider=a.provider, hermes_home=hermes_home)

        call_command = (shlex.split(a.lane_call_worker_command)
                        if a.lane_call_worker_command else
                        _default_lane_command(
                            Path.home() / ".hermes/profiles/cothinker"))
        # The coach lane is the mirroring-coach block (ADR 0010): its
        # worker command comes from the block's configuration, its agent
        # (a LanePort) from the block host wiring.
        coach_cfg = coach_block_config()
        coach_command = (shlex.split(coach_cfg["lane_coach_worker_command"])
                         if coach_cfg["lane_coach_worker_command"] else
                         _default_lane_command(
                             Path.home() / ".hermes/profiles/cothinker-coach"))
        seam = agent_seam(log)
        coach_agent_for = make_agent_for(
            coach_command=coach_command, session_id=session_id,
            lane_factory=seam[0] if seam else None,
            coach_briefing=seam[1] if seam else "", log=log)
    # The session host's block host (ADR 0008, issue #38): built from the
    # Reflect template (ADR 0010), fed every session event below. Every
    # change of its current view intents renders through the stage surface
    # (ADR 0006, issue #46) onto this same StageWriter -- the consumer
    # stays the only writer of buehne.json. The call lane sees the coach's
    # mirror lines through the same board.
    stage_surface = StageSurface(writer, state, log,
                                 coach_history_path=run_dir / "coach-history.jsonl")
    session_blocks_cell: list = []

    def block_sink(intent) -> None:
        # The agenda controller takes the timer / speaker-queue intents
        # first: it owns the round list (`people`, `speaker`) the timer
        # intent does not carry, then the surface renders the intent.
        if agenda_ctrl is not None and agenda_ctrl.observe(intent):
            state.agenda = agenda_ctrl.payload()
        # The snapshot, not just `intent`: render() needs the whole board
        # to notice what has left it since the last call. `intent` itself
        # is added back in when missing: a settle's own intent expires at
        # once (IntentWriter.settle, blocks/decisions_tasks_questions), so
        # by the time `current()` runs it may already be gone from the
        # board -- the row that applies it must still see it this once.
        #
        # Blocks emit from more than one thread (a decider's `on_result`,
        # an agent turn's take_turn/end_turn, the SSE reader, the ticker):
        # reading the snapshot and rendering it must be ONE step under the
        # surface's own lock (an RLock; render() re-enters it on the same
        # thread harmlessly). Without this, a slower thread's snapshot --
        # taken before a pending that a faster thread already announced
        # AND rendered -- can still apply after it, and render()'s
        # `_clear_gone` then reads "that pending left the board" from a
        # snapshot that never had it, forgets it was ever seen, and the
        # next occurrence announces a SECOND real pending for the same
        # order. The first one never settles and freezes into the final
        # stage file (issue #184).
        with stage_surface._lock:                       # noqa: SLF001 -- lock order
            if session_blocks_cell:
                current = {i.id: i for i in session_blocks_cell[0].intents.current()}
                current.setdefault(intent.id, intent)
                stage_surface.render(list(current.values()))
        if lanes is not None:
            lanes.observe_intent(intent)

    session_blocks = build_session_blocks(
        session_dir=run_dir,
        sink=block_sink,
        log=log,
        trace=lambda event, fields: trace.record(event, **fields),
        agent_for=coach_agent_for,
        block_configs=agenda_ctrl.block_configs() if agenda_ctrl else None,
    )
    session_blocks_cell.append(session_blocks)
    stage_surface.render(session_blocks.intents.current())
    if agenda_ctrl is not None:
        agenda_ctrl.attach(session_blocks)
        state.agenda = agenda_ctrl.payload()
    # Participant taps the stage spooled (issue #47). The stage names the
    # spool folder after the folder holding the buehne.json it shows, which
    # is this run's folder. The folder name must equal the stage's
    # `stage_file().parent.name`.
    actions_reader = ActionsReader(
        Path(a.spool_dir).expanduser() / run_dir.name / "actions.jsonl",
        dispatcher_for(session_blocks), log)
    minutes_file = MinutesFile(path, threading.RLock(), report=protocol_report)
    minutes_doc = MinutesPort(minutes_file, session_blocks, session_dir=run_dir,
                              mode=a.mode)
    # With COTHINKER_PROJECT_DIR the project is its own git repo (a plain
    # clone outside $COTHINKER_ENTITIES): git must run there, not in
    # ENTITIES.parent, or every commit -- the shutdown one included -- fails.
    session_git = SessionGit(
        path,
        (folder if os.environ.get("COTHINKER_PROJECT_DIR", "").strip()
         else ENTITIES.parent),
        commit_scope=project_commit_scope(folder, run_dir))
    # Where to look for sync conflict copies. The protocol's own folder
    # always; the project folder as well once the protocol sits one
    # level down in `sessions/<YYYY-MM-DD>-<slug>/`, because rclone
    # bisync writes its `.conflictN` copies wherever the second writer
    # touched a file -- and the round's documents, the ones a parallel
    # session is most likely to edit, are still in the project folder.
    # Looking only next to the protocol would have made the warning
    # blind to exactly the case AGENTS.md documents.
    conflict_dirs = [run_dir] if orte["flat"] else [run_dir, folder]
    # The consumer files the protocol's entries itself (format 1 removed,
    # issue #49): v2 protocol is the only path now.
    writer.protocol = minutes_doc
    # The decisions-tasks-questions block owns the level-1 decider (OFF
    # unless COTHINKER_DECIDER is set -- no surprise network call in a
    # running meeting; a backend that cannot be built is one log line) and,
    # with `--policy`, level 2 (docs/entwurf-meeting-modi.md §3): opt-in,
    # and only where every part it relies on is there -- the decider's
    # answers, a protocol the consumer files into, an agent to formulate
    # the orders. Anything missing is one log line and the meeting runs
    # as before. `minutes_doc.mode` is the mode the existing file was
    # created with, not the flag's: the policy table must match it.
    decisions = session_blocks.get("decisions-tasks-questions")
    decisions.attach(
        writer, decisions_path=orte["decisions"], session_dir=run_dir,
        project_dir=folder, events=trace, log=log, policy=a.policy,
        no_agent=a.no_agent, mode=minutes_doc.mode,
        wants_page=writer.knowledge_link.wants_page,
        order_note_for=order_note_for)
    if writer.protocol is not None:
        kl_block = session_blocks.get("knowledge-link")
        if kl_block is not None:
            writer.knowledge_link = kl_block
        else:
            log("knowledge-link block not in the template: default instance")
        writer.knowledge_link.use_project(folder)

    def refresh_conflicts() -> bool:
        """Raise or withdraw the conflict warning from the FACTS on disk.

        Checked repeatedly, not once at startup: conflict files do not
        remove themselves, so clearing the warning after a successful run
        told the round the problem was over while it was not -- and the
        realistic case, conflict files appearing DURING the meeting, was
        never announced at all. Only this warning is withdrawn here: the
        discard warning is re-raised by the ingest that follows the
        clear, and the restored-lines warning is a past event.
        """
        try:
            files = find_conflicts(conflict_dirs)
        except OSError as e:
            log("conflict check skipped:", repr(e))
            return False
        if files:
            writer.warn(warning_text("conflicts"))
            return True
        writer.clear_warning(warning_text("conflicts"))
        return False

    conflicts_seen = [refresh_conflicts()]
    if conflicts_seen[0]:
        log("warning: conflict files in the project folder:",
            ", ".join(p.name for p in find_conflicts(conflict_dirs)[:5]))
        log("    A sync service is writing too. Better switch it off during the meeting.")
    writer.write()
    if (not a.no_agent and not a.dry_agent and not a.worker_command
            and shutil.which("hermes") is None):
        log("warning: 'hermes' is not in PATH — the worker will start under "
            f"{sys.executable}, which most likely cannot import Hermes.")
    session_git.git(f"Live-Mitschrift begonnen: {title}")

    # One long-lived agent per meeting. It is started lazily, on the first
    # turn: the round should be transcribed from the first second, and a
    # worker that takes its time booting must not delay the SSE connect.
    worker = WorkerClient(
        # shlex, not str.split(): the interpreter or the worker script can
        # sit in a path with a space in it, and splitting on whitespace
        # turns that into two arguments and a worker that never starts.
        command=(shlex.split(a.worker_command) if a.worker_command
                 else [worker_interpreter(),
                       str(Path(__file__).resolve().parent / "worker.py"),
                       "--model", a.model, "--provider", a.provider]),
        session_id=session_id, log=log)
    log("agent session:", session_id)

    if a.lanes:
        # Point 7 (2026-10-20): participants belong in the STABLE part of
        # the prompt (same cache segment as profile/tools), set once here
        # -- see `project_agenda_text()` below for the ONE later change
        # this prefix is allowed, when enrolled speaker names replace the
        # bare participant list.
        project_agenda = project_agenda_text(title, a.participants, agenda)

        lanes = build_lanes(
            a, log, call_command=call_command,
            session_id=session_id, project_agenda=project_agenda,
            state=state, writer=writer, folder=folder, path=path,
            seam=seam)
        lanes.attach(session_blocks.dispatch)
        if coach_cfg["coach_rhythm"]:
            log("coach rhythm on: start / end / long pause")
        call_started = lanes.start()
        log(f"lanes on: call started={call_started}")
        # Confirmed topic changes come from the `topics` block (issue #39):
        # the block's `topic` output reaches the call lane through the host
        # listener; the decisions-tasks-questions block's hook below is
        # what feeds the block. The call lane's own re-announcement
        # (`from` = host) is not a new topic.
        session_blocks.on_output(call_lane_mod.topic_listener(lanes.on_topic))
        if decisions.decider_on:
            # Confirmed topic changes come from the `topics` block
            # (issue #39): the block's `topic` output reaches the call
            # lane through the host listener; this hook is what feeds
            # the block.
            def _lanes_feeds_topics(result, snap, reason) -> None:
                by_id = result.by_id()
                previous = list(getattr(snap, "previous", None) or [])
                block_text = str(getattr(snap, "block", "") or "")
                lanes.note_decider_block(block_text)
                session_blocks.dispatch("block_output", {"decider_result": {
                    "neues_thema": (by_id.get("neues_thema") or {}).get("p"),
                    "rueckkehr_thema":
                        (by_id.get("rueckkehr_thema") or {}).get("p"),
                    "block": block_text,
                    "previous": str(previous[-1]) if previous else "",
                }})

            decisions.add_result_hook(_lanes_feeds_topics)
        else:
            log("lanes: coach gets no topic signal -- needs COTHINKER_DECIDER")

    # B5: the FULL AGENT_PROMPT only has to go out once per worker
    # session -- after that the worker's own conversation history (kept
    # by Hermes, re-read from state.db before every turn, see
    # worker.py::load_history) already carries the instruction, the
    # paths and the proposal format. One flag per consumer run, flipped
    # the moment the first turn's prompt is built: a fresh consumer
    # process always gets a fresh `session_id` (make_session_id uses
    # `datetime.now()`), so a restart -- crash recovery included --
    # naturally starts a new worker session and needs the full prompt
    # again. Never reset back to True while this process lives.
    first_turn = [True]
    turn_no = [0]
    replies = ReplyIntake(session_blocks)
    failed_replies_path = orte["events"].parent / f"reply-failures-{day}.jsonl"

    stopp = threading.Event()
    # A turn is in flight. The queue cannot answer this: next_batch() pops
    # the block BEFORE the run, so backlog() is 0 for the whole 40-180 s
    # turn -- and the listening refresh of every utterance overwrote
    # "⚙️ Robo werkelt" with "🎧 hört zu". The round then lost exactly the
    # signal that tells a thinking agent from a crashed one, and the
    # duration on the page never started ticking.
    turn_running = threading.Event()
    last_run_failed = threading.Event()

    def publish_status() -> None:
        """Decide and write the operating line -- in ONE place.

        Every caller states the facts it knows; the precedence
        (working > backlog > listening) lives in the status-line block's
        pick() (blocks/status_line/line.py) so two call sites can never
        disagree about who wins. This function only reports the facts as a
        `tick` to the block host; the block turns them into the winning
        state and -- only when it changes -- a view intent that
        `gateway.stage_surface.StageSurface` applies to `writer` via
        `writer.set_status()`. The listening refresh therefore keeps
        running while nothing is in flight, including under --no-agent and
        --dry-agent, where the acknowledgement is the only thing the stage
        has to offer.

        A FAILED run does not take this line. It goes to the second one,
        beside the warnings: for 40-180 s after every failure the round
        was left without any answer to "did you hear me", and when the
        worker could not start at all the line alternated between failure
        and working for the whole meeting.

        AND IT NEVER RAISES. It is called from the SSE reader, whose
        enclosing `except` treats an exception as a dropped stream and
        goes into a reconnect backoff -- during which everything spoken is
        lost from the raw transcript.
        """
        try:
            waiting = 0 if a.no_agent else queue.backlog()
            session_blocks.dispatch("tick", {
                "components": {
                    "queue_backlog": waiting,
                    "turn_running": turn_running.is_set(),
                    "run_failed": last_run_failed.is_set(),
                },
            })
            writer.write()
        except Exception as e:            # noqa: BLE001 -- see the docstring
            log("operating line skipped:", repr(e))

    def block_kind(b: Block) -> str:
        """How the block came about, for the trace: `order` when the round
        addressed the agent, otherwise the cutting reason."""
        return "order" if b.wake_word else (b.reason or "unknown")

    # --lanes: every turn of the serial worker is an orders turn (ADR 0026,
    # `orders_turn_hook` is the only producer of its blocks). False on the
    # serial path, where run_turn is unchanged.
    orders_only = bool(a.lanes)

    def run_turn(b: Block) -> bool:
        """One agent turn: prompt, worker, proposals, stage.

        The only caller is `RunQueue`, one turn at a time.
        """
        marke = "ORDER" if b.wake_word else b.reason
        log(f"agent ({marke}, {b.chars} chars) …")
        trace.record("run_started", kind=block_kind(b), chars=b.chars)
        turn_running.set()
        # The failure notice is NOT cleared here. It stands until a run
        # actually succeeds: cleared at the start of every turn, it
        # flickered on and off for the whole meeting whenever the worker
        # could not start at all, and it said "fixed" before anything was.
        publish_status()
        # What the agent still owes the protocol: parked notices and
        # expired questions. Read BEFORE the prompt is built, so only what
        # this run was really told about is ticked off afterwards.
        unrecorded = state.unrecorded()
        # The consumer owns the protocol, so it files what fell off the
        # stage itself -- now, independent of how the run goes -- and marks
        # exactly those that landed. The prompt does not mention them.
        file_names = prepare_turn(writer, state, folder, unrecorded, log)
        unrecorded = []
        turn_no[0] += 1
        # A merged block (backlog) and a carry-over need different
        # instructions than a normal section: the agent should know it is
        # behind and orient itself on what was said LAST.
        #
        # B5: the full AGENT_PROMPT goes out only for the very first turn
        # of this worker session. `first_turn[0]` flips to False only
        # AFTER a successful run (below) -- not here, before the call: a
        # worker that never started (worker.run() returning "worker could
        # not be started") sent nothing to Hermes, so nothing landed in
        # the conversation history either. Flipping early would have the
        # next turn assume the instruction is already in a history that
        # was in fact never written -- an agent that then only sees a
        # spoken block and no protocol path, no proposal format, no
        # rules.
        dieser_turn_ist_erster = first_turn[0]
        t0 = time.monotonic()
        gelungen = False
        # What the surface looked like before the turn. `applied` counts
        # the accepted actions that moved it; `main.id` says whether THIS
        # call-out ended in a visible result.
        vorher_angewandt = writer.applied
        vorher_flaeche = state.main.id
        vorher_spiegel = state.mirror
        vorher_zeile = state.headline
        # The announcement of THIS call-out, and only this one: another
        # call-out may already be announced and waiting in the queue for
        # its own run. See `CallOutAnnouncements`.
        own_announcement = call_outs.take(b) if b.wake_word else ""
        veraendert = False
        try:
            # Level 2 (MM-B): orders decided by the policy since the last
            # turn ride along with this one -- finished, the agent only
            # formulates. Whatever of them brings no proposal is settled by
            # end_turn() in the `finally` below -- inside the `try`, so
            # that holds even when building the prompt raises: the orders
            # then fail with THIS turn, not the next one.
            # MM-G D7: "Robo, leg dafuer eine Seite an" creates the topic
            # page BEFORE turn_orders, so level 2's fill order rides
            # along with this very turn.
            orders = turn_opened(session_blocks, b, turn_no[0])
            extra_note, gesprochen = orders["note"], orders["spoken"]
            prompt = build_prompt(
                minutes_file.path, b, state,
                backlog_note=(BACKLOG_NOTE if b.reason in ("batch", "carry")
                              else ""),
                order_note=order_note_for(b.wake_word) + extra_note,
                project_dir=folder, first_turn=dieser_turn_ist_erster,
                file_names=file_names,
                # Level 2's own open question is Robo's to evaluate: the
                # agent sees it marked as such, not as one to resolve.
                policy_ask_id=orders["open_ask_id"])
            if a.dry_agent:
                log("  [dry] prompt would be:",
                    prompt[:120].replace("\n", " "), "…")
                result = {"ok": True, "final": "", "error": ""}
            else:
                result = worker.run(prompt, timeout=TURN_TIMEOUT_S)
            duration = time.monotonic() - t0
            gelungen = bool(result["ok"])
            reply = TurnReply([], [])
            if gelungen:
                # The worker actually sent this prompt and Hermes wrote it
                # into the conversation history (worker.py: `send()` after
                # `agent.run_conversation()`). Only from here on can the
                # NEXT turn safely assume the instruction is already
                # there. A failed run's prompt may or may not have
                # reached the history depending on where it failed; the
                # conservative choice is to send the full prompt again
                # until one run is confirmed to have gone through.
                first_turn[0] = False
                if not a.dry_agent:
                    reply = replies.read(result.get("final") or "", turn_no[0])
                if reply.failure is not None:
                    # The agent's answer is not the JSON reply: the turn
                    # counts as failed (the block is carried to the next
                    # run), the text is kept.
                    keep_failed_reply(failed_replies_path, reply.failure,
                                      trace, log)
                    gelungen = False
                    result = {**result, "ok": False,
                              "error": "reply not readable: "
                              + str(reply.failure.get("reason"))}
            if result["ok"]:
                # Cleared BEFORE reading the proposals, not after: a line
                # this very run wrote unreadably has to survive to the
                # stage, and clearing afterwards would wipe it again. Only
                # the discard warning is this run's to withdraw -- the
                # conflict files, the dropped stream and an unwritable
                # protocol did not stop being true because a run went
                # well, and each of them is withdrawn by whoever checks
                # its own condition.
                writer.clear_warning(warning_text("discarded"))
                refresh_conflicts()
                last_run_failed.clear()
                # The run came back, so it saw the entries that still owe
                # the protocol an appearance. They stop riding along.
                state.mark_recorded(unrecorded)
            if orders_only:
                # --lanes (ADR 0026): this turn exists for level 2's
                # orders. Only their answers are applied; the minutes items
                # stay unfiled (under --lanes no agent writes the Verlauf).
                kept = order_answers(reply.proposals)
                dropped = len(reply.proposals) - len(kept) + len(reply.bullets)
                if dropped:
                    log(f"  orders turn: {dropped} items outside the orders"
                        " not filed")
                reply = TurnReply([], kept, reply.failure)
            if writer.protocol is not None:
                # Once more, right before the entries are filed: an order
                # run may have CREATED the file its `**erledigt** [[...]]`
                # entry links to, and the set from the start of the turn
                # would strip that link as dead.
                refresh_link_names(writer, folder, log)
            # The reply's stage actions go into the proposal file -- the
            # host writes it, the agent never does -- and are applied
            # first: a `topic` opens the block the reply's items belong to.
            try:
                append_proposals(proposals_path, reply.proposals)
            except OSError as e:
                log("stage proposals not written:", repr(e))
            # A wake order's evidence is the block it was decided on, which
            # may have been said before this turn's own block.
            discarded = writer.ingest(proposals_path, spoken=gesprochen)
            if discarded:
                log(f"  {discarded} unreadable proposal lines discarded")
            file_bullets(writer, reply.bullets, log)
            veraendert = writer.applied > vorher_angewandt
            trace.record_all(state.tick())
            # The middle of the three call-out outcomes section 7 asks for.
            # A visible result is already recorded as `call_answered` by
            # `applied_event()`; these two functions decide whether there
            # was one, and what the agent did instead. See their
            # docstrings -- since the automatic topic change, a moved
            # surface is no longer evidence that the order was answered.
            if b.wake_word and not answered_the_call(
                    state.main.id, vorher_flaeche, state.main.quote):
                trace.record("call_unanswered", ok=gelungen,
                             kind=unanswered_kind(
                                 state.main.id, vorher_flaeche,
                                 state.mirror, vorher_spiegel,
                                 state.headline, vorher_zeile))
            if result["ok"]:
                log(f"  done in {duration:.0f}s")
            else:
                log("run failed:", result["error"])
                last_run_failed.set()
        finally:
            # In a `finally`, so a turn can never leave "⚙️ Robo werkelt"
            # standing for the rest of the meeting -- and so a run that
            # threw still leaves its outcome in the trace.
            # This turn's orders that brought nothing end here, so
            # their pendings do not stand until the 60 s timeout.
            # A wake order arrived when this turn put up an answer to
            # a call-out -- the same test the call-out itself gets.
            try:
                turn_closed(
                    session_blocks, turn_no[0], ok=gelungen,
                    answered=answered_the_call(
                        state.main.id, vorher_flaeche, state.main.quote))
            except Exception as e:        # noqa: BLE001
                log("policy end_turn failed:", repr(e))
            if own_announcement:
                # The announcement ends here too, so an exception anywhere
                # above (worker.run, ingest, the call-out bookkeeping
                # itself) cannot leave it standing until its 60 s timeout
                # -- it settles as `failed` in that case, since `gelungen`
                # never became True. Never allowed to raise itself: a
                # broken settle must not cost the turn its own outcome
                # recording right below.
                try:
                    settle_call_out(
                        state, own_announcement, ok=gelungen,
                        answered=answered_the_call(
                            state.main.id, vorher_flaeche, state.main.quote))
                    trace.record_all(state.tick())
                except Exception as e:    # noqa: BLE001
                    log("announcement settle failed:", repr(e))
            turn_running.clear()
            trace.record("run_finished", ok=gelungen, kind=block_kind(b),
                         duration_s=round(time.monotonic() - t0, 1))
        publish_status()
        # The yardstick of the evidence: last sentence of the block to the
        # change that followed it, median one minute. Recorded AFTER the
        # stage was rewritten, so it measures what the round could see.
        # `end` is the block builder's monotonic clock, the same one `t0`
        # uses; the remainder block of `RunQueue.finish()` has none.
        #
        # `changed` carries what the number is worth: section 7 measures to
        # the EFFECTIVE change, and a turn that applied nothing -- a failed
        # run, or an honest "nothing new here" -- has none. The line is
        # still written, because how often that happens is a measurement
        # too; the join filters on this field.
        if b.end:
            trace.record("latency", ok=gelungen, kind=block_kind(b),
                         changed=veraendert,
                         latency_s=round(time.monotonic() - b.end, 1))
        return bool(result["ok"])

    # Meeting chat (issue #208, ADR 0027): a typed message is recorded in
    # the raw transcript by its one writer and goes to the call lane, the
    # lane a spoken wake order goes to. Without --lanes (or with
    # --no-agent) the chat says "unavailable" instead of waiting. Wired
    # before the ticker starts polling the action spool.
    wire_meeting_chat(
        session_blocks, record=minutes_doc.append_raw,
        ask=(lanes.handle_chat if lanes is not None and not a.no_agent else None),
        log=log)

    queue = RunQueue(run=run_turn, max_chars=a.max_batch)
    if lanes is not None and decisions.policy_on:
        # ADR 0026: under --lanes level 2's orders get their own turns on
        # the serial worker -- enqueued by the decider result hook, only
        # when a result queued an order.
        decisions.add_result_hook(
            orders_turn_hook(decisions.orders_waiting, queue.enqueue, log))
        log("lanes: level-2 orders run on the serial worker")
    # Which announcement is which call-out's: filled by `block_done()`,
    # emptied by `run_turn()`. Both reach it only when called, so it can
    # sit after the one and before the other.
    call_outs = CallOutAnnouncements()

    # A short-lived buffer, NOT a history: the SSE loop below clears this
    # right before each `bb.sentence(text)` call and then reads it back
    # to learn, synchronously, which block(s) (if any) that very call
    # emitted -- used to correlate a "wake" or "length" cut with the
    # utterance that caused it (see there).
    letzte_bloecke: list[Block] = []
    segment_counter = [0]

    def block_done(b: Block) -> None:
        # Captured BEFORE anything below, not after: trace.record(),
        # queue.enqueue(), log() and publish_status() (which can call a
        # real, possibly disk-writing writer.write()) all run first
        # otherwise, and "now" measured only after all of that would
        # measurably delay the pause marker's `at` -- the same problem
        # commit d34d5b8 already fixed for record_utterance()'s `at`,
        # and with a second consequence here: a pause marker line is
        # written to timing.jsonl before its own timestamp would then
        # suggest, breaking monotonicity against the utterance timestamp
        # (captured even earlier, before bb.sentence() is called).
        jetzt_block = datetime.now().astimezone()
        segment_counter[0] += 1
        # gateway/segmenter.Block carries no per-speaker breakdown yet, so
        # "speakers" stays empty until that lands.
        session_blocks.dispatch("segment", {
            "id": segment_counter[0], "text": b.text, "speakers": [],
            "wake_word": b.wake_word, "reason": b.reason, "chars": b.chars,
            "at": jetzt_block.timestamp(),
            # The last resolved speaker (see `letzter_sprecher`) and the
            # prefiltered agent-facing text, for the mirroring coach.
            "speaker": letzter_sprecher[0],
            "agent_text": (lanes.agent_text(b.text) if lanes is not None
                           else b.text),
        })
        if lanes is not None:
            # --lanes: a separate code path entirely, never touching
            # `queue`/`call_outs` -- Spur 4 (the raw protocol) needs no
            # agent today (konzept-spuren-architektur.md), so an ordinary
            # block goes only to the coach lane's context/eligibility
            # check, and a call goes only to the call lane. The speaker
            # label is the LAST utterance's resolved name (or
            # "unbekannt") -- a block can span more than one utterance,
            # see `letzter_sprecher`'s own comment. Repeats the shared
            # tail below (decider.submit(), the pause marker) itself and
            # returns, rather than falling through into the serial
            # path's try/except, which must stay untouched.
            try:
                lanes.handle_block(letzter_sprecher[0], b.text,
                                  wake_word=b.wake_word)
                log(f"  block ({b.reason}, {b.chars} chars)"
                    f"{' — ORDER (lane)' if b.wake_word else ' — lane'}")
            except Exception as e:        # noqa: BLE001 — never take the reader down with it
                log("block not handed to lanes:", repr(e))
            letzte_bloecke.append(b)
            if b.reason == "pause":
                try:
                    timing.record_pause_marker(jetzt_block)
                except Exception as e:    # noqa: BLE001
                    log("timing not written:", repr(e))
            return
        try:
            # The denominator of the call-out measure: how many orders the
            # round gave, against how many `call_answered` lines follow.
            #
            # BEFORE the enqueue, not after: the agent thread can pick the
            # block up the moment it is in the queue and write
            # `run_started` for it first. Both stamps have second
            # resolution, so the true order would then be unrecoverable
            # from the file -- and this pair is exactly what the latency
            # measure is joined on. `count` is therefore the depth this
            # block is about to land at, read one instant earlier.
            trace.record("block_enqueued", kind=block_kind(b), chars=b.chars,
                         count=queue.backlog() + 1)
            # A call-out is the one moment today where the consumer KNOWS
            # something will come: the round gave an order, and the answer
            # is 40-180 s away. The stage says so at once. Only when an
            # agent works the queue -- under --no-agent nothing will come,
            # and an announcement would be a promise nobody keeps.
            #
            # BEFORE the enqueue: a fast run (the dry run answers at once)
            # could otherwise finish, find no announcement to end as
            # unanswered, and leave this one standing until its timeout.
            # Its own guard as well: a failed announcement must not cost
            # the block its run.
            if b.wake_word and not a.no_agent:
                try:
                    call_outs.announce(writer, b)
                except Exception as e:    # noqa: BLE001
                    log("announcement skipped:", repr(e))
            wartend = queue.enqueue(b)
            log(f"  block #{queue.counted} ({b.reason}, {b.chars} chars)"
                f"{' — ORDER' if b.wake_word else ''}"
                f"{f' — {wartend} waiting' if wartend > 1 else ''}")
            # Only when somebody is actually working the queue: with
            # --no-agent nothing is behind, the blocks are simply not run.
            # publish_status() knows that, and it also knows that a
            # running turn outranks the backlog line.
            publish_status()
        except Exception as e:            # noqa: BLE001 — never take the reader down with it
            log("block could not be enqueued:", repr(e))
        letzte_bloecke.append(b)
        # `_emit("pause")` (gateway/segmenter.py) never runs synchronously
        # with the sentence that "caused" it -- it fires either at the
        # start of the NEXT sentence() call (closing THIS now-stale
        # block) or from the ticker thread's tick(), when nobody has
        # spoken for pause_s. Both callers land here synchronously, under
        # BlockBuilder's own lock -- so even though sentence() runs in
        # the SSE reader thread and tick() in the separate ticker thread
        # (see the CONCURRENCY note in gateway/segmenter.py), the lock keeps
        # block_done() itself from ever running concurrently, so this is
        # the one place that can write the marker line the instant the
        # block actually closes, rather than waiting for (and possibly
        # never seeing) the next utterance.
        # timing.jsonl is append-only: nothing already written is edited.
        if b.reason == "pause":
            try:
                timing.record_pause_marker(jetzt_block)
            except Exception as e:        # noqa: BLE001
                log("timing not written:", repr(e))

    bb = BlockBuilder(output=block_done, pause_s=a.pause,
                      max_s=a.max_block, wake_word=a.wake_word,
                      call_silence_s=a.call_silence_s, call_max_s=a.call_max_s)
    # The call-window block's host handle (issue #180, ADR 0024): a no-op
    # when the template does not carry the block.
    wire_call_window(session_blocks, bb)
    dispatch_call_window_state(session_blocks, bb)

    faeden: list[threading.Thread] = []
    if not a.no_agent:
        faeden.append(threading.Thread(target=queue.work_loop, args=(stopp,),
                                       daemon=True, name="agent"))

    def ticker() -> None:
        """Checks the speaking pause — and survives every error while doing so.

        If this thread dies, no block will be cut by a pause for the rest
        of the meeting. Nothing may kill it.

        It also advances the stage clocks and writes the result. That is
        cheap because `write()` only touches the file when the rendered
        content actually changed -- at rest the folder stays untouched.
        """
        last_block_tick = [0.0]
        while not stopp.wait(0.5):
            try:
                bb.tick()
                # A silence/max timeout closes the call window from here,
                # not from an action -- the block only reacts to events and
                # never polls the segmenter, so a voice-triggered
                # transition is pushed explicitly (issue #180, ADR 0024).
                dispatch_call_window_state(session_blocks, bb)
                try:
                    actions_reader.poll()
                except Exception as e:  # noqa: BLE001 -- must not skip the tick's write
                    log(f"actions: poll failed: {type(e).__name__}")
                # `tick()` returns exactly what the trace wants: follow-up,
                # expiry, cleared receipts. It stays the single source of
                # truth; this only writes it down. At rest it returns
                # nothing and the trace file is not touched.
                trace.record_all(state.tick())
                writer.write()
                if lanes is not None:
                    lanes.mirror_tick()
                # Once per second, not every 0.5 s wakeup -- the block
                # host's own clock, not this thread's poll interval.
                now_mono = time.monotonic()
                if now_mono - last_block_tick[0] >= 1.0:
                    last_block_tick[0] = now_mono
                    session_blocks.dispatch("tick", {
                        "at": time.time(),
                        "components": {"queue_backlog": queue.backlog(),
                                       "open_block_chars": bb.open_chars},
                    })
            except Exception as e:        # noqa: BLE001
                log("ticker error (continuing):", repr(e))

    faeden.append(threading.Thread(target=ticker, daemon=True, name="ticker"))

    def watchdog() -> None:
        """Restores raw lines that an external write has cost us.

        Waits in one-second steps so that shutdown doesn't hang until the
        next check time.
        """
        naechste = time.monotonic() + a.check_every
        while not stopp.wait(1.0):
            if time.monotonic() < naechste:
                continue
            naechste = time.monotonic() + a.check_every
            try:
                restored = minutes_file.restore_missing()
                if restored:
                    writer.warn(warning_text("restored", count=restored))
                    writer.write()
                # The conflict files are re-checked on the same timer:
                # they appear DURING the meeting, and they never go away
                # by themselves. Logged only when the answer changes, so
                # the operations log doesn't fill up with it.
                now_conflicted = refresh_conflicts()
                if now_conflicted != conflicts_seen[0]:
                    conflicts_seen[0] = now_conflicted
                    log("warning: conflict files in the project folder:"
                        if now_conflicted else "conflict files are gone.",
                        ", ".join(p.name for p in find_conflicts(conflict_dirs)[:5]))
                writer.write()
            except Exception as e:        # noqa: BLE001
                log("watchdog error (continuing):", repr(e))

    if a.check_every > 0:
        faeden.append(threading.Thread(target=watchdog, daemon=True, name="watchdog"))

    for f in faeden:
        f.start()

    letzter_commit = [time.monotonic()]

    def commit_if_due() -> None:
        """Never raises: it runs in the SSE reader, where nothing may."""
        try:
            if time.monotonic() - letzter_commit[0] > a.commit_every:
                letzter_commit[0] = time.monotonic()
                session_git.git("Live-Mitschrift laeuft")
        except Exception as e:            # noqa: BLE001
            log("commit skipped:", repr(e))

    stt_cfg = SttConfig(transport="sse", url=a.stt, read_timeout_s=a.stt_timeout)
    log("connecting to", a.stt.rstrip("/") + "/events")
    versuch = n = 0
    try:
        while True:
            source = None
            try:
                source = open_stt(stt_cfg)
                for ev in source.events():
                    if versuch:
                        log("connected.")
                        versuch = 0
                        # The stream is back, so its warning goes -- and
                        # only its own: a conflict file or an unwritable
                        # protocol is not fixed by a reconnect.
                        writer.clear_warning(warning_text("stream"))
                        publish_status()
                    # The STT server's smart-turn answer for a final that
                    # already arrived (stt/.../thought_end.py). Numbers
                    # only; handed to the lane, never to the block builder.
                    if ev.type == "thought_end":
                        if gedankenende is not None:
                            gedankenende.smart_turn(ev.to_wire())
                        continue
                    if ev.type == "partial":
                        # issue #181: the live-transcript block's tentative
                        # tail. Forwarded as-is on every partial; the block
                        # itself throttles how often it re-emits the view
                        # intent (<=4/s per turn), and ignores a partial
                        # with no turn_id (a status ping, not a transcript
                        # update) -- see blocks/live_transcript/transcript.py.
                        session_blocks.dispatch("partial", {
                            "turn_id": ev.turn_id,
                            "speaker": final_speaker(ev),
                            "text": ev.text,
                            "at": time.time(),
                        })
                        continue
                    if ev.type != "final":
                        continue
                    text = ev.text
                    text = ("" if text is None else str(text)).strip()
                    if not text:
                        continue
                    spk = final_speaker(ev)
                    # Separate from `spk` on purpose: `spk` folds
                    # "default"/"mic_gate" to None for speaker
                    # attribution in the protocol -- timing.jsonl wants
                    # the raw channel id as the STT server sent it.
                    roh_recognizer = ev.recognizer_id
                    recognizer_id_raw = (str(roh_recognizer)
                                         if roh_recognizer not in (None, "")
                                         else None)
                    # The audio-clock span of this utterance, when the STT
                    # server measures one (stt/ since 2026-09-24). Both or
                    # neither -- half a span looks like a measurement and
                    # is not one -- and anything unusable counts as ABSENT
                    # rather than as an error: an older server simply
                    # sends nothing here and must keep working.
                    t_start = ev.t_start
                    t_end = ev.t_end
                    if not valid_span(t_start, t_end):
                        t_start = t_end = None
                    # Speaker resolution, --speakers
                    # only: a name from voice-embedding cosine similarity
                    # REPLACES `spk` entirely, channel id and all -- the
                    # whole point is telling people apart on a single mic,
                    # where `spk` is always the same useless "0". Never
                    # raises: a broken resolution must read exactly like
                    # no resolution, not like a dropped utterance.
                    # A control utterance (intro round start/end, decided
                    # ONLY by enroll.classify_control) is consumed
                    # here: it gets a stage mirror as feedback and is NOT
                    # handed to the block builder, so it never reaches the
                    # agent as an ORDER.
                    control = None
                    split_future = None
                    resolved = None
                    label_resolved = None
                    if enrollment is not None:
                        try:
                            war_aktiv = enrollment.active
                            control = enrollment.handle_control(text)
                            # A start line may carry the first intro in the
                            # same breath ("Robo-Vorstellungsrunde. Ich bin
                            # Max, ..." -- live 2026-10-20 12:13), so it is
                            # still fed to the accumulator; only an END line
                            # is never an introduction.
                            neu = (None if control == "end" else
                                   enrollment.maybe_enroll(text, t_start, t_end))
                            # --speaker-split: started BEFORE resolve() so
                            # both embedding round trips overlap.
                            if (splitter is not None and not control
                                    and t_start is not None):
                                split_future = splitter.split_async(
                                    text, t_start, t_end, ev.extra.get("words"))
                            resolved = enrollment.resolve(t_start, t_end)
                        except Exception as e:     # noqa: BLE001
                            log("speaker resolution skipped:", repr(e))
                            neu = resolved = None
                        if resolved:
                            spk = resolved
                            # Point 7 (2026-10-20): the lane window keeps
                            # each line's own resolved name (or
                            # "unbekannt") -- this is the LAST one, used
                            # at block_done() where blocks, not single
                            # utterances, are the unit lanes see.
                            if lanes is not None:
                                letzter_sprecher[0] = resolved
                        # Falling edge only: the round just finished
                        # introducing itself. The stable prefix may
                        # change exactly once for this (point 7) -- never
                        # on every enrolled name trickling in one by one.
                        if (lanes is not None and war_aktiv
                                and not enrollment.active):
                            try:
                                lanes.update_stable_prefix(project_agenda_text(
                                    title, a.participants, agenda,
                                    enrollment.store.names()))
                                log("lanes: stable prefix updated with "
                                    f"enrolled names: "
                                    f"{', '.join(enrollment.store.names())}")
                            except Exception as e:   # noqa: BLE001
                                log("lanes: stable prefix update skipped:",
                                   repr(e))
                        try:
                            mirror = enrollment_mirror(
                                control, neu, enrollment.store.names_in_order())
                            if mirror:
                                state.apply({"action": "mirror", "text": mirror})
                                writer.write()
                                log(f"speakers: {control or 'enrolled'} -> "
                                    f"{len(enrollment.store.names())} name(s)")
                        except Exception as e:     # noqa: BLE001
                            log("speaker mirror skipped:", repr(e))
                    # Diarization-label attribution,
                    # --speaker-labels only: independent of `enrollment`
                    # above, needs only the recognizer id Speechmatics
                    # already sends, no audio. Lower priority than the
                    # voice-embedding `resolved` above -- it only fills in
                    # `spk` when --speakers found nothing this time. Never
                    # raises: a broken attribution must read exactly like
                    # no attribution, not like a dropped utterance.
                    if speaker_labels is not None and recognizer_id_raw:
                        try:
                            rule_name = rule_attribute(
                                text, speaker_labels.participants)
                            if rule_name:
                                speaker_labels.attrib.assign_rule(
                                    recognizer_id_raw, rule_name)
                            speaker_labels.window.push(recognizer_id_raw, text)
                            label_name = speaker_labels.attrib.name_for(
                                recognizer_id_raw)
                            if label_name and not resolved:
                                spk = label_name
                                label_resolved = label_name
                                if lanes is not None:
                                    letzter_sprecher[0] = label_name
                            if (speaker_labels.worker is not None
                                    and should_fire_llm(
                                        speaker_labels.window,
                                        speaker_labels.participants,
                                        speaker_labels.attrib)):
                                speaker_labels.worker.request(
                                    speaker_labels.window.snapshot(),
                                    speaker_labels.participants)
                        except Exception as e:     # noqa: BLE001
                            log("speaker label attribution skipped:", repr(e))
                    # --speaker-split: one line per speaker turn when the
                    # sub-window embeddings found a change inside this
                    # utterance. `turns` None (the default, and every
                    # fallback) leaves everything below exactly as before.
                    turns = None
                    if split_future is not None:
                        turns = splitter.collect(split_future, log)
                        if turns and resolved:
                            spk = turns[-1].name
                            if lanes is not None:
                                letzter_sprecher[0] = spk
                        else:
                            turns = None
                    # Secure first, and never raise: `line()` scrubs what
                    # cannot be encoded and `_write()` catches what the
                    # file system can do. This guard is the last net --
                    # an exception here reads to the enclosing `except`
                    # as a dropped stream, and the backoff that follows
                    # costs the transcript everything spoken during it.
                    try:
                        if turns:
                            for turn in turns:
                                # The split sub-turns share the one STT
                                # turn_id of the utterance they came from
                                # (gateway/speaker_split.py's `Turn` has no
                                # id of its own) -- good enough for the
                                # live-transcript block to match a final
                                # back to the partial it closes.
                                feed_utterance(minutes_file, session_blocks, turn.text, turn.name,
                                              {"turn_id": ev.turn_id,
                                               "t_start": turn.t_start, "t_end": turn.t_end,
                                               "at": time.time()})
                        else:
                            feed_utterance(minutes_file, session_blocks, text, spk,
                                          {"turn_id": ev.turn_id,
                                           "t_start": t_start, "t_end": t_end,
                                           "at": time.time()})
                    except Exception as e:         # noqa: BLE001
                        log("raw line not written:", repr(e))
                    # RAW FIRST, always: the line above is the evidence,
                    # this one is a convenience. `enqueue` does no I/O,
                    # never blocks and never raises (gateway/correction.py)
                    # -- an exception here would read to the enclosing
                    # `except` as a dropped stream, and the backoff that
                    # follows costs the transcript everything spoken
                    # during it.
                    if korrektur is not None:
                        for t_text, t_spk, t_s, t_e in (
                                [(t.text, t.name, t.t_start, t.t_end)
                                 for t in turns] if turns
                                else [(text, spk, t_start, t_end)]):
                            korrektur.enqueue(t_text, t_spk,
                                              stamp=f"{datetime.now():%H:%M}",
                                              t_start=t_s, t_end=t_e)
                    elif vorfilter:
                        # Synchronous, not enqueued: no model, no I/O in
                        # correction_prefilter.prefilter() itself, so there
                        # is nothing to hand off to a worker thread for.
                        # `write_prefiltered` never raises (see its own
                        # docstring) -- no try/except needed here either,
                        # same as the `korrektur.enqueue` call above.
                        for t_text, t_spk in ([(t.text, t.name) for t in turns]
                                              if turns else [(text, spk)]):
                            write_prefiltered(orte["correction"], t_text, t_spk,
                                              stamp=f"{datetime.now():%H:%M}")
                    n += 1
                    log(f"[{n}] {text[:80]}")
                    # "Did you hear me?" — the one question the round asked
                    # again and again. It is answered here, in seconds,
                    # from facts this process already holds; the agent is
                    # far too slow to acknowledge anything. Before
                    # bb.sentence(), so a block cut by THIS utterance can
                    # still put the backlog line on top of it.
                    # NOT gated on worker.alive(): with --no-agent and
                    # --dry-agent no worker ever runs, and the
                    # acknowledgement is then the only thing the stage
                    # can offer -- it used to freeze for the whole run.
                    # publish_status() keeps "⚙️ Robo werkelt" standing
                    # while a turn is in flight.
                    publish_status()
                    letzte_bloecke.clear()
                    # Captured BEFORE the call, not after: for an
                    # utterance whose processing cuts a block (wake or
                    # length), bb.sentence()'s synchronous call chain
                    # runs _emit() -> output(b) -> block_done(b) ->
                    # publish_status() -> a real, possibly disk-writing
                    # writer.write() before control returns here -- and
                    # "now" measured only after all of that would
                    # measurably delay `at` for exactly the utterances
                    # whose timing is most interesting.
                    jetzt = datetime.now().astimezone()
                    # "naechster Punkt"/"naechste Frage"/"naechste Person":
                    # recognized and applied here, in code, BEFORE the text
                    # ever reaches the block builder. A command swallowed
                    # here never becomes a block, so it never reaches the
                    # agent and never becomes protocol/minutes text --
                    # exactly the ad-hoc moderation.py ticker's behaviour,
                    # now wired into the SSE loop instead of a second
                    # process watching the protocol from outside.
                    if agenda_ctrl is not None and agenda_ctrl.handle_utterance(text):
                        state.agenda = agenda_ctrl.payload()
                        commit_if_due()
                        continue
                    # The resolved name goes into the block/agent text the
                    # same way it already went into the raw line above:
                    # with --speakers and --speaker-labels both off (or no
                    # resolution/attribution this time) `agent_text` stays
                    # exactly `text` -- byte for byte what bb.sentence()
                    # always got.
                    agent_text = (f"{spk}: {text}"
                                 if (enrollment is not None and resolved)
                                 or label_resolved
                                 else text)
                    if turns:
                        agent_text = "\n".join(f"{t.name}: {t.text}"
                                               for t in turns)
                    try:
                        if not control:             # consumed above
                            # A line of a running intro round never is an
                            # order, even if it names Robo.
                            intro_line = bool(enrollment is not None
                                              and getattr(enrollment,
                                                          "active", False))
                            bb.sentence(agent_text,          # then bundle
                                        allow_wake=not intro_line)
                        # A wake word or the end phrase can open/close the
                        # call window SYNCHRONOUSLY inside this very call
                        # (issue #180, ADR 0024) -- pushed right away so a
                        # voice-triggered transition reaches `listen:room`
                        # exactly like a tap-triggered one does.
                        dispatch_call_window_state(session_blocks, bb)
                    except Exception as e:         # noqa: BLE001
                        log("block building skipped:", repr(e))
                    else:
                        # Did THIS utterance itself cut a block, SYNCHRONOUSLY
                        # inside this very call? wake always does (it is
                        # emitted from bb.sentence() the instant the wake
                        # word matches). length can too -- but in normal
                        # operation it almost always fires later, from the
                        # ticker thread's bb.tick() (see there), which never
                        # runs through this branch and so is never
                        # attributed here: that cut then shows up in
                        # timing.jsonl as a LATER line with cut=null, not on
                        # this utterance. See gateway/timing.py's `cut` field
                        # for the documented limitation this causes, and the
                        # Global Constraints for why pause is handled
                        # separately, in block_done. A block whose last
                        # sentence is not this text belongs to an older
                        # utterance and is none of this line's business.
                        schnitt = None
                        for b in letzte_bloecke:
                            if (b.reason in ("wake", "length")
                                    and b.sentences
                                    and b.sentences[-1] == agent_text):
                                schnitt = b.reason
                                break
                        try:
                            timing.record_utterance(recognizer_id_raw,
                                                    len(text), schnitt, jetzt,
                                                    t_start=t_start,
                                                    t_end=t_end)
                        except Exception as e:     # noqa: BLE001
                            log("timing not written:", repr(e))
                        # AFTER the timing row, with the SAME `jetzt`: the
                        # evaluation joins the two files on `at`. Never
                        # blocks, never raises (gateway/thought_end_live.py).
                        if gedankenende is not None:
                            gedankenende.enqueue(
                                text, at=jetzt,
                                recognizer_id=recognizer_id_raw,
                                turn_id=ev.turn_id,
                                t_start=t_start, t_end=t_end)
                    commit_if_due()
                # Even a cleanly closed stream counts as a drop: without a
                # wait, the reconnect would otherwise run as a full-load
                # infinite loop if the server immediately closes the
                # connection again.
                grund = "stream closed by server"
            except KeyboardInterrupt:
                raise
            except Exception as e:
                grund = repr(e)
            finally:
                if source is not None:
                    source.close()
            versuch += 1
            wart = min(30.0, a.retry_delay * (2.0 ** (versuch - 1)))
            log(f"STT stream dropped ({grund}); retrying in {wart:.1f}s")
            # The round has to know. Until now this was a line in the
            # operations log while the stage kept showing "hört zu" with a
            # frozen timestamp -- speech was being lost and the surface
            # said everything was fine. Withdrawn on the next connect.
            writer.warn(warning_text("stream"))
            publish_status()
            if stopp.wait(wart):
                break
    except KeyboardInterrupt:
        log("shutting down …")

    # Shutdown window: the sync stays out while the last writes land.
    # A second Ctrl-C / SIGTERM while waiting for the lock must cost the
    # lock only -- never the final block, the safety net or the commit.
    try:
        sync_window.take_lock()
    except KeyboardInterrupt:
        log("sync lock wait aborted, shutting down without it")
    # First emit the last block, then stop the threads — otherwise the
    # worker thread could exit before the final block arrives.
    try:
        bb.finish()
    except Exception as e:                # noqa: BLE001
        log("final block:", repr(e))
    stopp.set()
    try:
        if not a.no_agent:
            rest = queue.backlog()
            if rest:
                log(f"waiting for {rest} not yet processed blocks — "
                    f"another Ctrl-C aborts")
        for f in faeden:
            f.join(600 if f.name == "agent" else 5)
        if not a.no_agent:
            queue.finish()
    except KeyboardInterrupt:
        log("aborted — the raw transcript is complete, processing is not.")
    worker.stop()
    try:
        session_blocks.stop()
    except Exception as e:                # noqa: BLE001
        log("block host shutdown:", repr(e))
    if lanes is not None:
        try:
            lanes.stop()
        except Exception as e:            # noqa: BLE001
            log("lanes shutdown:", repr(e))
    if splitter is not None:
        log(f"speaker split: {splitter.utterances} utterances probed, "
            f"{splitter.splits} split, {splitter.embed_calls} embed calls, "
            f"{splitter.fallbacks} fallbacks")
        splitter.close()
    if korrektur is not None:
        # Bounded: what is still waiting after this is written raw and
        # marked `skipped`. A shutdown must not hang on a model.
        # Bound as finish() computes it: the join (timeout + grace), then
        # at most as long again for the writer lock. A second Ctrl-C in
        # here must cost the corrected tail only -- never the status, the
        # raw-transcript safety net, the closing note or the final commit
        # below.
        try:
            log(f"correction: finishing, at most "
                f"{2 * (FINISH_TIMEOUT_S + FINISH_GRACE_S):.0f} s")
            korrektur.finish()
        except KeyboardInterrupt:
            log("correction: finish aborted, the raw transcript is complete")
    # Bounded (decider.FINISH_TIMEOUT_S): the backlog is a measurement,
    # the closing note and the final commit below are not to wait on
    # it. A second Ctrl-C here costs the decisions' tail only. Level 2 is
    # detached and closed first inside: the agent is gone, and an action
    # on the backlog would freeze into the final stage file.
    decisions.finish()
    if gedankenende is not None:
        # Bounded like the correction: late rows are written without Laya
        # and marked `skipped`; a second Ctrl-C costs the lane's tail only.
        # The number matches finish()'s own join budget (timeout + 5 s for
        # the writer lock, gateway/thought_end_live.py).
        try:
            log(f"thought end: finishing, at most "
                f"{THOUGHT_END_FINISH_TIMEOUT_S + 5:.0f} s")
            gedankenende.finish()
        except KeyboardInterrupt:
            log("thought end: finish aborted, the raw transcript is complete")
    # The last state the round gets to see: not "⚙️ Robo werkelt", which
    # is what a stage frozen mid-run would keep claiming for the rest of
    # the evening.
    writer.set_status("listening", at=datetime.now().strftime("%H:%M:%S"))
    writer.write()
    minutes_file.restore_missing()
    # Closes this meeting in the append-only trace: everything between
    # `meeting_started` and here belongs to one run.
    trace.record("meeting_ended", count=n)
    minutes_file.append_note(f"\n*Mitschrift beendet {datetime.now():%H:%M} — "
                     f"{n} Aeusserungen.*\n")
    if queue.failed:
        log(f"note: {queue.failed} agent runs failed.")
    if worker.restarts:
        log(f"note: the worker was restarted {worker.restarts} times.")
    session_git.git_wait(60)
    session_git._git_run(f"Live-Mitschrift beendet ({n} Aeusserungen)")
    # Last step, still under the sync lock and as the folder's only
    # writer: sort `.conflictN` fragments into konflikte-<date>/ (moved,
    # never deleted). A triage problem must never break the shutdown.
    try:
        res = triage_conflicts(project_folders(folder), target_root=folder,
                               window=sync_window)
        log(res.summary())
    except (Exception, KeyboardInterrupt) as e:  # noqa: BLE001
        log("conflict triage failed:", repr(e))
    sync_window.release()
    log("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
