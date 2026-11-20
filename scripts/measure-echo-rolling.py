#!/usr/bin/env python3
# CoThinker — echo rolling-window cadence measurement
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""MUST #4: additions per 10 minutes and median latency,
BEFORE (the accumulate-then-reset window_s/pause_s buffer, one bullet per
60s talk or 8s pause) vs AFTER (the rolling step_s/window_s check,
checked every ~15s of new talk) this change.

Same technique as `scripts/measure-infomaniak-load.py` and the offline
harness `tests/test_workshop_echo.py` already use: a scripted feed of
INVENTED STT finals against a fake/stand-in chat client, no network, no
key (this sandbox has none, same constraint
`docs/messungen/2026-10-27-infomaniak-rate-budget.md` §7 documents).

"BEFORE" is not reimplemented by hand -- it is the real `EchoHook` as it
stood at the commit right before this card's own first commit
(`OLD_REV` below, `workshop/echo.py` pre-card), loaded straight from git
history via `git show`, so the comparison is against the actual prior
code, not a paraphrase of it. "AFTER" is the current `workshop.echo`.

The fake chat client always answers "yes, a new bullet" the instant it is
asked (fixed small latency with a little jitter) -- this isolates the
CADENCE this card changes (how often a check fires) from the model's own
judgement quality (whether a fired check's content is actually novel),
which `tests/test_workshop_echo.py`'s MUST #2 cases already cover
separately. So "additions/10min" here is an upper bound: how many checks
fired, assuming every one of them is accepted as a genuinely new thought.

    python3 scripts/measure-echo-rolling.py
    python3 scripts/measure-echo-rolling.py --minutes 10 --gap-s 1.5 --long-gap-s 4.0 --long-gap-every 10
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from gateway.infomaniak_chat import ChatReply  # noqa: E402

# workshop/echo.py as it stood right before this card's first commit (77617bf) --
# the one-shot accumulate-then-reset window_s/pause_s buffer.
OLD_REV = "3ded219"


def _load_old_echo_module():
    src = subprocess.run(["git", "show", f"{OLD_REV}:workshop/echo.py"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
    tmp.write(src)
    tmp.close()
    spec = importlib.util.spec_from_file_location("workshop_echo_before", tmp.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class FakeEv:
    kind: str
    text: str = ""


class FakeHub:
    def __init__(self) -> None:
        self.t = 0.0

    def wall_ms(self) -> float:
        return self.t

    @property
    def loop(self):
        return self

    def call_soon_threadsafe(self, fn) -> None:
        fn()


class FakeTable:
    def __init__(self, nn: str = "01") -> None:
        self.nn = nn
        self.step = "sort"
        self.hub = FakeHub()
        self.echo_calls: list = []

    def names(self) -> dict:
        return {}

    def mask_names(self) -> dict:
        return {}

    def add_echo(self, text: str, persons) -> None:
        self.echo_calls.append((text, list(persons)))


class FakeChatClient:
    """Answers the instant it is asked: "yes, a new bullet" either shape
    (old `{"bullet": ...}` or this card's `{"new": bool, "bullet": ...}`,
    told apart by the schema's own `properties`). Fixed latency plus a
    little deterministic jitter, same spirit as
    `scripts/measure-infomaniak-load.py`'s canned-reply stand-in."""

    def __init__(self, *, base_latency_s: float = 0.03) -> None:
        self.base_latency_s = base_latency_s
        self.calls = 0
        self.latencies: List[float] = []

    def chat(self, model, system, user, schema, **kw):
        self.calls += 1
        latency_s = self.base_latency_s + 0.01 * ((self.calls * 37) % 5) / 5.0
        self.latencies.append(latency_s)
        body = {"bullet": "Ergänzung der Gruppe."}
        if "new" in schema.get("properties", {}):
            body["new"] = True
        return ChatReply(text=json.dumps(body), latency_s=latency_s, model=model)


def _talk_script(total_s: float, gap_s: float, long_gap_s: float, long_gap_every: int) -> List[Tuple[float, str, str]]:
    """`(dt_s, person, text)` tuples -- INVENTED sentences, continuous group
    chatter at `gap_s` between utterances, a longer `long_gap_s` pause every
    `long_gap_every`th utterance (a turn change, say)."""
    persons = ("P1", "P2", "P3")
    words = ("die Gruppe spricht weiter über die Details des Projekts heute",
            "noch ein Gedanke zur Probebühne und zum Fundus insgesamt",
            "das war schon einmal angesprochen aber es passt wieder hier",
            "ein neuer Punkt zur Zeitplanung für die kommende Woche")
    out: List[Tuple[float, str, str]] = []
    t, i = 0.0, 0
    while t < total_s:
        gap = long_gap_s if (i + 1) % long_gap_every == 0 else gap_s
        out.append((gap, persons[i % len(persons)], f"{words[i % len(words)]} {i}"))
        t += gap
        i += 1
    return out


def _advance_and_feed(hook, clock: dict, talk: List[Tuple[float, str, str]], *,
                      real_pace_s: float = 0.02, settle_s: float = 1.0) -> FakeTable:
    """Drives `talk` through `hook` on a virtual clock. `hook.slot`
    (`gateway/lanes.py` `TaskSlot`) runs in REAL wall time on its own
    thread and keeps only "1 running + 1 waiting, newest wins" -- feeding
    all of `talk` with no real-time gap between calls would coalesce
    every check this scenario fires down to whichever one submits last,
    which is a property of the queue, not of this card's cadence change.
    `real_pace_s` of real sleep after each simulated utterance gives that
    thread real wall-clock room to actually run and clear the slot
    between submissions, the same room a live meeting gives it between
    utterances."""
    table = FakeTable()
    for dt, person, text in talk:
        clock["t"] += dt
        hook.on_stt(table, FakeEv(kind="final", text=text), person, (0, 1))
        hook.on_stt(table, FakeEv(kind="eou"), None, None)
        time.sleep(real_pace_s)
    time.sleep(settle_s)
    hook.stop()
    return table


def measure_before(old_mod, talk, minutes: float) -> dict:
    client = FakeChatClient()
    clock = {"t": 0.0}
    hook = old_mod.EchoHook(window_s=60.0, pause_s=8.0, min_words=8, client=client, now=lambda: clock["t"])
    table = _advance_and_feed(hook, clock, talk)
    additions = len(table.echo_calls)
    return {"additions": additions, "additions_per_10min": additions / (minutes / 10.0),
           "calls": client.calls, "median_latency_s": statistics.median(client.latencies) if client.latencies else None}


def measure_after(new_mod, talk, minutes: float) -> dict:
    client = FakeChatClient()
    clock = {"t": 0.0}
    hook = new_mod.EchoHook(step_s=15.0, window_s=45.0, pause_s=3.0, min_words=8,
                            client=client, now=lambda: clock["t"])
    table = _advance_and_feed(hook, clock, talk)
    additions = len(table.echo_calls)
    return {"additions": additions, "additions_per_10min": additions / (minutes / 10.0),
           "calls": client.calls, "median_latency_s": statistics.median(client.latencies) if client.latencies else None}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=10.0, help="simulated conversation length")
    ap.add_argument("--gap-s", type=float, default=1.5, help="normal gap between utterances")
    ap.add_argument("--long-gap-s", type=float, default=4.0, help="an occasional longer gap (turn change)")
    ap.add_argument("--long-gap-every", type=int, default=10, help="every Nth utterance gets the long gap")
    a = ap.parse_args(argv)

    talk = _talk_script(a.minutes * 60.0, a.gap_s, a.long_gap_s, a.long_gap_every)
    print(f"talk script: {len(talk)} utterances over {a.minutes} simulated minutes "
         f"(gap {a.gap_s}s, every {a.long_gap_every}th utterance {a.long_gap_s}s)")

    import workshop.echo as new_mod
    old_mod = _load_old_echo_module()

    before = measure_before(old_mod, talk, a.minutes)
    after = measure_after(new_mod, talk, a.minutes)

    print(json.dumps({"before": before, "after": after}, indent=2))
    print(f"\nBEFORE (window_s=60s, pause_s=8s): {before['additions_per_10min']:.1f} additions/10min, "
         f"median latency {before['median_latency_s']:.3f}s ({before['calls']} calls)")
    print(f"AFTER  (step_s=15s, window_s=45s, pause_s=3s): {after['additions_per_10min']:.1f} additions/10min, "
         f"median latency {after['median_latency_s']:.3f}s ({after['calls']} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
