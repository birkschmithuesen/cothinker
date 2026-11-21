"""Session host: the single writer of the minutes file (`protokoll.md`).

The raw transcript (`## Rohtranskript`) is written only here, and every
utterance reaches it before any block sees it. The minutes-document block
renders the sections in front of that heading; the host splices them in.
Both operations run under the same lock, and a section write carries the
raw section through byte-identically.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from gateway.stage_state import scrub
from shared.minutes_format import _ROH_LINE_RE

RAW_HEADING = "## Rohtranskript"


def _log(*args) -> None:
    print("[raw_transcript]", *args, file=sys.stderr, flush=True)


def _flock(f) -> bool:
    """File lock across process boundaries, where the platform has one."""
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return True
    except Exception:
        return False


class MinutesFile:
    """The one writer of a session's minutes file."""

    def __init__(self, path: Path, lock: "threading.RLock | threading.Lock",
                 report: Callable[[bool], None] | None = None):
        self.path = Path(path)
        self.lock = lock
        # Called after every append attempt with True or False: a failed
        # write is the most expensive failure here, the raw transcript is
        # unrecoverable.
        self.report = report or (lambda ok: None)
        self._raw_lines: list[str] = []

    @staticmethod
    def line(text, speaker=None, stempel: str | None = None) -> str:
        """One raw-transcript line. Exactly one line, always."""
        text = re.sub(r"\s+", " ", scrub(str(text))).strip()
        stempel = stempel or f"{datetime.now():%H:%M}"
        wer = ""
        if speaker:
            wer = "*" + re.sub(r"\s+", " ", str(speaker)).strip() + ":* "
        return f"- **{stempel}** {wer}{text}\n"

    def append_raw(self, text, speaker=None) -> bool:
        """Append one utterance to the raw section. Returns whether it landed.

        Never raises on I/O or encoding errors: this runs on the reader
        thread, where nothing may raise.
        """
        zeile = self.line(text, speaker)
        with self.lock:
            self._raw_lines.append(zeile)
            return self._append(zeile)

    def append_note(self, text: str) -> bool:
        """Append a note of the host's own (not an utterance). Returns
        whether it landed."""
        return self._append(text)

    def _append(self, text: str) -> bool:
        """Append to the file; report the outcome. Never raises."""
        ok = False
        with self.lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    _flock(f)
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                ok = True
            except (OSError, UnicodeError) as e:
                _log("minutes file not writable:", e)
        # The report may take the stage writer's lock while this file's lock
        # is still held by a caller; that is the only direction there is.
        try:
            self.report(ok)
        except Exception as e:  # noqa: BLE001 -- never fail a write
            _log("report failed:", repr(e))
        return ok

    def restore_missing(self) -> int:
        """Write back raw lines another writer made disappear. Returns the
        number RESTORED, zero when the write back failed."""
        with self.lock:
            if not self._raw_lines:
                return 0
            try:
                inhalt = self.path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                _log("minutes file not readable:", e)
                return 0
            vorhanden = set(inhalt.splitlines())
            fehlend = [z for z in self._raw_lines if z.rstrip("\n") not in vorhanden]
            if not fehlend:
                return 0
            _log(f"warning: {len(fehlend)} raw lines were missing from the "
                 f"protocol — restoring (external write?)")
            kopf = (f"\n*Nachgetragen {datetime.now():%H:%M} — {len(fehlend)} "
                    f"Zeilen waren aus dem Rohtranskript verschwunden.*\n")
            if not self._append(kopf + "".join(fehlend)):
                _log("  restoring failed — the lines are still missing")
                return 0
            return len(fehlend)

    def write_sections(self, text: str) -> None:
        """Replace everything in front of `## Rohtranskript` with `text`.

        "In front of" is the whole prefix: front matter, title and every
        section the block rendered, so `text` is the complete head of the
        document. The heading and everything after it are carried through
        byte-identically. A missing file or heading is created: `text`,
        the heading, nothing else. Atomic (tmp file + os.replace).
        """
        head = text.rstrip("\r\n") + "\n\n"
        with self.lock:
            try:
                with open(self.path, encoding="utf-8", newline="",
                          errors="surrogateescape") as f:
                    old = f.read()
            except FileNotFoundError:
                old = ""
            m = _ROH_LINE_RE.search(old)
            tail = old[m.start():] if m else RAW_HEADING + "\n"
            self._replace(head + tail)

    def _replace(self, text: str) -> None:
        """Write `text` to a temp file next to the file, fsync, replace."""
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="",
                      errors="surrogateescape") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                shutil.copymode(self.path, tmp)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        try:
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
