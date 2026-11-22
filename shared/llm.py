# CoThinker - the LLM port blocks use
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The one-call LLM port. Stdlib only.

A block sees only this protocol; the host decides which provider sits
behind it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LlmPort(Protocol):
    def complete(self, system: str, user: str, *, timeout_s: float = 60) -> str:
        """Return the model's text answer; raise on failure."""
        ...
