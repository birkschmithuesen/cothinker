#!/usr/bin/env python3
"""Pre-flight checks, run BEFORE any acceptance scenario against a running
deployment. Prints one `PASS|FAIL|SKIPPED <check>: <found value / reason>`
line per check and exits 1 if any FAIL (SKIPPED is exit-neutral).

Checks:

- `lane_profile`: the consumer's `--profile-env` environment variable
  (`<proc-root>/<consumer-pid>/environ`, NUL-separated; the PID is either
  given directly or looked up from a systemd user unit) names a profile
  directory whose `config.yaml` lists `--call-toolset` under
  `agent.enabled_toolsets` (`gateway/worker.py`'s own reading of a
  profile's `config.yaml`, `shared/profiles/skel/main/config.yaml`'s
  schema -- there is no literal top-level `toolsets:` key in this repo)
  and whose basename does not match `--forbidden-profile-glob`.
- `eou_silence`: the last `[sm-rt]` line of `--stt-log` carries
  `eou_silence=<float>` equal to `--expect-eou`; `max_delay=` is printed
  for information only.
- `blocks_registered`: `gateway/block_host_wiring.py` logs
  "block host: no block registered for '<id>'" once per template block id
  nobody has wired up yet; this FAILs if that text occurs anywhere after
  the LAST session start of `--consumer-log`. A session start is the line
  `gateway/consumer.py`'s `main()` logs right after building the worker
  client (`log("agent session:", session_id)`, written by `log()` as
  "[HH:MM:SS] agent session: <id>"); override the regex with
  `--start-marker` if a profile logs something else.
- `template_blocks` (only with `--template`): lists every block id of the
  template (`shared.templates.load_template`, the format `templates/*/`
  TOML files use, ADR 0009). SKIPPED, not PASS, when the ids cannot be
  read deterministically (e.g. the template defines `[formats]` and needs
  one named to resolve its block list).
- `contract_fields`: `--expect-fields` must all be present in the stage
  snapshot (`--snapshot-url` + `--token-file`, sent as an
  `Authorization: Bearer` header -- same convention as
  `surfaces/operator/app.py` -- or `--snapshot-file` for tests); each of
  `--optional-fields` is PASS if present, SKIPPED "feature not merged
  yet" if absent (key-absent-means-hidden, `StageState._content()`).
  `listen`, when present, must carry a `state`; its `last_close`, when
  present, must carry a `reason` (issue #180).

usage: preflight.py --consumer-pid N [--proc-root /proc]
         [--profile-env HERMES_PROFILE_DIR_CALL] [--call-toolset file]
         [--forbidden-profile-glob '*coach*']
         [--stt-log PATH] [--expect-eou 1.8]
         [--consumer-log PATH] [--template PATH] [--start-marker REGEX]
         [--snapshot-url URL --token-file PATH | --snapshot-file PATH]
         [--expect-fields voice,share,queue]
         [--optional-fields listen,transcript_recent]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import yaml

# Run as a plain script, only this directory is on sys.path; the shared
# report module lives under the repo root's `demo` namespace package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from demo.acceptance.report import Check  # noqa: E402

DEFAULT_PROFILE_ENV = "HERMES_PROFILE_DIR_CALL"
DEFAULT_CALL_TOOLSET = "file"
DEFAULT_FORBIDDEN_PROFILE_GLOB = "*coach*"
DEFAULT_EXPECT_EOU = 1.8
DEFAULT_START_MARKER = "agent session:"
DEFAULT_EXPECT_FIELDS = "voice,share,queue"
DEFAULT_OPTIONAL_FIELDS = "listen,transcript_recent"

_EOU_RE = re.compile(r"eou_silence=([0-9.]+)")
_MAX_DELAY_RE = re.compile(r"max_delay=([0-9.]+)")


# --------------------------------------------------------------- lane_profile

def read_environ(path: Path) -> dict:
    """`<proc-root>/<pid>/environ`: NUL-separated `KEY=VALUE` entries."""
    env = {}
    for entry in path.read_bytes().split(b"\0"):
        if not entry:
            continue
        text = entry.decode("utf-8", errors="replace")
        key, sep, value = text.partition("=")
        if sep:
            env[key] = value
    return env


def resolve_consumer_pid(*, consumer_pid: Optional[int],
                         consumer_unit: Optional[str],
                         runner: Callable = subprocess.run
                         ) -> tuple:
    """-> (pid, error); exactly one of the two is None on success."""
    if consumer_pid is not None:
        return consumer_pid, None
    result = runner(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value",
         consumer_unit],
        capture_output=True, text=True)
    if result.returncode != 0:
        return None, (f"systemctl --user show failed for unit "
                       f"{consumer_unit!r}: {result.stderr.strip() or result.returncode}")
    value = result.stdout.strip()
    if not value.isdigit():
        return None, (f"systemctl --user show returned no PID for unit "
                       f"{consumer_unit!r}: {value!r}")
    pid = int(value)
    if pid == 0:
        return None, f"unit {consumer_unit!r} is not running (MainPID=0)"
    return pid, None


def check_lane_profile(*, proc_root: Path, pid: int, profile_env: str,
                       call_toolset: str, forbidden_profile_glob: str) -> Check:
    name = "lane_profile"
    environ_path = Path(proc_root) / str(pid) / "environ"
    if not environ_path.is_file():
        return Check(name, "FAIL", f"environ not found at {environ_path}")
    env = read_environ(environ_path)
    profile_dir_raw = env.get(profile_env, "").strip()
    if not profile_dir_raw:
        return Check(name, "FAIL",
                      f"{profile_env} not set (environ={environ_path})")
    profile_dir = Path(profile_dir_raw)
    config_path = profile_dir / "config.yaml"
    if not config_path.is_file():
        return Check(name, "FAIL",
                      f"dir={profile_dir} toolsets=[] (no config.yaml)")
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return Check(name, "FAIL", f"{config_path} is not valid YAML: {e}")
    toolsets = list((cfg.get("agent") or {}).get("enabled_toolsets") or [])
    problems = []
    if call_toolset not in toolsets:
        problems.append(f"toolset {call_toolset!r} not in {toolsets}")
    if fnmatch.fnmatch(profile_dir.name, forbidden_profile_glob):
        problems.append(
            f"basename matches forbidden glob {forbidden_profile_glob!r}")
    if problems:
        return Check(name, "FAIL",
                      f"dir={profile_dir} toolsets={toolsets} "
                      f"({'; '.join(problems)})")
    return Check(name, "PASS", f"dir={profile_dir} toolsets={toolsets}")


# ----------------------------------------------------------------- eou_silence

def check_eou_silence(*, stt_log: Path, expect_eou: float) -> Check:
    name = "eou_silence"
    stt_log = Path(stt_log)
    if not stt_log.is_file():
        return Check(name, "FAIL", f"log not found at {stt_log}")
    last = None
    for line in stt_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "[sm-rt]" in line and "eou_silence=" in line:
            last = line
    if last is None:
        return Check(name, "FAIL",
                      f"no '[sm-rt]' line with eou_silence= in {stt_log}")
    m = _EOU_RE.search(last)
    if not m:
        return Check(name, "FAIL",
                      f"eou_silence= not parseable in line: {last.strip()}")
    found = float(m.group(1))
    extra = ""
    md = _MAX_DELAY_RE.search(last)
    if md:
        extra = f" max_delay={md.group(1)}"
    if found != expect_eou:
        return Check(name, "FAIL",
                      f"eou_silence={found} (expected {expect_eou}){extra}")
    return Check(name, "PASS", f"eou_silence={found}{extra}")


# ------------------------------------------------------------ blocks_registered

def check_blocks_registered(*, consumer_log: Path, start_marker: str) -> Check:
    name = "blocks_registered"
    consumer_log = Path(consumer_log)
    if not consumer_log.is_file():
        return Check(name, "FAIL", f"log not found at {consumer_log}")
    lines = consumer_log.read_text(encoding="utf-8", errors="replace").splitlines()
    marker_re = re.compile(start_marker)
    starts = [i for i, l in enumerate(lines) if marker_re.search(l)]
    if not starts:
        return Check(name, "FAIL",
                      f"no start marker ({start_marker!r}) in {consumer_log}")
    last_start = starts[-1]
    missing = [l for l in lines[last_start + 1:] if "no block registered" in l]
    if missing:
        return Check(name, "FAIL",
                      f"{len(missing)} missing-block line(s) after the last "
                      f"start (line {last_start + 1}): {missing[-1].strip()}")
    return Check(name, "PASS",
                  f"no missing-block line after the last start "
                  f"(line {last_start + 1})")


def check_template_blocks(template_path) -> Optional[Check]:
    name = "template_blocks"
    if template_path is None:
        return None
    try:
        from shared.templates import load_template, TemplateError
    except ImportError as e:
        return Check(name, "SKIPPED", f"shared.templates not importable: {e}")
    try:
        template = load_template(Path(template_path))
    except TemplateError as e:
        return Check(name, "SKIPPED",
                      f"block ids not readable deterministically: {e}")
    ids = [b.id for b in template.blocks]
    return Check(name, "PASS", f"{len(ids)} block ids: {', '.join(ids)}")


# -------------------------------------------------------------- contract_fields

def load_snapshot(*, snapshot_url: Optional[str], token_file: Optional[str],
                  snapshot_file: Optional[str] = None,
                  opener: Callable = urllib.request.urlopen) -> tuple:
    """-> (snapshot_dict, error). The token (`token_file`) is read but
    never included in the error text or anywhere else that gets printed."""
    if snapshot_file is not None:
        try:
            return json.loads(Path(snapshot_file).read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as e:
            return None, f"{snapshot_file}: {e}"
    if not snapshot_url:
        return None, "neither --snapshot-url nor --snapshot-file given"
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as e:
        return None, f"token file {token_file}: {type(e).__name__}"
    req = urllib.request.Request(
        snapshot_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:  # noqa: BLE001 -- reported as a FAIL text, never raised
        return None, f"snapshot request to {snapshot_url} failed: {type(e).__name__}"


def _check_listen_shape(listen) -> Optional[str]:
    if not isinstance(listen, dict) or "state" not in listen:
        return f"listen is present but has no 'state': {listen!r}"
    last_close = listen.get("last_close")
    if last_close is not None:
        if not isinstance(last_close, dict) or "reason" not in last_close:
            return f"listen.last_close is present but has no 'reason': {last_close!r}"
    return None


def check_contract_fields(*, snapshot: dict, expect_fields: list,
                          optional_fields: list) -> list:
    name = "contract_fields"
    checks = []
    missing = [f for f in expect_fields if f not in snapshot]
    if missing:
        checks.append(Check(name, "FAIL",
                             f"missing required field(s): {', '.join(missing)}"))
    else:
        checks.append(Check(name, "PASS",
                             f"required fields present: {', '.join(expect_fields)}"))
    for field in optional_fields:
        oname = f"{name}:{field}"
        value = snapshot.get(field)
        if field not in snapshot or value is None:
            checks.append(Check(oname, "SKIPPED",
                                 "field not in snapshot (feature not merged yet)"))
            continue
        if field == "listen":
            problem = _check_listen_shape(value)
            if problem:
                checks.append(Check(oname, "FAIL", problem))
                continue
        checks.append(Check(oname, "PASS", f"present: {json.dumps(value)[:200]}"))
    return checks


# ------------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--proc-root", default="/proc")
    ap.add_argument("--consumer-pid", type=int)
    ap.add_argument("--consumer-unit")
    ap.add_argument("--profile-env", default=DEFAULT_PROFILE_ENV)
    ap.add_argument("--call-toolset", default=DEFAULT_CALL_TOOLSET)
    ap.add_argument("--forbidden-profile-glob",
                    default=DEFAULT_FORBIDDEN_PROFILE_GLOB)
    ap.add_argument("--stt-log")
    ap.add_argument("--expect-eou", type=float, default=DEFAULT_EXPECT_EOU)
    ap.add_argument("--consumer-log")
    ap.add_argument("--template")
    ap.add_argument("--start-marker", default=DEFAULT_START_MARKER)
    ap.add_argument("--snapshot-url")
    ap.add_argument("--token-file")
    ap.add_argument("--snapshot-file")
    ap.add_argument("--expect-fields", default=DEFAULT_EXPECT_FIELDS)
    ap.add_argument("--optional-fields", default=DEFAULT_OPTIONAL_FIELDS)
    a = ap.parse_args(argv)

    if a.consumer_pid is None and not a.consumer_unit:
        ap.error("one of --consumer-pid or --consumer-unit is required")

    checks = []

    pid, pid_err = resolve_consumer_pid(consumer_pid=a.consumer_pid,
                                        consumer_unit=a.consumer_unit)
    if pid_err:
        checks.append(Check("lane_profile", "FAIL", pid_err))
    else:
        checks.append(check_lane_profile(
            proc_root=Path(a.proc_root), pid=pid, profile_env=a.profile_env,
            call_toolset=a.call_toolset,
            forbidden_profile_glob=a.forbidden_profile_glob))

    if a.stt_log:
        checks.append(check_eou_silence(stt_log=Path(a.stt_log),
                                        expect_eou=a.expect_eou))

    if a.consumer_log:
        checks.append(check_blocks_registered(
            consumer_log=Path(a.consumer_log), start_marker=a.start_marker))
        template_check = check_template_blocks(
            Path(a.template) if a.template else None)
        if template_check is not None:
            checks.append(template_check)

    if a.snapshot_url or a.snapshot_file:
        snapshot, err = load_snapshot(snapshot_url=a.snapshot_url,
                                      token_file=a.token_file,
                                      snapshot_file=a.snapshot_file)
        if err:
            checks.append(Check("contract_fields", "FAIL", err))
        else:
            expect_fields = [f.strip() for f in a.expect_fields.split(",") if f.strip()]
            optional_fields = [f.strip() for f in a.optional_fields.split(",") if f.strip()]
            checks.extend(check_contract_fields(
                snapshot=snapshot, expect_fields=expect_fields,
                optional_fields=optional_fields))

    for c in checks:
        print(f"{c.status} {c.id}: {c.evidence}")
    return 1 if any(c.status == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
