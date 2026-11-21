#!/usr/bin/env python3
# CoThinker — gateway/legacy_stage_sink.py: view intents onto today's stage
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Routes the block host's view intents to today's `StageWriter` (issue
#38, Wave B.1) -- the interim module between the Surface API (ADR 0006)
and a stage that still only understands `buehne.json`.

A `ViewIntent.id` is "<block-id>:<suffix>" by the blocks' own convention;
`HANDLERS` keys on that block-id prefix, one line per block, the same way
`gateway/block_host_wiring.REGISTRY` does. The whole module is replaced,
not extended, by the stage surface of Wave C (ADR 0006/0008) -- it exists
only to bridge the gap.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Mapping, Optional

from blocks.mirroring_coach.coach import NACHTRAG_PREFIX  # the block owns the constant
from shared.surface import ViewIntent

# The Nachtrag notice reuses the EXISTING single-slot warning channel
# (`StageWriter.warn()`/`clear_warning()`) rather than a new field -- but
# that slot is shared with real operational warnings (conflicts, a failed
# protocol write), so the sink NEVER overwrites a warning it did not itself
# write: every text it sets starts with this prefix, and it only ever
# replaces a warning that already starts with it.


class _MirrorRow:
    """`mirror:<n>` (the mirroring coach): files a coach contribution
    exactly where the coach lane always put it -- into ITS topic's history
    entry (never the mirror line, which is the call lane's) -- plus the
    Nachtrag notice and the JSONL record of attempts that did not land."""

    def __init__(self, log: Callable[[str], None],
                 history_path: Optional[Path]) -> None:
        self.log = log
        self.history_path = history_path
        self._lock = threading.Lock()
        self._pending: list = []          # titles of pending Nachtraege
        self._last_text = ""              # the notice text this row wrote

    def __call__(self, writer: object, intent: ViewIntent) -> None:
        payload = intent.payload
        kind = payload.get("contribution")
        if kind == "clear":
            self._clear_notice(writer)
        elif kind == "failure":
            self._file_record(payload.get("record"))
        elif kind in ("reply", "Abschluss", "Nachtrag"):
            self._file(writer, payload)

    def _file(self, writer: object, payload: Mapping[str, object]) -> None:
        entry_id = str(payload.get("topic_id") or "")
        if not entry_id or not writer.state.append_to_history(
                entry_id, str(payload.get("addition") or "")):
            # The entry aged out of MAX_HISTORY, or none was ever pushed
            # (the brake refused it) -- fall back to the JSONL record so
            # the contribution is not silently lost.
            self._file_record(payload.get("record"))
            return
        try:
            writer.write()
        except Exception as e:                # noqa: BLE001
            self.log(f"lane: coach result not written: {e!r}")
        if payload.get("notice"):
            title = writer.state.history_title(entry_id) or "?"
            self.log(f"coach contribution filed as a Nachtrag on an older "
                     f"topic ({title!r}), not shown as current")
            self._note_nachtrag(writer, title)

    def _note_nachtrag(self, writer: object, title: str) -> None:
        """A small notice for a late result that landed on a topic no
        longer shown as current. Several pending Nachtraege collapse into
        one line; cleared by the next genuinely new topic."""
        with self._lock:
            self._pending.append(title)
            pending = list(self._pending)
        try:
            current = writer.warning()
        except Exception as e:                # noqa: BLE001
            self.log(f"lane: nachtrag notice skipped: {e!r}")
            return
        if current and not current.startswith(NACHTRAG_PREFIX):
            # Never clobber someone else's warning: the notice waits and
            # gets another chance on the next Nachtrag.
            return
        if len(pending) == 1:
            text = f'{NACHTRAG_PREFIX}zu „{pending[0]}" — ← zum Anzeigen'
        else:
            text = (f"{len(pending)} {NACHTRAG_PREFIX}e: "
                    + ", ".join(f'„{t}"' for t in pending))
        try:
            writer.warn(text)
            writer.write()
            self._last_text = text
        except Exception as e:                # noqa: BLE001
            self.log(f"lane: nachtrag notice not written: {e!r}")

    def _clear_notice(self, writer: object) -> None:
        with self._lock:
            had_pending = bool(self._pending)
            self._pending = []
            last_text, self._last_text = self._last_text, ""
        if not had_pending or not last_text:
            return
        try:
            # `only=` (not an unconditional clear): if some OTHER warning
            # has since taken the slot, this must not clobber it either.
            writer.clear_warning(only=last_text)
            writer.write()
        except Exception as e:                # noqa: BLE001
            self.log(f"lane: nachtrag notice not cleared: {e!r}")

    def _file_record(self, record: object) -> None:
        if not self.history_path or not isinstance(record, Mapping):
            return
        try:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
        except OSError as e:
            self.log(f"coach history not written: {e!r}")


# One line per block, added by the pull request that builds it. A row is a
# plain `handler(writer, intent)`. A block whose rows need state of their own
# (the mirroring coach's pending Nachtraege) gets a row object built in
# `make_sink`, configured through `row_config`.
HANDLERS: dict[str, Callable[[object, ViewIntent], None]] = {}


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


def make_sink(writer: object, log: Callable[[str], None],
              row_config: Optional[Mapping[str, object]] = None) -> Callable[[ViewIntent], None]:
    """An unknown prefix is logged once, not once per intent.

    `row_config` carries settings for the stateful rows built here:
    `coach_history_path` (Path) is where the `mirror:` row records
    contributions that did not land in the stage."""
    logged: set[str] = set()
    config = row_config or {}
    history_path = config.get("coach_history_path")
    handlers: dict[str, Callable[[object, ViewIntent], None]] = {
        **HANDLERS,
        "mirror": _MirrorRow(log, Path(history_path) if history_path else None),
    }

    def sink(intent: ViewIntent) -> None:
        prefix = _prefix(intent.id)
        handler = handlers.get(prefix)
        if handler is None:
            if prefix not in logged:
                logged.add(prefix)
                log(f"legacy stage sink: no handler for intent prefix {prefix!r}")
            return
        handler(writer, intent)

    return sink
