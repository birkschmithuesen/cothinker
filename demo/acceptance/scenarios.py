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

Catalogue mode (`--catalogue DIR --via call|transcript`) runs the YAML
scenarios of `demo/acceptance/scenarios/` through the mechanical oracle
(`oracle.py`) and an optional capped judge (`judge.py`); see `main_catalogue`.

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
from pathlib import Path
from typing import Callable, Optional

_ACC_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _ACC_DIR.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_ACC_DIR) not in sys.path:
    sys.path.insert(0, str(_ACC_DIR))
from demo.acceptance.report import Check, Report, bind_preflight  # noqa: E402
import score as _score  # noqa: E402
from preflight import load_snapshot  # noqa: E402

ALL_SCENARIOS = ("a", "b", "c", "d")
DEFAULT_WAKE_PHRASE = "Hey Robo"
DEFAULT_MID_SENTENCE_MAX = 0.2
DEFAULT_TIMEOUT = 90.0
DEFAULT_A_KEYWORDS = ["Infrastruktur", "Server", "Oberflaechen"]


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
        return Check(name, "FAIL", "; ".join(parts))
    listen_during = (snapshot_during or {}).get("listen")
    listen_after = (snapshot_after or {}).get("listen")
    if listen_during is None or listen_after is None:
        return Check(name, "PASS",
                      f"calls=1 keywords={keywords}; listen: SKIPPED "
                      f"(field not in snapshot (feature not merged yet))")
    problem = _listen_ok(snapshot_during, snapshot_after)
    if problem:
        return Check(name, "FAIL", problem)
    return Check(name, "PASS",
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
        return Check(name, "FAIL",
                      f"no agenda line found in call_answer={call_answer!r}")
    return Check(name, "PASS", f"call_answer contains agenda line {matched!r}")


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
        return Check(name, "FAIL", f"no finals found ({info})")
    if share > mid_sentence_max:
        return Check(name, "FAIL", info)
    return Check(name, "PASS", info)


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
        return Check(name, "FAIL", f"starts with the wake phrase: {offenders!r}")
    return Check(name, "PASS", f"headline={headline!r} topics={titles!r}")


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
        return Check(name, "SKIPPED", f"{wav} not found")
    if name == "b" and not args.agenda_file:
        return Check(name, "SKIPPED", "--agenda-file not given")

    meta = _scenario_meta(audio_dir, name)
    duration = wav_duration_s(wav)

    result = run_participant(
        url_file=args.url_file, name=f"Scenario{name.upper()}", wav=wav,
        cdp_port=args.cdp_port, seconds=duration, join_timeout=args.timeout)
    if result.returncode != 0:
        return Check(name, "FAIL",
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


# ----------------------------------------------------------- catalogue runner

CLIP_SKIP_EVIDENCE = "scenario uses an audio clip; transcript replay cannot render it"
DEFAULT_BASELINE = _ACC_DIR / "baseline.yaml"
JUDGE_FLAGS = ("model", "endpoint", "key_env", "api", "max_calls", "max_tokens")
_LANE_MODEL_RE = re.compile(r"LANE_MODEL\s*[=:]\s*(\S+)")


def step_starts(timeline, durations: dict):
    """Pure placement of spoken steps on one scenario clock.

    `durations` maps the timeline index of each say/clip step to its audio
    length in seconds. Same rule as `replay.render_finals`: a step starts at
    the running cursor minus its `overlap_prev_s`; the cursor only moves
    forward. -> ({index: start_s}, total_s)."""
    starts: dict = {}
    cursor = 0.0
    for i, step in enumerate(timeline):
        if "pause" in step:
            cursor += float(step["pause"])
            continue
        start = max(0.0, cursor - float(step.get("overlap_prev_s", 0.0)))
        starts[i] = start
        cursor = max(cursor, start + durations[i])
    return starts, cursor


def _parse_threshold(raw: str):
    key, sep, val = raw.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"--threshold wants key=value, got {raw!r}")
    try:
        return key, float(val)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--threshold {key}: {val!r} is not a number")


def lane_model_from_preflight(path) -> Optional[str]:
    """`LANE_MODEL=<x>` in any check evidence of the bound preflight report."""
    if not path:
        return None
    from demo.acceptance.report import load
    for c in load(path).get("checks", []):
        m = _LANE_MODEL_RE.search(str(c.get("evidence", "")))
        if m:
            return m.group(1)
    return None


def _scenario_wavs(s, args, workdir: Path) -> tuple:
    """Per-speaker WAV files for a call run: every say step rendered by piper,
    clip steps read verbatim from `<audio-dir>/<x>.wav`, each placed at its
    scenario time with silence padding. -> ({speaker: path}, total_seconds)."""
    import tempfile
    from demo.acceptance import e2e
    from demo.acceptance import make_tts
    pcm: dict = {}
    with tempfile.TemporaryDirectory() as td:
        for i, step in enumerate(s.timeline):
            if "say" in step:
                voice = args.piper_voice_alex if step["speaker"] == "Alex" else args.piper_voice_sam
                pcm[i] = e2e.render_piper(args.piper_cmd, Path(voice), args.ffmpeg,
                                          step["say"], Path(td))
            elif "clip" in step:
                with wave.open(str(Path(args.audio_dir) / f"{step['clip']}.wav"), "rb") as w:
                    pcm[i] = w.readframes(w.getnframes())
    durations = {i: len(b) / 2 / make_tts.RATE for i, b in pcm.items()}
    starts, total = step_starts(s.timeline, durations)
    items: dict = {}
    for i, b in pcm.items():
        items.setdefault(s.timeline[i]["speaker"], []).append((starts[i], b))
    workdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for who, its in items.items():
        path = workdir / f"{who.lower()}.wav"
        make_tts.write_wav(path, make_tts.place(total, its))
        paths[who] = path
    return paths, total


def apply_gaps(s, paths: dict, baseline: dict) -> str:
    """Inject the scenario's packet gaps into each speaker's WAV, in place.

    -> evidence `gaps: profile=<p> seed=<s> injected=<n>`, or "" when the
    scenario has no `gaps:` block. Each speaker gets its own seed
    (`seed + index`), so two tracks never lose the same samples."""
    from demo.acceptance import audio, make_tts
    if not s.gaps:
        return ""
    name = s.gaps["profile"]
    entry = (baseline.get("gap_profiles") or {}).get(name)
    if entry is None:
        raise ValueError(f"gap profile {name!r} is not in baseline gap_profiles")
    profile = audio.GapProfile.from_baseline(entry)
    seed = int(s.gaps.get("seed", 0))
    injected = 0
    for k, who in enumerate(sorted(paths)):
        with wave.open(str(paths[who]), "rb") as w:
            pcm = w.readframes(w.getnframes())
        out, n = audio.inject_gaps_counted(pcm, make_tts.RATE, profile, seed + k)
        make_tts.write_wav(Path(paths[who]), out)
        injected += n
    return f"gaps: profile={name} seed={seed} injected={injected}"


def fresh_answers(samples, baseline: str) -> list:
    """Answers that belong to this scenario's window.

    `samples` are the successive `call_answer` values polled during the run.
    `call_answer` stays on the snapshot after it was given, so a value equal
    to the pre-scenario one is the previous run's answer, not a new one.
    Consecutive repeats collapse to one entry."""
    out: list = []
    for raw in samples:
        ans = str(raw or "").strip()
        if ans and ans != baseline.strip() and (not out or out[-1] != ans):
            out.append(ans)
    return out


def after_mark(items, mark: int) -> list:
    """The entries added after the first `mark` ones (the pre-scenario state)."""
    return list(items)[mark:]


def history_titles(snap: dict) -> list:
    return [str(h.get("title", "")) for h in (snap or {}).get("history") or []
            if isinstance(h, dict) and h.get("title")]


def _protocol_lines(protocol: str) -> list:
    return [p for p in (_score.parse_line(ln)
                        for ln in _score.raw_transcript_lines(protocol)) if p]


def _read_protocol(args) -> str:
    if not args.session_dir:
        return ""
    return (Path(args.session_dir) / "protokoll.md").read_text(
        encoding="utf-8", errors="replace")


def run_call(s, args, workdir: Path, baseline: Optional[dict] = None):
    """Live call driver. -> Observation, or a Check when it cannot run.

    Every part of the observation is limited to this scenario's own window:
    the state is read before the participants start and only what is new
    afterwards is kept."""
    from demo.acceptance import e2e
    from demo.acceptance.oracle import Observation
    odd = sorted({sp["name"] for sp in s.speakers} - {"Alex", "Sam"})
    if odd:
        return Check(_row_id(s), "FAIL",
                     f"call mode supports only speakers Alex and Sam, got {odd}")
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    paths, total = _scenario_wavs(s, args, workdir)
    audio_evidence = apply_gaps(s, paths, baseline or {}) if s.gaps else ""
    for who in ("Alex", "Sam"):  # start_participants is hard-wired to both
        if who not in paths:
            from demo.acceptance import make_tts
            make_tts.write_wav(workdir / f"{who.lower()}.wav",
                               make_tts.place(total, []))
    log_path = Path(args.consumer_log) if args.consumer_log else None

    def order_lines():
        if not log_path or not log_path.is_file():
            return []
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return [ln for ln in text.splitlines() if "— ORDER" in ln]

    try:
        base_snap = e2e.fetch_snapshot(args.base_url, token)
    except OSError:
        base_snap = {}
    base_answer = str((base_snap or {}).get("call_answer") or "")
    base_history = len(history_titles(base_snap))
    base_protocol = _read_protocol(args)
    base_topics = len(topic_titles(base_protocol))
    base_lines = len(_protocol_lines(base_protocol))
    before = len(order_lines())
    seconds = total + float(s.timeouts["answer_s"])
    procs = e2e.start_participants(args.base_url, token, workdir, args.chrome, seconds)
    samples: list = []
    snap: dict = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            snap = e2e.fetch_snapshot(args.base_url, token)
        except OSError:
            continue
        samples.append(snap.get("call_answer"))
    e2e.collect_participants(procs, token, seconds)
    topics = after_mark(history_titles(snap), base_history)
    lines: list = []
    if args.session_dir:
        protocol = _read_protocol(args)
        topics += [t for t in after_mark(topic_titles(protocol), base_topics)
                   if t not in topics]
        lines = after_mark(_protocol_lines(protocol), base_lines)
    agenda = [str(i.get("title", "")).strip()
              for i in ((snap.get("agenda") or {}).get("items") or [])
              if isinstance(i, dict) and str(i.get("title", "")).strip()]
    if not agenda and args.agenda_file:
        agenda = agenda_lines(Path(args.agenda_file).read_text(encoding="utf-8"))
    # live order text is invisible: one placeholder entry per ORDER log line
    return Observation(lane_calls=after_mark(order_lines(), before),
                       answers=fresh_answers(samples, base_answer), topics=topics,
                       transcript_lines=lines, agenda_lines=agenda, mode="call",
                       audio_evidence=audio_evidence)


def _row_id(s) -> str:
    """Id of a scenario's own row; regression-only scenarios end in `.regression`."""
    return f"scenario.{s.id}.regression" if s.regression_only else f"scenario.{s.id}"


def _spoken(s) -> str:
    return " ".join(str(st["say"]) for st in s.timeline if "say" in st)


def grade_scenario(s, args, baseline, overrides, judge, workdir: Path) -> list:
    """All check rows of one catalogue scenario (oracle, judge, combined)."""
    from demo.acceptance import judge as judge_mod, oracle, replay
    if args.via == "transcript":
        if any("clip" in st for st in s.timeline):
            mech = [Check(f"scenario.{s.id}.{g}", "SKIPPED", CLIP_SKIP_EVIDENCE)
                    for g in s.expect]
            obs = None
        else:
            try:
                obs = replay.run(s, repo=Path(args.consumer_repo), workdir=workdir,
                                 speed=args.speed, timeout_s=float(s.timeouts["scenario_s"]))
            except Exception as exc:  # noqa: BLE001 - one scenario must not abort the run
                return [Check(_row_id(s), "FAIL",
                              f"replay failed: {type(exc).__name__}: {exc}")]
    else:
        try:
            obs = run_call(s, args, workdir, baseline)
        except Exception as exc:  # noqa: BLE001 - one scenario must not abort the run
            return [Check(_row_id(s), "FAIL",
                          f"call run failed: {type(exc).__name__}: {exc}")]
        if isinstance(obs, Check):
            return [obs]
    if obs is not None:
        mech = oracle.grade(s, obs, baseline, overrides)
    jrow = None
    ran = any(c.status == "PASS" for c in mech) and not any(
        c.status == "FAIL" for c in mech)
    if s.judge and judge.cfg is not None and ran:
        if args.via == "transcript":
            jrow = Check(f"scenario.{s.id}.judge", "SKIPPED", "judge needs a live answer")
        else:
            jrow = judge.grade(s.id, s.judge["question"], _spoken(s),
                               obs.answers[-1] if obs.answers else "")
    combined = judge_mod.combine(mech, jrow, s.id)
    if s.regression_only:
        combined = Check(f"scenario.{s.id}.regression", combined.status, combined.evidence)
    rows = [*mech, *([jrow] if jrow else []), combined]
    if obs is not None and obs.audio_evidence:
        rows.append(Check(f"scenario.{s.id}.audio", "PASS", obs.audio_evidence))
    return rows


def main_catalogue(args) -> int:
    from demo.acceptance import catalogue, judge as judge_mod
    try:
        scenarios = catalogue.load_all(Path(args.catalogue))
    except catalogue.CatalogueError as exc:
        print(f"catalogue error: {exc}", file=sys.stderr)
        return 2
    if args.only:
        unknown = [i for i in args.only if i not in scenarios]
        if unknown:
            print(f"unknown scenario id(s) {unknown}; known: {sorted(scenarios)}",
                  file=sys.stderr)
            return 2
        scenarios = {i: scenarios[i] for i in args.only}
    baseline = catalogue.load_baseline(Path(args.baseline))
    overrides = dict(args.threshold or [])
    missing = []
    for key in sorted(catalogue.required_thresholds(scenarios.values())):
        try:
            catalogue.resolve(key, baseline, overrides)
        except catalogue.MissingThreshold as exc:
            missing.append(exc.key)
    if missing:
        print(f"missing threshold(s): {', '.join(missing)} -- pass --threshold "
              f"or run the part-4 baseline", file=sys.stderr)
        return 2
    cfg = None
    if args.judge_model:
        cfg = judge_mod.JudgeConfig(
            model=args.judge_model, endpoint=args.judge_endpoint,
            key_env=args.judge_key_env, api=args.judge_api,
            max_calls=args.judge_max_calls, max_tokens=args.judge_max_tokens)
    lane_model = args.lane_model or lane_model_from_preflight(args.preflight_report)
    judge = judge_mod.Judge(cfg, lane_model)
    rep = Report(tool="scenarios")
    bind_preflight(rep, args.preflight_report)
    out = Path(args.out)
    for s in scenarios.values():
        rep.extend(grade_scenario(s, args, baseline, overrides, judge, out / "work" / s.id))
    rep.judge = judge.summary()
    for c in rep.checks:
        print(f"{c.status} {c.id}: {c.evidence}")
    rep.write(out)
    return 0 if rep.passed else 1


# ------------------------------------------------------------------------ CLI

def _scenario_list(raw: str) -> list:
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in ALL_SCENARIOS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown scenario name(s) {unknown}; known: {list(ALL_SCENARIOS)}")
    return names


_LEGACY_REQUIRED = ("audio_dir", "url_file", "cdp_port", "snapshot_url",
                    "token_file", "consumer_log", "session_dir")


def _id_list(raw: str) -> list:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _require_observation_sources(ap, args) -> None:
    """Call mode: flags the selected scenarios' observations depend on."""
    from demo.acceptance import catalogue
    try:
        scenarios = catalogue.load_all(Path(args.catalogue))
    except Exception:  # noqa: BLE001 - main_catalogue reports a broken catalogue
        return
    chosen = [scenarios[i] for i in (args.only or scenarios) if i in scenarios]
    need = []
    if not args.consumer_log and any("lane_calls" in s.expect for s in chosen):
        need.append("--consumer-log (a selected scenario expects lane_calls)")
    if not args.session_dir and any("transcript" in s.expect for s in chosen):
        need.append("--session-dir (a selected scenario expects transcript)")
    if not args.audio_dir and any(
            "clip" in st or str(sp.get("voice", "")).startswith("audio-dir:")
            for s in chosen for st in s.timeline for sp in s.speakers):
        need.append("--audio-dir (a selected scenario uses an audio clip)")
    if need:
        ap.error("--via call needs " + ", ".join(need))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # required only in the legacy a-d mode (checked after parsing)
    ap.add_argument("--audio-dir")
    ap.add_argument("--url-file")
    ap.add_argument("--cdp-port", type=int)
    ap.add_argument("--snapshot-url")
    ap.add_argument("--token-file")
    ap.add_argument("--consumer-log")
    ap.add_argument("--session-dir")
    ap.add_argument("--scenarios", type=_scenario_list, default=list(ALL_SCENARIOS))
    ap.add_argument("--wake-phrase", default=DEFAULT_WAKE_PHRASE)
    ap.add_argument("--mid-sentence-max", type=float, default=DEFAULT_MID_SENTENCE_MAX)
    ap.add_argument("--agenda-file")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--preflight-report",
                    help="report.json of the preflight run to bind this run to")
    ap.add_argument("--out",
                    help="also write report.json/report.md of this run here")
    cat = ap.add_argument_group("catalogue mode (--catalogue switches it on)")
    cat.add_argument("--catalogue", help="directory of scenario YAML files")
    cat.add_argument("--via", choices=("call", "transcript"))
    cat.add_argument("--only", type=_id_list)
    cat.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    cat.add_argument("--threshold", action="append", type=_parse_threshold,
                     metavar="KEY=VALUE")
    cat.add_argument("--lane-model")
    cat.add_argument("--speed", type=float, default=1.0,
                     help="transcript driver only")
    cat.add_argument("--consumer-repo", default=str(_REPO_ROOT))
    cat.add_argument("--base-url", help="call driver: deployment base URL")
    cat.add_argument("--chrome", default="chromium")
    cat.add_argument("--piper-cmd", default="piper")
    cat.add_argument("--ffmpeg", default="ffmpeg")
    cat.add_argument("--piper-voice-alex")
    cat.add_argument("--piper-voice-sam")
    jg = ap.add_argument_group("judge (all six or none)")
    jg.add_argument("--judge-model")
    jg.add_argument("--judge-endpoint")
    jg.add_argument("--judge-key-env")
    jg.add_argument("--judge-api", choices=("anthropic-messages", "openai-chat"))
    jg.add_argument("--judge-max-calls", type=int)
    jg.add_argument("--judge-max-tokens", type=int)
    args = ap.parse_args(argv)
    given = [f for f in JUDGE_FLAGS if getattr(args, "judge_" + f) is not None]
    if given and len(given) != len(JUDGE_FLAGS):
        absent = ["--judge-" + f.replace("_", "-") for f in JUDGE_FLAGS if f not in given]
        ap.error("judge flags are all-or-none; missing: " + ", ".join(absent))
    if args.catalogue:
        if not args.via:
            ap.error("--catalogue needs --via call|transcript")
        if not args.out:
            ap.error("--catalogue needs --out")
        if args.via == "call":
            need = ("base_url", "token_file", "chrome", "piper_voice_alex", "piper_voice_sam")
            gone = [f"--{n.replace('_', '-')}" for n in need if not getattr(args, n)]
            if gone:
                ap.error("--via call needs " + ", ".join(gone))
            _require_observation_sources(ap, args)
    else:
        gone = [f"--{n.replace('_', '-')}" for n in _LEGACY_REQUIRED if getattr(args, n) is None]
        if gone:
            ap.error("the following arguments are required: " + ", ".join(gone))
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.catalogue:
        return main_catalogue(args)
    rep = Report(tool="scenarios")
    bind_preflight(rep, args.preflight_report)
    checks = [run_scenario(name, args) for name in args.scenarios]
    rep.extend(checks)
    for c in rep.checks:
        print(f"{c.status} {c.id}: {c.evidence}")
    if args.out:
        rep.write(Path(args.out))
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
