#!/usr/bin/env python3
"""Score speaker attribution of the consumer's transcript against script.json.

Transcript lines look like `- **16:28** *Alex:* Guten. Tag. ...` (the
`## Rohtranskript` section of the consumer's protokoll.md). A line counts
as matched when it contains a script keyword; it is correct when the
attributed speaker equals the expected speaker of every keyword it holds.
Lines without a keyword are ignored.

usage: score.py --transcript protokoll.md --script script.json [--min-ratio 0.9]
Exit 0 when ratio >= min-ratio, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

LINE_RE = re.compile(r"^\s*-\s+\*\*(\d{1,2}:\d{2})\*\*\s+\*(.+?):\*\s*(.*)$")


def parse_line(line: str):
    """-> (speaker, text) or None."""
    m = LINE_RE.match(line)
    return (m.group(2).strip(), m.group(3)) if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", "", s.lower())


def score(transcript_lines, script: dict) -> dict:
    keyword_owner = {}
    for ev in script["events"]:
        for kw in ev["keywords"]:
            keyword_owner[_norm(kw)] = ev["speaker"].lower()
    details = []
    for raw in transcript_lines:
        parsed = parse_line(raw)
        if not parsed:
            continue
        speaker, text = parsed
        flat = _norm(text)
        hits = sorted(kw for kw in keyword_owner if kw in flat)
        if not hits:
            details.append({"speaker": speaker, "keywords": [],
                            "matched": False, "correct": False})
            continue
        expected = {keyword_owner[k] for k in hits}
        ok = expected == {speaker.lower()}
        details.append({"speaker": speaker, "keywords": hits,
                        "expected": sorted(expected), "matched": True,
                        "correct": ok})
    matched = sum(d["matched"] for d in details)
    correct = sum(d["correct"] for d in details)
    return {"lines": len(details), "matched": matched, "correct": correct,
            "ratio": (correct / matched) if matched else 0.0,
            "details": details}


def passes(result: dict, min_ratio: float = 0.9) -> bool:
    return result["matched"] > 0 and result["ratio"] >= min_ratio


def raw_transcript_lines(text: str) -> list[str]:
    """Lines of the `## Rohtranskript` section, or all lines if absent."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.strip().lower().startswith("## rohtranskript")), None)
    if start is None:
        return lines
    out = []
    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        out.append(l)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--min-ratio", type=float, default=0.9)
    a = ap.parse_args(argv)
    with open(a.transcript, encoding="utf-8") as fh:
        lines = raw_transcript_lines(fh.read())
    with open(a.script, encoding="utf-8") as fh:
        script = json.load(fh)
    res = score(lines, script)
    print("lines=%d matched=%d correct=%d ratio=%.3f" % (
        res["lines"], res["matched"], res["correct"], res["ratio"]))
    return 0 if passes(res, a.min_ratio) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
