#!/usr/bin/env python3
"""Standard end-to-end acceptance run for a deployment (ADR 0022 level 4,
issue #153): one command that exercises a deployment the way a real
participant reaches it -- from a machine outside the server, over the
public HTTPS host with the token, through a real browser that joins the
real video call and sends real audio.

usage: python -m demo.acceptance.e2e --base-url https://<host>
           --token-file <file> --out <dir> [--minutes 4] [--keep-artifacts]
           [--tts piper|espeak] [--piper-voice-alex PATH]
           [--piper-voice-sam PATH] [--wiki-dir DIR]

The token is read from --token-file and never printed, logged or written
into the report (see `redact`/`Report.add`).

Import note: `demo/acceptance/` has no `__init__.py` on purpose (it holds
loose operator scripts, see make_tts.py/participant.py/score.py); `demo`
and `demo.acceptance` still import fine as implicit namespace packages
(no name shadows them on sys.path), so this module imports its siblings
normally (`from demo.acceptance import make_tts, score`) rather than the
`importlib.util.spec_from_file_location` pattern `tests/test_demo_acceptance.py`
uses to load those scripts *as standalone files* -- that pattern exists
for tests exercising a script in isolation, not for a module that is
itself invoked as `python -m demo.acceptance.e2e` and needs normal,
package-relative imports of its siblings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from demo.acceptance import make_tts, score

# --------------------------------------------------------------------------
# Report / Check / redaction (point 9: PASS/FAIL/SKIPPED with evidence; the
# token never appears in a report or on stdout).
# --------------------------------------------------------------------------

STATUSES = ("PASS", "FAIL", "SKIPPED")


def redact(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(token, "<redacted>")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class Report:
    base_url: str
    token: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> Check:
        assert status in STATUSES, status
        check = Check(name, status, redact(detail, self.token))
        self.checks.append(check)
        return check

    @property
    def passed(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "pass": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }

    def to_markdown(self) -> str:
        lines = ["# Acceptance report", "", "Base URL: %s" % self.base_url, "",
                 "| Status | Check | Detail |", "|---|---|---|"]
        for c in self.checks:
            lines.append("| %s | %s | %s |" % (c.status, c.name, c.detail))
        lines.append("")
        lines.append("Overall: %s" % ("PASS" if self.passed else "FAIL"))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Preflight (point 1): every public route 200s with the token, 404s without,
# TLS is valid.
# --------------------------------------------------------------------------

DEFAULT_ROUTES = {
    "call-only page": "/meeting/",
    "split page": "/meeting-split/",
    "stage": "/stage/",
    "monitor": "/monitor/",
    "contract snapshot": "/api/snapshot",
    "contract events": "/api/events",
    "fixtures": "/api-fixtures/",
}


def _default_fetch(url: str, timeout: float = 10.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def preflight(base_url: str, token: str, routes: dict[str, str] = DEFAULT_ROUTES,
              *, fetch: Callable[[str], int] = _default_fetch) -> list[Check]:
    checks = []
    for name, path in routes.items():
        url = base_url.rstrip("/") + path
        try:
            with_token = fetch(url + ("&" if "?" in url else "?") + "t=" + token)
            without_token = fetch(url)
        except Exception as exc:  # network/TLS error on an otherwise plain GET
            checks.append(Check("preflight " + name, "FAIL", "request failed: %s" % exc))
            continue
        ok = with_token == 200 and without_token == 404
        detail = "with token: %d, without token: %d" % (with_token, without_token)
        checks.append(Check("preflight " + name, "PASS" if ok else "FAIL", detail))
    return checks


def check_tls(base_url: str, *, fetch: Callable[[str], int] = _default_fetch) -> Check:
    host = urlsplit(base_url).netloc
    try:
        fetch(base_url.rstrip("/") + "/")
    except ssl.SSLCertVerificationError as exc:
        return Check("TLS certificate valid", "FAIL", "certificate error for %s: %s" % (host, exc))
    except Exception as exc:
        return Check("TLS certificate valid", "FAIL", "request to %s failed: %s" % (host, exc))
    return Check("TLS certificate valid", "PASS", "TLS handshake succeeded for %s" % host)


# --------------------------------------------------------------------------
# Speech (point 2): the probe timeline. The scoreable keyword sentences
# come from make_tts.sentences() (reused, not duplicated); the wake
# phrase, the agenda line and the decision line are new, fixed probes
# this tool adds on top.
# --------------------------------------------------------------------------

WAKE_LINE = "Hey Robo, fass bitte kurz zusammen."
AGENDA_LINE = "Der nächste Punkt auf der Agenda ist das Budget für das dritte Quartal."
DECISION_LINE = "Wir entscheiden uns für die Variante B und setzen sie diese Woche um."


def build_timeline(per_speaker: int = 12, lead_in: float = 15.0) -> dict:
    """events: list of {speaker, start_s, text, keywords}. Keyword
    sentences alternate Alex/Sam on make_tts.SLOT_S slots after `lead_in`;
    the four probe lines (wake x2, agenda, decision) follow in their own
    slots so each stays isolated, with no two events in the same slot."""
    sents = make_tts.sentences(2 * per_speaker)
    events = []
    for i, (text, kw) in enumerate(sents):
        who = "Alex" if i % 2 == 0 else "Sam"
        events.append({"speaker": who, "start_s": lead_in + i * make_tts.SLOT_S,
                        "text": text, "keywords": [kw]})
    probe_start = lead_in + len(sents) * make_tts.SLOT_S
    probes = [
        ("Sam", WAKE_LINE),
        ("Alex", WAKE_LINE),
        ("Sam", AGENDA_LINE),
        ("Alex", DECISION_LINE),
    ]
    for i, (who, text) in enumerate(probes):
        events.append({"speaker": who, "start_s": probe_start + i * make_tts.SLOT_S,
                        "text": text, "keywords": []})
    total_s = probe_start + len(probes) * make_tts.SLOT_S
    return {"slot_s": make_tts.SLOT_S, "total_s": total_s, "events": events}


def _normalize_wav(raw: Path, ffmpeg: str, norm: Path) -> bytes:
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw),
                    "-ar", str(make_tts.RATE), "-ac", "1", "-sample_fmt", "s16",
                    str(norm)], check=True)
    with wave.open(str(norm), "rb") as w:
        assert w.getframerate() == make_tts.RATE and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def render_piper(piper_cmd: str, model: Path, ffmpeg: str, text: str, tmp: Path) -> bytes:
    raw = tmp / "raw.wav"
    subprocess.run([piper_cmd, "--model", str(model), "--output_file", str(raw)],
                   input=text, text=True, check=True)
    return _normalize_wav(raw, ffmpeg, tmp / "norm.wav")


def render_espeak(espeak: str, ffmpeg: str, text: str, tmp: Path) -> bytes:
    raw = tmp / "raw.wav"
    subprocess.run([espeak, "-v", "de", "-s", "160", "-w", str(raw), text], check=True)
    return _normalize_wav(raw, ffmpeg, tmp / "norm.wav")


def download_voice(url: str, dest: Path, sha256: str,
                    *, fetch: Callable[[str], bytes] = None) -> None:
    """Fetch a Piper voice model and verify its checksum before keeping
    it. No bytes are written to `dest` on a checksum mismatch."""
    if fetch is None:
        def fetch(u: str) -> bytes:
            with urllib.request.urlopen(u, timeout=60) as resp:
                return resp.read()
    payload = fetch(url)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        raise ValueError("checksum mismatch for %s: expected %s, got %s" % (url, sha256, digest))
    dest.write_bytes(payload)


def render_script(engine: str, timeline: dict, out_dir: Path, *,
                   piper_cmd: str = "piper", espeak: str = "espeak-ng",
                   ffmpeg: str = "ffmpeg", piper_voice_alex: Path | None = None,
                   piper_voice_sam: Path | None = None) -> None:
    """Writes alex.wav, sam.wav and script.json into `out_dir`."""
    total_s = timeline["total_s"]
    buffers = {"Alex": bytearray(int(total_s * make_tts.RATE) * 2),
               "Sam": bytearray(int(total_s * make_tts.RATE) * 2)}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for ev in timeline["events"]:
            if engine == "piper":
                voice = piper_voice_alex if ev["speaker"] == "Alex" else piper_voice_sam
                pcm = render_piper(piper_cmd, voice, ffmpeg, ev["text"], tmp)
            else:
                pcm = render_espeak(espeak, ffmpeg, ev["text"], tmp)
            off = int(ev["start_s"] * make_tts.RATE) * 2
            buffers[ev["speaker"]][off:off + len(pcm)] = pcm
    for who, buf in buffers.items():
        make_tts.write_wav(out_dir / (who.lower() + ".wav"), bytes(buf))
    (out_dir / "script.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
# Media path proof (point 4): classify the selected candidate pair and the
# audio byte counters from a raw `RTCPeerConnection.getStats()` dump
# (participant.py's `--stats-out` writes this JSON array).
# --------------------------------------------------------------------------


def classify_candidate_pair(stats: list[dict]) -> dict:
    by_id = {s["id"]: s for s in stats if "id" in s}
    pairs = [s for s in stats if s.get("type") == "candidate-pair"
             and (s.get("selected") or s.get("state") == "succeeded")]
    candidate_type = protocol = None
    if pairs:
        local = by_id.get(pairs[0].get("localCandidateId"))
        if local:
            candidate_type = local.get("candidateType")
            protocol = local.get("protocol")
    audio_sent = sum(s.get("bytesSent", 0) for s in stats
                      if s.get("type") == "outbound-rtp" and s.get("kind") == "audio")
    audio_received = sum(s.get("bytesReceived", 0) for s in stats
                          if s.get("type") == "inbound-rtp" and s.get("kind") == "audio")
    return {"candidate_type": candidate_type, "protocol": protocol,
            "audio_bytes_sent": audio_sent, "audio_bytes_received": audio_received}


def media_check(name: str, stats: dict) -> Check:
    total = stats["audio_bytes_sent"] + stats["audio_bytes_received"]
    detail = ("transport=%s/%s audio_bytes_sent=%d audio_bytes_received=%d"
               % (stats.get("candidate_type"), stats.get("protocol"),
                  stats["audio_bytes_sent"], stats["audio_bytes_received"]))
    return Check("media path (%s)" % name, "PASS" if total > 0 else "FAIL", detail)


# --------------------------------------------------------------------------
# Results (point 7).
# --------------------------------------------------------------------------


def fetch_snapshot(base_url: str, token: str) -> dict:
    url = base_url.rstrip("/") + "/api/snapshot?t=" + token
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_call_answer(fetch: Callable[[], dict], timeout_s: float = 60.0,
                          poll_interval: float = 2.0,
                          clock: Callable[[], float] = time.time,
                          sleep: Callable[[float], None] = time.sleep) -> tuple[bool, float]:
    """Polls the live contract snapshot's `call_answer` field (`StageStateSnapshot`
    in surfaces/contract/models.py: "The last call-and-answer line, if any.")
    until it is non-empty or `timeout_s` elapses."""
    start = clock()
    while clock() - start < timeout_s:
        snapshot = fetch() or {}
        if snapshot.get("call_answer"):
            return True, clock() - start
        sleep(poll_interval)
    return False, timeout_s


def wake_check(tts_engine: str, lane_answer_seen: bool, elapsed_s: float,
               timeout_s: float = 60.0) -> Check:
    if tts_engine == "espeak":
        return Check("wake phrase answered on stage", "SKIPPED",
                      "espeak fallback: robotic speech is not reliably recognised, "
                      "the call lane was never actually exercised")
    ok = lane_answer_seen and elapsed_s <= timeout_s
    detail = "answer seen=%s after %.1fs (timeout %.0fs)" % (lane_answer_seen, elapsed_s, timeout_s)
    return Check("wake phrase answered on stage", "PASS" if ok else "FAIL", detail)


def score_check(ratio: float, matched: int, min_ratio: float = 0.9) -> Check:
    detail = "matched=%d ratio=%.3f (min %.2f)" % (matched, ratio, min_ratio)
    ok = matched > 0 and ratio >= min_ratio
    return Check("speaker attribution", "PASS" if ok else "FAIL", detail)


def knowledge_check(wiki_dir_configured: bool, hits: int) -> Check:
    if not wiki_dir_configured:
        return Check("knowledge hits", "SKIPPED", "no wiki dir configured")
    return Check("knowledge hits", "PASS" if hits > 0 else "FAIL", "hits=%d" % hits)


def events_check(kind: str, seen: bool) -> Check:
    return Check("%s event seen" % kind, "PASS" if seen else "FAIL",
                 "observed" if seen else "not observed")


def count_knowledge_hits(snapshot: dict) -> int:
    """Heuristic: a notice whose `kind` (chosen by the raising block,
    surfaces/contract/models.py Notice.kind) names knowledge or wiki."""
    notices = snapshot.get("notices") or []
    return sum(1 for n in notices
               if "knowledge" in n.get("kind", "").lower()
               or "wiki" in n.get("kind", "").lower())


# --------------------------------------------------------------------------
# Cleanup (point 8): the tool marks its session and prints the cleanup
# command; it never deletes server data itself.
# --------------------------------------------------------------------------


def cleanup_command(session_title: str) -> str:
    return ("Session marked '%s'. To remove it, run on the server: "
            "project-cli session delete --title-prefix '%s'"
            % (session_title, session_title))


# --------------------------------------------------------------------------
# Surfaces (point 5): the stage's own "Rohtranskript" section renders each
# markdown bullet score.py expects (`- **HH:MM** *Speaker:* text`) as an
# `<li>`; its innerText reads "HH:MM Speaker: text". Converting it back
# lets score.score() run unchanged against the live stage.
# --------------------------------------------------------------------------

_STAGE_ROH_LINE_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+([^:]+):\s*(.*)$")


def stage_transcript_line_to_bullet(text: str) -> str | None:
    m = _STAGE_ROH_LINE_RE.match(text.strip())
    if not m:
        return None
    time_s, speaker, rest = m.groups()
    return "- **%s** *%s:* %s" % (time_s, speaker.strip(), rest)


def build_public_url(base_url: str, path: str, token: str) -> str:
    return base_url.rstrip("/") + path + "?t=" + token


def check_surfaces(base_url: str, token: str, names: list[str], agenda_hint: str,
                    out_dir: Path) -> tuple[list[Check], list[str]]:
    """Opens stage and monitor at 1920x1080 via the public contract route,
    asserts named transcript lines, the current agenda item, and no
    console errors; returns the checks plus the raw stage transcript
    bullets (for score_check) collected along the way."""
    from playwright.sync_api import sync_playwright

    checks: list[Check] = []
    transcript_bullets: list[str] = []
    api_url = base_url.rstrip("/") + "/api/"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for surface_name, path in (("stage", "/stage/"), ("monitor", "/monitor/")):
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text)
                     if msg.type == "error" else None)
            url = build_public_url(base_url, path, token) + "&api=" + api_url
            page.goto(url, wait_until="load")
            page.wait_for_timeout(2000)

            # Without the ct_demo_name cookie (only ever set by
            # participant.py for the meeting page), stage/monitor ask "Wie
            # heißt du?" before showing anything; "Später" is the page's
            # own way for a non-speaking viewer to skip that.
            join_later = page.locator("#join-later")
            if join_later.count() and join_later.is_visible():
                join_later.click()
                page.wait_for_timeout(300)

            if surface_name == "stage":
                roh_summary = page.locator("details.roh summary")
                if roh_summary.count():
                    roh_summary.click()
                    page.wait_for_timeout(300)
                    for li in page.locator("details.roh li").all_inner_texts():
                        bullet = stage_transcript_line_to_bullet(li)
                        if bullet:
                            transcript_bullets.append(bullet)

            body_text = page.locator("body").inner_text()
            missing_names = [n for n in names if n not in body_text]
            checks.append(Check("%s shows participant names" % surface_name,
                                 "PASS" if not missing_names else "FAIL",
                                 "missing: %s" % ", ".join(missing_names) if missing_names
                                 else "all of %s present" % ", ".join(names)))
            checks.append(Check("%s shows the current agenda item" % surface_name,
                                 "PASS" if agenda_hint in body_text else "FAIL",
                                 "agenda hint %r %s" % (
                                     agenda_hint, "found" if agenda_hint in body_text
                                     else "not found")))
            page.screenshot(path=str(out_dir / ("%s.png" % surface_name)), full_page=True)
            checks.append(Check("%s has no console errors" % surface_name,
                                 "PASS" if not console_errors else "FAIL",
                                 "; ".join(console_errors) if console_errors else "none"))
            context.close()
        browser.close()
    return checks, transcript_bullets


def run_contract_check(monitor_url: str, token: str, out_dir: Path) -> Check:
    """Runs the live conformance check (point 6). `monitor_url` carries the
    token so the page is reachable; check.py's own report.json records
    the surface URL verbatim, so it is redacted here right after the
    subprocess returns, before anything else can read that file."""
    contract_dir = out_dir / "contract"
    result = subprocess.run(
        [sys.executable, "-m", "surfaces.contract.check", "--surface", monitor_url,
         "--out", str(contract_dir)],
        capture_output=True, text=True)
    sub_report = contract_dir / "report.json"
    if sub_report.exists():
        sub_report.write_text(redact(sub_report.read_text(encoding="utf-8"), token),
                               encoding="utf-8")
    detail = redact(result.stdout + result.stderr, token).strip().splitlines()
    tail = " / ".join(detail[-3:]) if detail else "no output"
    return Check("contract conformance (live monitor)",
                 "PASS" if result.returncode == 0 else "FAIL", tail)


# --------------------------------------------------------------------------
# Participants (point 3): two headless Chrome participants join the call
# page via the public URL -- no host-resolver mapping, no localhost.
# --------------------------------------------------------------------------

PARTICIPANT_PY = Path(__file__).resolve().parent / "participant.py"
_JOINED_RE = re.compile(r"^joined name=(\S+) after ([\d.]+)s")


def start_participant(name: str, wav: Path, url_file: Path, stats_out: Path,
                       screenshot: Path, cdp_port: int, chrome: str,
                       seconds: float) -> subprocess.Popen:
    cmd = [sys.executable, str(PARTICIPANT_PY), "--url-file", str(url_file),
           "--name", name, "--wav", str(wav), "--seconds", str(seconds),
           "--cdp-port", str(cdp_port), "--chrome", chrome,
           "--stats-out", str(stats_out), "--screenshot", str(screenshot)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def join_participants(base_url: str, token: str, out_dir: Path, chrome: str,
                       seconds: float) -> tuple[list[Check], dict[str, dict]]:
    """Writes each participant's per-user URL to a token-mode (0600) temp
    file (never printed), joins Alex and Sam at the same moment via the
    public call-only page, and returns join checks plus each
    participant's raw getStats() entries for the media check."""
    checks: list[Check] = []
    stats: dict[str, dict] = {}
    specs = [("Alex", out_dir / "alex.wav", 9301), ("Sam", out_dir / "sam.wav", 9302)]
    procs = {}
    url_files = {}
    for name, wav, port in specs:
        url_file = out_dir / (".%s.url" % name.lower())
        url_file.write_text(build_public_url(base_url, "/meeting/", token), encoding="utf-8")
        url_file.chmod(0o600)
        url_files[name] = url_file
        stats_out = out_dir / ("%s-stats.json" % name.lower())
        screenshot = out_dir / ("%s-join.png" % name.lower())
        procs[name] = (start_participant(name, wav, url_file, stats_out, screenshot,
                                          port, chrome, seconds), stats_out)
    for name, (proc, stats_out) in procs.items():
        out, _ = proc.communicate(timeout=seconds + 60)
        joined = _JOINED_RE.search(out)
        if proc.returncode != 0 or not joined:
            checks.append(Check("join (%s)" % name, "FAIL",
                                 redact(out, token).strip().splitlines()[-1]
                                 if out.strip() else "no output"))
            continue
        checks.append(Check("join (%s)" % name, "PASS",
                             "joined after %ss" % joined.group(2)))
        try:
            stats[name] = classify_candidate_pair(json.loads(stats_out.read_text()))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            checks.append(Check("media path (%s)" % name, "FAIL",
                                 "no usable stats: %s" % exc))
    for url_file in url_files.values():
        url_file.unlink(missing_ok=True)
    return checks, stats


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m demo.acceptance.e2e",
                                  description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--keep-artifacts", action="store_true")
    ap.add_argument("--tts", choices=["piper", "espeak"], default="piper")
    ap.add_argument("--per-speaker", type=int, default=12)
    ap.add_argument("--piper-cmd", default="piper")
    ap.add_argument("--piper-voice-alex")
    ap.add_argument("--piper-voice-sam")
    ap.add_argument("--piper-voice-url-alex")
    ap.add_argument("--piper-voice-sha256-alex")
    ap.add_argument("--piper-voice-url-sam")
    ap.add_argument("--piper-voice-sha256-sam")
    ap.add_argument("--espeak", default="espeak-ng")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--chrome", default="google-chrome")
    ap.add_argument("--min-score-ratio", type=float, default=0.9)
    ap.add_argument("--wiki-dir")
    return ap


def resolve_piper_voice(label: str, path_arg: str | None, url_arg: str | None,
                         sha_arg: str | None, voices_dir: Path) -> Path:
    if path_arg:
        return Path(path_arg)
    if url_arg and sha_arg:
        dest = voices_dir / ("%s.onnx" % label)
        if not dest.exists():
            voices_dir.mkdir(parents=True, exist_ok=True)
            download_voice(url_arg, dest, sha_arg)
        return dest
    raise SystemExit(
        "--piper-voice-%s or --piper-voice-url-%s/--piper-voice-sha256-%s is required "
        "for --tts piper" % (label, label, label))


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = Report(base_url=args.base_url, token=token)
    session_title = "e2e-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    # 1. Preflight.
    report.checks.extend(preflight(args.base_url, token))
    report.checks.append(check_tls(args.base_url))

    # 2. Speech.
    timeline = build_timeline(per_speaker=args.per_speaker)
    render_kwargs = {"espeak": args.espeak, "ffmpeg": args.ffmpeg}
    if args.tts == "piper":
        voices_dir = out_dir / "voices"
        render_kwargs["piper_cmd"] = args.piper_cmd
        render_kwargs["piper_voice_alex"] = resolve_piper_voice(
            "alex", args.piper_voice_alex, args.piper_voice_url_alex,
            args.piper_voice_sha256_alex, voices_dir)
        render_kwargs["piper_voice_sam"] = resolve_piper_voice(
            "sam", args.piper_voice_sam, args.piper_voice_url_sam,
            args.piper_voice_sha256_sam, voices_dir)
    render_script(args.tts, timeline, out_dir, **render_kwargs)
    report.add("speech generated", "PASS",
               "engine=%s events=%d total_s=%.1f" % (args.tts, len(timeline["events"]),
                                                       timeline["total_s"]))

    # 3 & 4. Participants join over the public path; media path proof.
    join_checks, media_stats = join_participants(
        args.base_url, token, out_dir, args.chrome, args.minutes * 60)
    report.checks.extend(join_checks)
    for name, stats in media_stats.items():
        report.checks.append(media_check(name, stats))

    # 5. Surfaces.
    surface_checks, transcript_bullets = check_surfaces(
        args.base_url, token, ["Alex", "Sam"], AGENDA_LINE, out_dir)
    report.checks.extend(surface_checks)

    # 6. Contract.
    monitor_url = build_public_url(args.base_url, "/monitor/", token)
    report.checks.append(run_contract_check(monitor_url, token, out_dir))

    # 7. Results.
    result = score.score(transcript_bullets, timeline)
    report.checks.append(score_check(result["ratio"], result["matched"], args.min_score_ratio))

    def poll_snapshot() -> dict:
        try:
            return fetch_snapshot(args.base_url, token)
        except Exception:
            return {}

    lane_answer_seen, elapsed_s = wait_for_call_answer(poll_snapshot)
    report.checks.append(wake_check(args.tts, lane_answer_seen, elapsed_s))

    final_snapshot = poll_snapshot()
    topic_keywords = {kw for ev in timeline["events"] for kw in ev["keywords"]}
    history_text = " ".join(h.get("markdown", "") for h in final_snapshot.get("history", []))
    report.checks.append(events_check(
        "topic", any(kw in history_text for kw in topic_keywords)))
    report.checks.append(events_check(
        "view-intent", bool(final_snapshot.get("history") or final_snapshot.get("main"))))
    report.checks.append(knowledge_check(
        bool(args.wiki_dir), count_knowledge_hits(final_snapshot)))

    # 8. Cleanup.
    print(cleanup_command(session_title))

    # 9. Output.
    (out_dir / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(report.to_markdown() + "\n", encoding="utf-8")
    print(report.to_markdown())
    if not args.keep_artifacts:
        for name in ("alex.wav", "sam.wav"):
            (out_dir / name).unlink(missing_ok=True)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
