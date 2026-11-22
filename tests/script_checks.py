"""Recorder behind the script-style tests of the decisions block.

The tests were written as top-level scripts that call ``check(name, cond)``.
Importing this module in place of the old stand-alone harness lets pytest
collect them: ``check`` records every result, and ``collected()`` turns the
recorded results into ``pytest.param`` items, one test item per check, so no
assertion is merged or dropped.
"""
from __future__ import annotations

import os
import tempfile
import traceback

import pytest

# A real consumer a test starts inherits os.environ: keep it off the
# production sync lock.
os.environ.setdefault(
    "COTHINKER_SYNC_LOCK",
    os.path.join(tempfile.gettempdir(), f"cothinker-test-sync-{os.getpid()}.lock"))

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, info: str = "") -> None:
    _results.append((name, bool(cond), info))


def check_no_exception(name: str, fn, *a, **kw):
    try:
        out = fn(*a, **kw)
    except Exception:
        check(name, False, traceback.format_exc(limit=2).replace("\n", " | ")[:200])
        return None
    check(name, True)
    return out


def done() -> None:
    """Kept so the script body reads as before; reporting is pytest's job."""


def collected() -> list:
    """Drain the recorded checks as pytest params, in execution order.

    Draining keeps one test file's checks out of the next file's items when
    several files are collected in one session.
    """
    params = [pytest.param(name, ok, info, id=f"{i:03d}-{name[:70]}")
              for i, (name, ok, info) in enumerate(_results)]
    _results.clear()
    return params
