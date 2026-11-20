#!/usr/bin/env python3
# CoThinker — real-audio probe for the in-utterance speaker split
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Run gateway/speaker_split.py against a real session recording.

    env -u PYTHONPATH python3 scripts/speaker-split-probe.py \\
        --wav <session.wav> --timing <session>/timing.jsonl \\
        --enroll Alice:1794.404:1809.92 --enroll Bob:1811.2:1830.018 \\
        [--only 1918.484,1967.39] [--fixture-out out.json]

Prints NUMBERS ONLY, one line per utterance >= --min-s: audio-clock
start, duration, window labels (one initial per window, '?' unknown),
the resulting turns (name initial + cut offset), embed calls and wall
time. Never prints transcript text -- timing.jsonl carries none, and this
script reads nothing else from the session.

`--fixture-out` writes the enrolled vectors plus every window embedding
of the probed utterances as JSON (numbers only), the shape
tests/fixtures/speaker_split_2026-10-20.json uses.

Needs SPEAKER_EMBED_URL / SPEAKER_EMBED_SECRET in the environment (or
`--env-file`, read without echoing anything).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

from speaker_enroll import EmbeddingClient, SpeakerEnrollment, cut_clip  # noqa: E402
from speaker_split import (MIN_UTTERANCE_S, SpeakerSplitter,  # noqa: E402
                           window_starts, WINDOW_S)


def _load_env(path: str) -> None:
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        for key in ("SPEAKER_EMBED_URL", "SPEAKER_EMBED_SECRET"):
            if line.startswith(key + "=") and not os.environ.get(key):
                os.environ[key] = line.split("=", 1)[1].strip().strip("'\"")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wav", required=True)
    ap.add_argument("--timing", required=True)
    ap.add_argument("--enroll", action="append", default=[],
                    help="NAME:T_START:T_END on the audio clock")
    ap.add_argument("--only", default="",
                    help="comma-separated t_start values to probe")
    ap.add_argument("--min-s", type=float, default=MIN_UTTERANCE_S)
    ap.add_argument("--env-file", default="")
    ap.add_argument("--fixture-out", default="")
    a = ap.parse_args()
    if a.env_file:
        _load_env(a.env_file)
    url = os.environ.get("SPEAKER_EMBED_URL", "")
    if not url:
        print("SPEAKER_EMBED_URL not set", file=sys.stderr)
        return 2
    client = EmbeddingClient(url, os.environ.get("SPEAKER_EMBED_SECRET", ""))
    store = SpeakerEnrollment()
    fixture = {"window_s": WINDOW_S, "enrolled": {}, "utterances": []}
    for spec in a.enroll:
        name, t0, t1 = spec.rsplit(":", 2)
        vec = client.embed(cut_clip(a.wav, float(t0), float(t1)))
        store.enroll(name, vec)
        fixture["enrolled"][name] = vec
    only = {round(float(x), 3) for x in a.only.split(",") if x.strip()}

    recorded: dict = {}

    def embed_fn(t0, t1):
        vec = client.embed(cut_clip(a.wav, t0, t1))
        recorded[round(t0, 3)] = vec
        return vec

    splitter = SpeakerSplitter(store, embed_fn=embed_fn, min_utterance_s=a.min_s,
                               log=lambda *x: None)
    rows = [json.loads(l) for l in Path(a.timing).read_text().splitlines()
            if l.strip()]
    rows = [r for r in rows if r.get("t_start") is not None]
    t_wall = time.monotonic()
    n_split = n_probe = 0
    lat = []
    for r in rows:
        ts, te = float(r["t_start"]), float(r["t_end"])
        if only and round(ts, 3) not in only:
            continue
        if te - ts < a.min_s:
            continue
        recorded.clear()
        # Proportional text split needs some text; the probe has none, so
        # a neutral placeholder of the logged length stands in.
        dummy = " ".join(["x"] * max(2, int(r.get("chars") or 40) // 2))
        turns, st = splitter.split(dummy, ts, te)
        n_probe += 1
        lat.append(st.ms)
        cut = (" | " + " ".join(f"{t.name[:1]}@{t.t_start - ts:.2f}"
                                for t in turns)) if turns else ""
        if turns:
            n_split += 1
        print(f"{ts:9.3f} {te - ts:5.1f}s {st.labels:<28} {st.outcome:<7} "
              f"calls={st.embed_calls:2d} ms={st.ms}{cut}")
        fixture["utterances"].append({
            "t_start": ts, "duration": round(te - ts, 3),
            "windows": [recorded.get(round(ts + s, 3))
                        for s in window_starts(te - ts)],
            "expected": [t.name for t in turns] if turns else None})
    wall = time.monotonic() - t_wall
    splitter.close()
    lat.sort()
    print(f"probed={n_probe} split={n_split} embed_calls={splitter.embed_calls} "
          f"wall_s={wall:.1f} median_ms={lat[len(lat) // 2] if lat else 0} "
          f"max_ms={lat[-1] if lat else 0}")
    if a.fixture_out:
        Path(a.fixture_out).write_text(json.dumps(fixture))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
