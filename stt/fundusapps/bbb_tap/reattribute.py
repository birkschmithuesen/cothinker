#!/usr/bin/env python3
# CoThinker — retroactively re-attribute lines written during a clock-offset window
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Issue #202's third ask: a tool that re-attributes lines ALREADY
WRITTEN during an STT-restart clock-offset window, from the same
bbb_tap --per-track recordings `track_speaker_resolver.TrackSpeakerResolver`
uses live.

    python -m fundusapps.bbb_tap.reattribute \\
        --sidecar bbb-120000-tracks.json --timing timing.jsonl \\
        --protokoll protokoll.md --out protokoll-corrected.md

A line's own recognised t_start/t_end on the STT clock cannot be trusted
during the outage this issue describes -- that is the whole bug. What
IS still trustworthy is `at`, the wall-clock instant the line was
logged (`gateway/timing.py`'s `timing.jsonl`), and bbb_tap's own
`start_epoch` anchor in the sidecar (`fundusapps.bbb_tap.__main__.
PerTrack.save()`). `attribute_by_wall_clock` (speaker_attribution.py)
converts straight from those two wall-clock numbers onto the per-track
frame axis, bypassing the offset STT clock entirely.

Deliberately self-contained, not importing `gateway/timing.py` or
`gateway/raw_transcript.py` for the two tiny pieces of their formats
this needs: `gateway/` has no `__init__.py` and is only ever added to
sys.path by `gateway/consumer.py`'s own boot (flat top-level modules,
`import timing`, not `gateway.timing`) -- a different process, usually a
different virtualenv, from the one this package runs in. `_valid_span`
and the raw-transcript line format below are reproduced, not shared;
keep them in sync with `gateway/timing.py`'s `valid_span` and
`gateway/raw_transcript.py`'s `MinutesFile.line` if either changes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from datetime import datetime
from pathlib import Path

from fundusapps.bbb_tap.speaker_attribution import TrackEnergy, attribute_by_wall_clock

#: Frames per energy window when reading a track WAV in full (100 ms at
#: 16 kHz) -- fine enough to tell two words apart, coarse enough to stay
#: fast over a multi-hour recording. `TrackEnergy.add_chunk` records one
#: RMS value per call; reading the whole file in a single call would
#: average a brief loud burst into the silence around it and make every
#: position in the file look the same, defeating the point of ranking
#: BY TIME at all.
_CHUNK_FRAMES = 1600

#: Mirrors `gateway/raw_transcript.py`'s `MinutesFile.line()` output:
#: "- **HH:MM** *speaker:* text" (the "*speaker:* " part absent when the
#: utterance had none).
_RAW_LINE_RE = re.compile(
    r"^- \*\*(?P<stempel>\d{2}:\d{2})\*\* (?:\*(?P<speaker>[^*]+):\* )?(?P<text>.*)$")


def _valid_span(t_start, t_end) -> bool:
    """Same acceptance rule as `gateway/timing.py`'s `valid_span` (not
    imported -- see module docstring): both present, finite, non-negative,
    end >= start."""
    for value in (t_start, t_end):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if value != value or value in (float("inf"), float("-inf")) or value < 0:
            return False
    return t_end >= t_start


def load_tracks(sidecar_path):
    """-> (tracks: {track_n: TrackEnergy}, owners_by_track: {track_n: [...]},
    tap_start_epoch: float|None), read from a bbb_tap --per-track sidecar
    plus its per-track WAVs in full -- this is a retroactive, offline
    tool, not the live tailing `TrackSpeakerResolver` does."""
    sidecar_path = Path(sidecar_path)
    doc = json.loads(sidecar_path.read_text(encoding="utf-8"))
    base_dir = sidecar_path.parent
    tracks: dict[int, TrackEnergy] = {}
    owners_by_track: dict[int, list] = {}
    for key, info in (doc.get("tracks") or {}).items():
        if not isinstance(info, dict):
            continue
        try:
            g = int(key)
        except ValueError:
            continue
        owners_by_track[g] = info.get("owners") or []
        filename = info.get("file")
        if not filename:
            continue
        energy = TrackEnergy()
        with wave.open(str(base_dir / filename), "rb") as wav:
            frame = 0
            while True:
                pcm = wav.readframes(_CHUNK_FRAMES)
                if not pcm:
                    break
                energy.add_chunk(frame, pcm)
                frame += len(pcm) // 2
        tracks[g] = energy
    return tracks, owners_by_track, doc.get("start_epoch")


def load_timing_rows(timing_path) -> list[dict]:
    """One dict per line of a timing.jsonl, in file order -- pause
    markers (`cut == "pause"`) included; `reattribute` is the one that
    drops them."""
    rows = []
    with Path(timing_path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_protokoll_lines(protokoll_text: str) -> list[dict]:
    """Raw-transcript lines only (`- **HH:MM** *speaker:* text`), in file
    order, as `{"line_no", "stempel", "speaker", "text"}` -- `line_no` is
    the 0-based index into `protokoll_text.splitlines()`, needed by
    `apply_corrections` to rewrite exactly those lines back. Everything
    else in protokoll.md (headings, minutes sections) does not match this
    format and is skipped."""
    out = []
    for line_no, line in enumerate(protokoll_text.splitlines()):
        m = _RAW_LINE_RE.match(line)
        if m:
            out.append({"line_no": line_no, "stempel": m["stempel"],
                       "speaker": m["speaker"], "text": m["text"]})
    return out


def reattribute(timing_rows, raw_lines, tracks, owners_by_track, tap_start_epoch):
    """Pairs each REAL utterance timing row (pause markers dropped) with
    its raw-transcript line IN ORDER: both are appended exactly once per
    final utterance, from the same consumer.py call path, so once
    markers are dropped the two sequences line up 1:1. Returns one dict
    per line whose re-derived speaker actually differs from what was
    written (`{"line_no", "stempel", "text", "old_speaker",
    "new_speaker"}`) -- a line with no usable span, or whose track
    energy still resolves to nothing, is left alone.

    Raises ValueError on a count mismatch rather than guessing: a silent
    misalignment here would misattribute every later line, not just the
    one with no span.
    """
    utterances = [r for r in timing_rows if r.get("cut") != "pause"]
    if len(utterances) != len(raw_lines):
        raise ValueError(
            f"{len(utterances)} timing.jsonl utterance row(s) vs "
            f"{len(raw_lines)} protokoll.md raw line(s) -- not 1:1, refusing to guess")
    out = []
    for row, raw in zip(utterances, raw_lines):
        t_start, t_end = row.get("t_start"), row.get("t_end")
        if not _valid_span(t_start, t_end):
            continue
        at = datetime.fromisoformat(row["at"]).timestamp()
        new_speaker = attribute_by_wall_clock(
            tracks, owners_by_track, at, t_end - t_start, tap_start_epoch)
        if not new_speaker or new_speaker == raw["speaker"]:
            continue
        out.append({"line_no": raw["line_no"], "stempel": raw["stempel"], "text": raw["text"],
                   "old_speaker": raw["speaker"], "new_speaker": new_speaker})
    return out


def apply_corrections(protokoll_text: str, corrections) -> str:
    """`protokoll_text` with only the `new_speaker` of each correction's
    `line_no` swapped in -- stempel and text untouched, every other line
    byte-identical. Mirrors `MinutesFile.line()`'s own format (see module
    docstring)."""
    lines = protokoll_text.splitlines()
    for c in corrections:
        wer = f"*{c['new_speaker']}:* " if c["new_speaker"] else ""
        lines[c["line_no"]] = f"- **{c['stempel']}** {wer}{c['text']}"
    text = "\n".join(lines)
    return text + "\n" if protokoll_text.endswith("\n") else text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sidecar", required=True,
                    help="bbb_tap --per-track sidecar (bbb-HHMMSS-tracks.json)")
    ap.add_argument("--timing", required=True,
                    help="the same session's timing.jsonl (gateway/timing.py)")
    ap.add_argument("--protokoll", required=True,
                    help="the same session's protokoll.md")
    ap.add_argument("--out", default="",
                    help="write the corrected markdown here; omit to only print a report "
                         "-- --protokoll itself is never modified")
    a = ap.parse_args(argv)

    tracks, owners_by_track, tap_start_epoch = load_tracks(a.sidecar)
    if tap_start_epoch is None:
        print("sidecar has no start_epoch (recorded before issue #202's fix); "
              "nothing to re-attribute", file=sys.stderr)
        return 1

    timing_rows = load_timing_rows(a.timing)
    protokoll_text = Path(a.protokoll).read_text(encoding="utf-8")
    raw_lines = parse_protokoll_lines(protokoll_text)
    corrections = reattribute(timing_rows, raw_lines, tracks, owners_by_track, tap_start_epoch)

    for c in corrections:
        print(f"{c['stempel']} {c['old_speaker']!r} -> {c['new_speaker']!r}: {c['text'][:60]}")
    if not corrections:
        print("no corrections needed")
        return 0

    if a.out:
        Path(a.out).write_text(apply_corrections(protokoll_text, corrections), encoding="utf-8")
        print(f"wrote {len(corrections)} correction(s) to {a.out}", file=sys.stderr)
    else:
        print(f"{len(corrections)} correction(s) found; pass --out to write a corrected "
              f"file ({a.protokoll} is never modified in place)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
