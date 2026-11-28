"""Mechanical oracle: grade one scenario against one observation record.

The oracle holds no thresholds of its own; a numeric bound comes from the
baseline file or an override via ``catalogue.resolve``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalogue import Scenario, resolve
from .report import Check

_END_PUNCT = (".", "!", "?", "…")


@dataclass
class Observation:
    lane_calls: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    transcript_lines: list[tuple[str, str]] = field(default_factory=list)
    agenda_lines: list[str] = field(default_factory=list)
    mode: str = "call"


def _ends_sentence(text: str) -> bool:
    # Strip trailing closing quotes/brackets, then look for sentence punctuation.
    t = text.rstrip().rstrip("\"'“”‘’»«)]")
    return t.endswith(_END_PUNCT)


def share_no_sentence_end(lines: list[str]) -> float:
    """Share of non-empty lines that do not end with sentence-ending punctuation."""
    texts = [ln.strip() for ln in lines if ln and ln.strip()]
    if not texts:
        return 1.0
    return sum(1 for t in texts if not _ends_sentence(t)) / len(texts)


def _missing(keywords, haystack: str) -> list[str]:
    low = haystack.lower()
    return [k for k in keywords if k.lower() not in low]


def _lane_calls(cid: str, body: dict, obs: Observation) -> Check:
    problems = []
    if "count" in body and body["count"] != len(obs.lane_calls):
        problems.append(f"expected {body['count']} lane call(s), observed {len(obs.lane_calls)}")
    if body.get("keywords_all"):
        if obs.mode == "transcript":
            source, haystack = "lane calls", "\n".join(obs.lane_calls)
        else:
            source, haystack = "answer", "\n".join(obs.answers)
        miss = _missing(body["keywords_all"], haystack)
        if miss:
            problems.append(f"{source} missing keyword(s): {', '.join(miss)}")
    return Check(cid, "FAIL", "; ".join(problems)) if problems else Check(cid, "PASS", f"{len(obs.lane_calls)} lane call(s)")


def _answer(cid: str, body: dict, obs: Observation) -> Check:
    if obs.mode == "transcript":
        return Check(cid, "SKIPPED", "answers are not observable in transcript mode")
    if not any(a.strip() for a in obs.answers):
        return Check(cid, "FAIL", "no answer observed")
    text = "\n".join(obs.answers)
    low = text.lower()
    problems = []
    bad = [m for m in body.get("must_not", []) if m.lower() in low]
    if bad:
        problems.append(f"answer contains forbidden text: {', '.join(repr(b) for b in bad)}")
    miss = _missing(body.get("keywords_all", []), text)
    if miss:
        problems.append(f"answer missing keyword(s): {', '.join(miss)}")
    if body.get("agenda_line"):
        if not obs.agenda_lines:
            if not problems:
                return Check(cid, "SKIPPED", "no agenda lines known")
        elif not any(a.lower() in low for a in obs.agenda_lines if a.strip()):
            problems.append("answer contains none of the known agenda lines")
    return Check(cid, "FAIL", "; ".join(problems)) if problems else Check(cid, "PASS", "answer ok")


def _topic(cid: str, body: dict, obs: Observation) -> Check:
    if not obs.topics:
        return Check(cid, "SKIPPED", "no topic observed")
    hits = []
    for topic in obs.topics:
        for bad in body.get("must_not_contain", []):
            if bad.lower() in topic.lower():
                hits.append(f"topic {topic!r} contains {bad!r}")
    if hits:
        return Check(cid, "FAIL", "; ".join(hits))
    return Check(cid, "PASS", f"{len(obs.topics)} topic(s) clean")


def _transcript(cid: str, body: dict, obs: Observation, baseline: dict, overrides: dict) -> Check:
    if not obs.transcript_lines:
        return Check(cid, "FAIL", "no transcript lines")
    problems = []
    if body.get("mid_sentence_max") is not None:
        bound = resolve(body["mid_sentence_max"], baseline, overrides)
        share = share_no_sentence_end([t for _, t in obs.transcript_lines])
        if share > bound:
            problems.append(f"share without sentence end {share:.2f} exceeds bound {bound}")
    speakers = {s for s, _ in obs.transcript_lines}
    miss_sp = [s for s in body.get("speakers_all", []) if s not in speakers]
    if miss_sp:
        problems.append(f"missing speaker(s): {', '.join(miss_sp)}")
    miss_kw = _missing(body.get("keywords_all", []), "\n".join(t for _, t in obs.transcript_lines))
    if miss_kw:
        problems.append(f"transcript missing keyword(s): {', '.join(miss_kw)}")
    if problems:
        return Check(cid, "FAIL", "; ".join(problems))
    return Check(cid, "PASS", f"{len(obs.transcript_lines)} transcript line(s) ok")


def grade(s: Scenario, obs: Observation, baseline: dict, overrides: dict) -> list[Check]:
    checks: list[Check] = []
    for group in ("lane_calls", "answer", "topic", "transcript"):
        body = s.expect.get(group)
        if body is None:
            continue
        cid = f"scenario.{s.id}.{group}"
        if group == "lane_calls":
            checks.append(_lane_calls(cid, body, obs))
        elif group == "answer":
            checks.append(_answer(cid, body, obs))
        elif group == "topic":
            checks.append(_topic(cid, body, obs))
        else:
            checks.append(_transcript(cid, body, obs, baseline, overrides))
    return checks
