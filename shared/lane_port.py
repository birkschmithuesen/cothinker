# CoThinker — shared/lane_port.py: the agent lane a block reaches through its context
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`LanePort`: the one door from a block to the agent transport.

The agent transport is a core service (Agent API, ADR 0005,
`docs/architecture/target.md` §2.2). A block never imports the session
host; the host hands it a `LanePort` as `BlockContext.agent`, and the block
only submits prompts through it.

Contract:

- `submit` never blocks. One job runs, at most one waits; a newer request
  replaces the waiting one ("newest wins"), and the replaced request is
  reported through its `on_dropped` -- it never runs.
- `on_result(result, duration_s)` gets the lane's result dict
  (`ok`, `final`, `error`, ...; a turn that outlived its budget also carries
  `timed_out: True`) and the turn's duration in seconds.
- `on_dropped()` is called when the job was replaced or the lane stopped
  with the job still waiting.
- Both callbacks run on the lane's own thread, not the caller's.
- `stats()` is a plain dict of lane counters (`jobs_run`, `jobs_dropped`, ...).

Stdlib only.
"""
from __future__ import annotations

from typing import Callable, Protocol


class LanePort(Protocol):
    def submit(self, job_id: str, prompt: str, *,
               on_result: Callable[[dict, float], None],
               on_dropped: Callable[[], None]) -> None: ...

    def stats(self) -> dict: ...
