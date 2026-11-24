"""Operator token auth (issue #159): a separate token from the
participant/room surface, never shared with it. Read from a file given
by flag/env (`surfaces/operator/__main__.py`), compared in constant
time, never logged -- same pattern as `stage/stage.py`'s `_token_ok`
and `demo/session_server.py`'s `check_token`.
"""
from __future__ import annotations

import secrets
from pathlib import Path


def load_token(path: str | Path) -> str:
    """The token file's content, trailing whitespace stripped."""
    return Path(path).read_text(encoding="utf-8").strip()


def check_token(candidate: str | None, expected: str) -> bool:
    """`True` only for a non-empty `candidate` matching `expected`.

    Compared as UTF-8 bytes, not as `str`: `secrets.compare_digest` raises
    `TypeError` on a non-ASCII `str` operand, which would turn a probe
    into a 500 instead of the intended 401.
    """
    if not candidate or not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
