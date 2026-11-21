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

from gateway.stage_state import scrub

RAW_HEADING = "## Rohtranskript"
_RAW_LINE_RE = re.compile(r"^## Rohtranskript[ \t]*\r?$", re.MULTILINE)


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

    def __init__(self, path: Path, lock: "threading.RLock | threading.Lock"):
        self.path = Path(path)
        self.lock = lock

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
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    _flock(f)
                    f.write(zeile)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except (OSError, UnicodeError) as e:
                _log("minutes file not writable:", e)
                return False

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
            m = _RAW_LINE_RE.search(old)
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
