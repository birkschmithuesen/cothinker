"""`Api` moved to `workshop/agent_api.py` (S10, the agent seam's
Hermes adapter) -- re-exported here so `m1_turns.py`, `m2_delegation.py`
and `m3_load.py` keep their `from .api_client import Api` import. The
verified Hermes wire-shape facts live in that module's docstring now.
"""
from __future__ import annotations

from workshop.agent_api import Api

__all__ = ["Api"]
