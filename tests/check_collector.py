# CoThinker — collector for ported characterization checks
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Collects the named checks of a characterization module.

The wiki and graph characterization tests build one synthetic project at
import time and then evaluate many named checks against it. A module calls
`check(name, cond, info)` while it is imported; this collector records each
call, and the module turns every recorded check into one pytest case.
"""
from __future__ import annotations

import os
import tempfile

# Keep every test off a production sync lock.
os.environ.setdefault(
    "COTHINKER_SYNC_LOCK",
    os.path.join(tempfile.gettempdir(), f"cothinker-test-sync-{os.getpid()}.lock"))


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, cond: bool, info: str = "") -> None:
        self.results.append((name, bool(cond), info))

    def params(self) -> list:
        import pytest
        return [pytest.param(name, ok, info, id=f"{i:03d}-{name[:60]}")
                for i, (name, ok, info) in enumerate(self.results)]
