# CoThinker — atomic whole-file writes and the sync window
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Keep the vault sync (rclone bisync) from seeing a half-written file.

Why: on 2026-09-26 three consumer restarts inside a running meeting left
eight `.conflictN` fragments (`buehne.json`, `stage-events.jsonl`, the
decisions file) -- the sync caught the files while they were being
replaced or rewritten (docs/feedback-2026-09-26.md point 9).

Two protections, both owned by the consumer:

* `atomic_write_text()` -- a whole-file rewrite goes to a temp file NEXT
  to the target (same file system), is fsynced and then `os.replace()`d.
  The temp name always ends in `.tmp`, so one exclude line in the sync
  filter (`**/*.tmp`) keeps it out of the sync altogether.
* `SyncWindow` -- around the restart windows (start-up and shutdown) the
  consumer takes the sync's own lock, `/tmp/vault-bisync.lock`
  (`flock`, the same one `vault-instant-sync.sh` takes with `flock -n`
  and skips its run on), and drops a marker file `.cothinker-writing` in
  the session folder. The marker is for humans and for scripts: a
  sync that has no lock concept can be told to leave the folder alone
  while it exists (docs/operations.md).

Append-only `*.jsonl` files stay appends: every row is ONE `write()` of a
complete line on an `O_APPEND` descriptor, which the kernel keeps whole.
Rewriting a growing trace per event would only change its inode every
time and make the sync's life harder, not easier.
"""
from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from typing import Callable

try:                                    # pragma: no cover -- non-POSIX
    import fcntl
except ImportError:                     # pragma: no cover
    fcntl = None                        # type: ignore[assignment]

TMP_SUFFIX = ".tmp"
MARKER_NAME = ".cothinker-writing"
DEFAULT_SYNC_LOCK = "/tmp/vault-bisync.lock"
LOCK_ENV = "COTHINKER_SYNC_LOCK"


def tmp_path_for(path: Path) -> Path:
    """The temp file `atomic_write_text` uses for `path`: hidden, next to
    it, ending in `.tmp`."""
    return path.with_name(f".{path.name}{TMP_SUFFIX}")


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` in one step. Readers see the old file or
    the new one, never a part of it. A failed attempt leaves no temp file."""
    tmp = tmp_path_for(path)
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def sweep_stale_tmp(folder: Path) -> list[Path]:
    """Remove temp files a killed process left in `folder` (not recursive).

    Only the names `tmp_path_for()` produces (`.<name>.tmp`) -- plus the
    older visible `<name>.tmp` of `buehne.json`/`agenda` state from before
    this module -- are touched. Returns what was removed.
    """
    removed: list[Path] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return removed
    for p in entries:
        n = p.name
        if not n.endswith(TMP_SUFFIX) or not p.is_file():
            continue
        if n.startswith(".") or n in ("buehne.json.tmp",):
            try:
                p.unlink()
                removed.append(p)
            except OSError:
                pass
    return removed


class SyncWindow:
    """Context manager for a restart window.

    The marker lives from `acquire()` to `release()` (the whole run); the
    lock only around the restart windows (`release_lock()` after start-up,
    `take_lock()` again at shutdown).

    On enter: sweep stale temp files in the session folder, write the
    marker, take the sync lock (waiting at most `wait_s`; a sync run that
    is already underway is not worth a delayed meeting, so it carries on
    without the lock and says so via `.locked`). On exit: remove the
    marker and release the lock. `release()` may be called early.
    """

    def __init__(self, folder: Path, *, lock_path: str | None = None,
                 wait_s: float = 5.0,
                 log: Callable[..., None] = lambda *a: None) -> None:
        self.folder = Path(folder)
        self.lock_path = lock_path or os.environ.get(LOCK_ENV) or DEFAULT_SYNC_LOCK
        self.wait_s = wait_s
        self.log = log
        self.locked = False
        self._fd: int | None = None

    @property
    def marker(self) -> Path:
        return self.folder / MARKER_NAME

    def __enter__(self) -> "SyncWindow":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def acquire(self) -> bool:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for p in sweep_stale_tmp(self.folder):
                self.log("removed stale temp file:", p.name)
            self.marker.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError as e:
            self.log("sync marker not written:", repr(e))
        return self.take_lock()

    def take_lock(self) -> bool:
        """(Re-)take the sync lock; the marker stays as it is."""
        if self._fd is None:
            self.locked = self._take_lock()
        return self.locked

    def _take_lock(self) -> bool:
        if fcntl is None:
            return False
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o666)
        except OSError as e:
            self.log("sync lock not openable:", repr(e))
            return False
        deadline = time.monotonic() + self.wait_s
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return True
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EACCES):
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
        except BaseException:
            # A second Ctrl-C / SIGTERM while waiting: no fd leak.
            os.close(fd)
            raise
        os.close(fd)
        self.log("sync lock busy -- carrying on without it")
        return False

    def release(self) -> None:
        """Clean stop: marker away, lock released."""
        try:
            self.marker.unlink()
        except OSError:
            pass
        self.release_lock()

    def release_lock(self) -> None:
        """End of a restart window: the lock goes, the marker stays for the
        whole run (it says "a consumer is writing here")."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
            self._fd = None
        self.locked = False
