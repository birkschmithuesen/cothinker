#!/usr/bin/env python3
"""Content-oracle acceptance scenarios against a running deployment.

Each scenario starts `participant.py` (reused as a subprocess, never
forked) with its own WAV, waits for the audio length plus `--timeout`,
then checks the deployment's OWN evidence of what happened -- never just
"an answer appeared". Prints one `PASS|FAIL|SKIPPED <scenario>: <evidence>`
line per scenario and exits 1 if any FAIL (SKIPPED is exit-neutral).

Audio layout, fixed and never committed: `<audio-dir>/<scenario>.wav` plus
an optional `<audio-dir>/<scenario>.json` (`{"keywords": [...], "segments":
N}`). A scenario is SKIPPED, not FAIL, when its WAV is missing.

Evidence sources, and why:

- a (wake + pause + content + "Robo, Ende"): a call is
  `gateway/consumer.py` logging a wake-worded block -- "... — ORDER" in
  both the serial path (`log(f"  block #{n} (...) — ORDER")`) and the
  lanes path (`... — ORDER (lane)`); `count_calls()` counts those lines
  in `--consumer-log`. The spoken content is read from the session's
  `protokoll.md` raw transcript (same file `score.py` scores), since the
  consumer log never prints a block's full text. If the snapshot carries
  `listen` (issue #180, not merged everywhere yet), its `state` is also
  checked during and after the call; absent, that part is SKIPPED and the
  scenario verdict rests on the call count and the keywords alone.
- b (file request, "Robo, lies die Agenda vor"): the agent's answer is
  `StageStateSnapshot.call_answer` -- "the last call-and-answer line, if
  any" (`surfaces/contract/models.py`), itself read from `buehne.json`,
  which `gateway/consumer.py`'s `StageWriter` is the only writer of. Read
  through `--snapshot-url`/`--snapshot-file`, the same endpoint
  `preflight.py` already uses.
- c (natural pauses, no scripted content): finals are read from the
  session's `protokoll.md` `## Rohtranskript` section (`raw_transcript_texts()`,
  reusing `score.py`'s own line parser) -- not the consumer log's
  per-final line (`log(f"[{n}] {text[:80]}")`), which truncates at 80
  characters and would corrupt the sentence-ending check.
- d (wake sentence as the session's first utterance): neither the
  snapshot's `headline` nor any topic-block heading of `protokoll.md`
  (`### HH:MM <title>`, filed by `gateway/consumer.py`'s topic-page
  machinery) may start with the wake phrase.

usage: scenarios.py --audio-dir DIR --url-file F --cdp-port N
         --snapshot-url U --token-file T --consumer-log P --session-dir D
         [--scenarios a,b,c,d] [--wake-phrase "Hey Robo"]
         [--mid-sentence-max 0.2] [--agenda-file PATH] [--timeout 90]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_ACC_DIR = Path(__file__).resolve().parent
if str(_ACC_DIR) not in sys.path:
    sys.path.insert(0, str(_ACC_DIR))
import score as _score  # noqa: E402
from preflight import load_snapshot  # noqa: E402

ALL_SCENARIOS = ("a", "b", "c", "d")
DEFAULT_WAKE_PHRASE = "Hey Robo"
DEFAULT_MID_SENTENCE_MAX = 0.2
DEFAULT_TIMEOUT = 90.0
DEFAULT_A_KEYWORDS = ["Infrastruktur", "Server", "Oberflaechen"]


@dataclass
class Check:
    status: str  # PASS | FAIL | SKIPPED
    name: str
    text: str

    def line(self) -> str:
        return f"{self.status} {self.name}: {self.text}"


# ----------------------------------------------------------------- scenario a

def count_calls(log_text: str) -> int:
    """Lines marking a wake-worded block: "... — ORDER" (serial path) or
    "... — ORDER (lane)" (lanes path), `gateway/consumer.py`."""
    return sum(1 for line in log_text.splitlines() if "— ORDER" in line)


def transcript_contains_keywords(transcript_text: str, keywords: list) -> list:
    """-> keywords NOT found (case-insensitive, punctuation-insensitive) in
    `transcript_text`. Empty list means every keyword was found."""
    flat = re.sub(r"[^\w]+", "", transcript_text.lower())
    missing = []
    for kw in keywords:
        norm = re.sub(r"[^\w]+", "", kw.lower())
        if norm not in flat:
            missing.append(kw)
    return missing


def _listen_ok(during, after) -> Optional[str]:
    """None when both snapshots carry `listen` and its states are as
    expected; else the problem text. Caller treats a missing `listen` key
    on EITHER snapshot as "not merged yet" (issue #180), not a failure."""
    listen_during = (during or {}).get("listen")
    listen_after = (after or {}).get("listen")
    if listen_during is None or listen_after is None:
        return None
    during_state = listen_during.get("state")
    after_state = listen_after.get("state")
    if during_state != "listening" or after_state != "idle":
        return (f"listen.state during={during_state!r} "
                f"(expected 'listening') after={after_state!r} "
                f"(expected 'idle')")
    return None


def evaluate_scenario_a(*, log_text: str, transcript_text: str, keywords: list,
                        snapshot_during, snapshot_after) -> Check:
    name = "a"
    calls = count_calls(log_text)
    missing = transcript_contains_keywords(transcript_text, keywords)
    if calls != 1 or missing:
        parts = []
        if calls != 1:
            parts.append(f"calls={calls} (expected 1)")
        if missing:
            parts.append(f"missing keywords: {missing}")
        return Check("FAIL", name, "; ".join(parts))
    listen_during = (snapshot_during or {}).get("listen")
    listen_after = (snapshot_after or {}).get("listen")
    if listen_during is None or listen_after is None:
        return Check("PASS", name,
                      f"calls=1 keywords={keywords}; listen: SKIPPED "
                      f"(field not in snapshot (feature not merged yet))")
    problem = _listen_ok(snapshot_during, snapshot_after)
    if problem:
        return Check("FAIL", name, problem)
    return Check("PASS", name,
                  f"calls=1 keywords={keywords}; listen ok "
                  f"(during={listen_during.get('state')} "
                  f"after={listen_after.get('state')})")


# ----------------------------------------------------------------- scenario b

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def agenda_lines(agenda_text: str) -> list:
    return [normalize(l) for l in agenda_text.splitlines() if normalize(l)]


def matches_agenda(call_answer: str, agenda_text: str, min_len: int = 15) -> Optional[str]:
    """The first normalised agenda line (>= `min_len` chars) found inside
    `call_answer`, normalised the same way; `None` if none matches."""
    answer_norm = normalize(call_answer)
    for line in agenda_lines(agenda_text):
        if len(line) >= min_len and line in answer_norm:
            return line
    return None


def evaluate_scenario_b(*, call_answer: str, agenda_text: str) -> Check:
    name = "b"
    matched = matches_agenda(call_answer, agenda_text)
    if matched is None:
        return Check("FAIL", name,
                      f"no agenda line found in call_answer={call_answer!r}")
    return Check("PASS", name, f"call_answer contains agenda line {matched!r}")


# ----------------------------------------------------------------- scenario c

def raw_transcript_texts(protocol_text: str) -> list:
    """Every final's bare text from `## Rohtranskript` (`score.py`'s own
    section finder and line parser)."""
    texts = []
    for raw in _score.raw_transcript_lines(protocol_text):
        parsed = _score.parse_line(raw)
        if parsed:
            texts.append(parsed[1])
    return texts


def mid_sentence_share(texts: list):
    """-> (count, share of texts NOT ending in '.', '!' or '?')."""
    if not texts:
        return 0, 0.0
    bad = sum(1 for t in texts if not t.rstrip().endswith((".", "!", "?")))
    return len(texts), bad / len(texts)


def evaluate_scenario_c(*, protocol_text: str, mid_sentence_max: float,
                        segments: Optional[int] = None) -> Check:
    name = "c"
    texts = raw_transcript_texts(protocol_text)
    count, share = mid_sentence_share(texts)
    info = f"count={count} share={share:.3f} threshold={mid_sentence_max}"
    if segments is not None:
        info += f" |count-segments|={abs(count - segments)}"
    if count == 0:
        return Check("FAIL", name, f"no finals found ({info})")
    if share > mid_sentence_max:
        return Check("FAIL", name, info)
    return Check("PASS", name, info)


# ----------------------------------------------------------------- scenario d

def strip_leading_punct(s: str) -> str:
    return re.sub(r"^[^\w]+", "", s)


def starts_with_wake_phrase(text: str, wake_phrase: str) -> bool:
    t = strip_leading_punct(text).strip().lower()
    w = strip_leading_punct(wake_phrase).strip().lower()
    return bool(w) and t.startswith(w)


_TOPIC_HEADING_RE = re.compile(r"^###\s+\d{1,2}:\d{2}\s+(.+)$")


def topic_titles(protocol_text: str) -> list:
    """Every topic-block heading title (`### HH:MM <title>`) of the
    protocol, in document order."""
    out = []
    for line in protocol_text.splitlines():
        m = _TOPIC_HEADING_RE.match(line.strip())
        if m:
            out.append(m.group(1).strip())
    return out


def evaluate_scenario_d(*, headline: str, titles: list, wake_phrase: str) -> Check:
    name = "d"
    offenders = [t for t in [headline, *titles]
                if t and starts_with_wake_phrase(t, wake_phrase)]
    if offenders:
        return Check("FAIL", name, f"starts with the wake phrase: {offenders!r}")
    return Check("PASS", name, f"headline={headline!r} topics={titles!r}")


# -------------------------------------------------------------- orchestration

def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def run_participant(*, url_file: str, name: str, wav: Path, cdp_port: int,
                    seconds: float, join_timeout: float,
                    runner: Callable = subprocess.run):
    script = _ACC_DIR / "participant.py"
    return runner(
        [sys.executable, str(script), "--url-file", str(url_file),
         "--name", name, "--wav", str(wav), "--seconds", str(seconds),
         "--cdp-port", str(cdp_port), "--join-timeout", str(join_timeout)],
        capture_output=True, text=True)


def _scenario_meta(audio_dir: Path, name: str) -> dict:
    meta_path = audio_dir / f"{name}.json"
    if meta_path.is_file():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}


def run_scenario(name: str, args) -> Check:
    audio_dir = Path(args.audio_dir)
    wav = audio_dir / f"{name}.wav"
    if not wav.is_file():
        return Check("SKIPPED", name, f"{wav} not found")
    if name == "b" and not args.agenda_file:
        return Check("SKIPPED", name, "--agenda-file not given")

    meta = _scenario_meta(audio_dir, name)
    duration = wav_duration_s(wav)

    result = run_participant(
        url_file=args.url_file, name=f"Scenario{name.upper()}", wav=wav,
        cdp_port=args.cdp_port, seconds=duration, join_timeout=args.timeout)
    if result.returncode != 0:
        return Check("FAIL", name,
                      f"participant.py failed: {result.stderr.strip()[-300:]}")

    time.sleep(args.timeout)

    consumer_log_text = Path(args.consumer_log).read_text(
        encoding="utf-8", errors="replace")
    protocol_text = (Path(args.session_dir) / "protokoll.md").read_text(
        encoding="utf-8", errors="replace")
    snapshot, _err = load_snapshot(
        snapshot_url=args.snapshot_url, token_file=args.token_file)

    if name == "a":
        keywords = meta.get("keywords", DEFAULT_A_KEYWORDS)
        return evaluate_scenario_a(
            log_text=consumer_log_text, transcript_text=protocol_text,
            keywords=keywords, snapshot_during=snapshot,
            snapshot_after=snapshot)
    if name == "b":
        agenda_text = Path(args.agenda_file).read_text(encoding="utf-8")
        call_answer = (snapshot or {}).get("call_answer", "")
        return evaluate_scenario_b(call_answer=call_answer, agenda_text=agenda_text)
    if name == "c":
        return evaluate_scenario_c(
            protocol_text=protocol_text, mid_sentence_max=args.mid_sentence_max,
            segments=meta.get("segments"))
    return evaluate_scenario_d(
        headline=(snapshot or {}).get("headline", ""),
        titles=topic_titles(protocol_text), wake_phrase=args.wake_phrase)


# ------------------------------------------------------------------------ CLI

def _scenario_list(raw: str) -> list:
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in ALL_SCENARIOS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown scenario name(s) {unknown}; known: {list(ALL_SCENARIOS)}")
    return names


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--snapshot-url", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--consumer-log", required=True)
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--scenarios", type=_scenario_list, default=list(ALL_SCENARIOS))
    ap.add_argument("--wake-phrase", default=DEFAULT_WAKE_PHRASE)
    ap.add_argument("--mid-sentence-max", type=float, default=DEFAULT_MID_SENTENCE_MAX)
    ap.add_argument("--agenda-file")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    checks = [run_scenario(name, args) for name in args.scenarios]
    for c in checks:
        print(c.line())
    return 1 if any(c.status == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
