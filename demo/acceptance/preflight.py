#!/usr/bin/env python3
"""Pre-flight checks, run BEFORE any acceptance scenario against a running
deployment. Prints one `PASS|FAIL|SKIPPED <check>: <found value / reason>`
line per check and exits 1 if any FAIL (SKIPPED is exit-neutral).

Checks:

- `lane_profile`: the consumer's `--profile-env` environment variable
  (`<proc-root>/<consumer-pid>/environ`, NUL-separated; the PID is either
  given directly or looked up from a systemd user unit) names a profile
  directory whose `config.yaml` does not list `--call-toolset` under
  `agent.disabled_toolsets` (the key `gateway/worker.py` applies) and
  whose path does not match `--forbidden-profile-glob`.
- `preflight.lane-profile.<main|coach|call>`: every lane's profile (the
  `--hermes-home` of the consumer's command line, see `lane_profiles`) is
  checked against the rules in `--invariants` (`invariants.yaml`).
- `preflight.stt.eou` / `preflight.stt.max_delay`: the last `[sm-rt]` start
  line of `--stt-log` carries values equal to `--expect-eou` /
  `--expect-max-delay` (both required, no default).
- `preflight.blocks.registry`: with `--consumer-log`, every template block
  loads via `shared.block_registry` and the log since its last start has no
  "no block registered" line.
- `preflight.unit.pending.<unit>` (per `--unit`): FAIL when systemd reports
  NeedDaemonReload or the unit file or a drop-in is newer than the process
  start. Read-only; never reloads or restarts.
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
         [--invariants PATH] [--home DIR]
         [--stt-log PATH] --expect-eou S --expect-max-delay S
         [--unit NAME ...]
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
import shlex
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
from demo.acceptance.fingerprint import run_sh  # noqa: E402

DEFAULT_INVARIANTS = Path(__file__).resolve().parent / "invariants.yaml"
DEFAULT_PROFILE_ENV = "HERMES_PROFILE_DIR_CALL"
DEFAULT_CALL_TOOLSET = "file"
DEFAULT_FORBIDDEN_PROFILE_GLOB = "*coach*"
DEFAULT_START_MARKER = "agent session:"
DEFAULT_EXPECT_FIELDS = "voice,share,queue"
DEFAULT_OPTIONAL_FIELDS = "listen,transcript_recent"

_SMRT = re.compile(r"\[sm-rt\].*?max_delay=([\d.]+)\s+eou_silence=([\d.]+)")
_NOREG = re.compile(r"block host: no block registered for '([^']+)'")


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


def _hermes_home(command: str) -> Optional[Path]:
    parts = shlex.split(command or "")
    if "--hermes-home" in parts:
        i = parts.index("--hermes-home")
        if i + 1 < len(parts):
            return Path(parts[i + 1])
    return None


def _flag(argv, name: str) -> str:
    return argv[argv.index(name) + 1] if name in argv[:-1] else ""


def lane_profiles(consumer_argv, coach_command: str, *,
                  home: Path = Path.home()) -> dict:
    """Profile directory of each lane worker, as `gateway/consumer.py` picks
    it: main is the `--hermes-home` of `--worker-command`; call is that of
    `--lane-call-worker-command`, else the dedicated call profile under
    `home`; coach is that of the template's `lane_coach_worker_command`,
    else the dedicated coach profile under `home`."""
    home = Path(home)
    call_home = _hermes_home(_flag(consumer_argv, "--lane-call-worker-command"))
    coach_home = _hermes_home(coach_command)
    return {
        "main": _hermes_home(_flag(consumer_argv, "--worker-command")),
        "call": call_home or home / ".hermes" / "profiles" / "cothinker",
        "coach": coach_home or home / ".hermes" / "profiles" / "cothinker-coach",
    }


def load_invariants(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _disabled(profile: Path) -> list:
    # The key the runtime applies (gateway/worker.py): agent.disabled_toolsets.
    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8")) or {}
    return list((cfg.get("agent") or {}).get("disabled_toolsets") or [])


def check_lane(lane: str, profile, rules: dict, all_profiles: dict) -> Check:
    cid = f"preflight.lane-profile.{lane}"
    if profile is None or not (Path(profile) / "config.yaml").exists():
        return Check(cid, "FAIL", f"no profile config found (profile={profile})")
    profile = Path(profile)
    try:
        disabled = _disabled(profile)
    except (OSError, yaml.YAMLError) as e:
        return Check(cid, "FAIL", f"profile={profile} config.yaml unreadable: {e}")
    problems = []
    for t in rules.get("toolsets_enabled", []):
        if t in disabled:
            problems.append(f"toolset {t} disabled")
    for t in rules.get("toolsets_disabled", []):
        if t not in disabled:
            problems.append(f"toolset {t} enabled")
    soul_path = profile / "SOUL.md"
    soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    for pat in rules.get("soul_must_not_match", []):
        if re.search(pat, soul):
            problems.append(f"SOUL matches {pat}")
    for glob in rules.get("path_must_not_match", []):
        if fnmatch.fnmatch(str(profile), glob):
            problems.append(f"path matches {glob}")
    for other in rules.get("distinct_from", []):
        if all_profiles.get(other) == profile:
            problems.append(f"same profile as {other}")
    return Check(cid, "FAIL" if problems else "PASS",
                 f"profile={profile} disabled_toolsets={disabled}" +
                 (": " + "; ".join(problems) if problems else ""))


def read_cmdline(path: Path) -> list:
    """`<proc-root>/<pid>/cmdline`: NUL-separated argv."""
    return [p.decode("utf-8", errors="replace")
            for p in Path(path).read_bytes().split(b"\0") if p]


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
    lane = check_lane(
        "call", profile_dir,
        {"toolsets_enabled": [call_toolset],
         "path_must_not_match": [forbidden_profile_glob]}, {})
    return Check(name, lane.status, lane.evidence)


# --------------------------------------------------------------------- stt

def stt_values(log_text: str) -> dict:
    """eou_silence and max_delay of the LAST `[sm-rt]` start line."""
    found = _SMRT.findall(log_text)
    if not found:
        return {}
    md, eou = found[-1]
    return {"eou": float(eou), "max_delay": float(md)}


def check_stt(values: dict, expect_eou: float, expect_max_delay: float) -> list:
    out = []
    for key, flag, expected in (("eou", "eou_silence", expect_eou),
                                ("max_delay", "max_delay", expect_max_delay)):
        got = values.get(key)
        ok = got is not None and abs(got - expected) < 1e-9
        out.append(Check(f"preflight.stt.{key}", "PASS" if ok else "FAIL",
                         f"found {flag}={got} expected {expected} "
                         f"(source: last [sm-rt] start line)"))
    return out


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


def check_registry(template_path, registry: dict, log_tail: str) -> Check:
    """Every block of the template is in `registry`, and the consumer log
    since its last start holds no 'no block registered' line."""
    missing = sorted(set(_NOREG.findall(log_tail)))
    if template_path is not None:
        from shared.templates import load_template
        missing += sorted(b.id for b in load_template(Path(template_path)).blocks
                          if b.id not in registry and b.id not in missing)
    return Check("preflight.blocks.registry", "FAIL" if missing else "PASS",
                 f"unregistered: {missing}" if missing
                 else "every template block registered")


def check_unit_pending(unit: str, need_reload: bool, changed_after_start: int) -> Check:
    bad = need_reload or changed_after_start > 0
    return Check(f"preflight.unit.pending.{unit}", "FAIL" if bad else "PASS",
                 f"NeedDaemonReload={'yes' if need_reload else 'no'} "
                 f"unit/drop-in files changed after start: {changed_after_start}")


def _unit_pending_inputs(unit: str, sh: Callable) -> tuple:
    """-> (need_reload, files changed after the unit's start). Reads only
    `systemctl --user show` and file mtimes."""
    props = {}
    for line in sh(["systemctl", "--user", "show", "-p",
                    "NeedDaemonReload,ActiveEnterTimestamp,FragmentPath,DropInPaths",
                    unit]).splitlines():
        k, _, v = line.partition("=")
        props[k] = v.strip()
    need_reload = props.get("NeedDaemonReload") == "yes"
    started = props.get("ActiveEnterTimestamp", "")
    start_ts = None
    if started:
        out = sh(["date", "-d", started, "+%s"]).strip()
        start_ts = int(out) if out.isdigit() else None
    paths = [p for p in [props.get("FragmentPath", "")] +
             props.get("DropInPaths", "").split() if p]
    changed = 0
    for p in paths:
        try:
            if start_ts is not None and Path(p).stat().st_mtime > start_ts:
                changed += 1
        except OSError:
            pass
    return need_reload, changed


def _registry_for(template_path) -> dict:
    """Blocks of the template that `shared.block_registry` can load."""
    from shared.blocks import BlockError
    from shared.block_registry import load_block
    from shared.templates import load_template
    registry = {}
    for b in load_template(Path(template_path)).blocks:
        try:
            registry[b.id] = load_block(b.id)
        except BlockError:
            pass
    return registry


def _log_tail(path: Path, start_marker: str) -> str:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    marker = re.compile(start_marker)
    starts = [i for i, l in enumerate(lines) if marker.search(l)]
    return "\n".join(lines[(starts[-1] + 1) if starts else 0:])


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

def _coach_command(template) -> str:
    """The coach block's `lane_coach_worker_command` ('' = not configured)."""
    from gateway.block_host_wiring import coach_block_config
    cfg = (coach_block_config(Path(template)) if template
           else coach_block_config())
    return cfg.get("lane_coach_worker_command") or ""


def _lane_checks(*, cmdline: Path, invariants: Path, template, home: Path) -> list:
    rules = load_invariants(invariants)["lanes"]
    try:
        argv = read_cmdline(cmdline)
    except OSError as e:
        return [Check(f"preflight.lane-profile.{lane}", "FAIL",
                      f"consumer command line not readable at {cmdline}: "
                      f"{type(e).__name__}") for lane in rules]
    profiles = lane_profiles(argv, _coach_command(template), home=home)
    return [check_lane(lane, profiles.get(lane), lane_rules, profiles)
            for lane, lane_rules in rules.items()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--proc-root", default="/proc")
    ap.add_argument("--consumer-pid", type=int)
    ap.add_argument("--consumer-unit")
    ap.add_argument("--profile-env", default=DEFAULT_PROFILE_ENV)
    ap.add_argument("--call-toolset", default=DEFAULT_CALL_TOOLSET)
    ap.add_argument("--forbidden-profile-glob",
                    default=DEFAULT_FORBIDDEN_PROFILE_GLOB)
    ap.add_argument("--invariants", default=str(DEFAULT_INVARIANTS))
    ap.add_argument("--home", default=str(Path.home()),
                    help="home directory the consumer resolves its default "
                         "lane profiles under")
    ap.add_argument("--stt-log")
    ap.add_argument("--expect-eou", type=float, required=True)
    ap.add_argument("--expect-max-delay", type=float, required=True)
    ap.add_argument("--unit", action="append", default=[],
                    help="systemd user unit to check for pending changes "
                         "(repeatable)")
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
        checks.extend(_lane_checks(
            cmdline=Path(a.proc_root) / str(pid) / "cmdline",
            invariants=Path(a.invariants), template=a.template,
            home=Path(a.home)))

    if a.stt_log:
        try:
            text = Path(a.stt_log).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        checks.extend(check_stt(stt_values(text), a.expect_eou,
                                a.expect_max_delay))

    if a.consumer_log:
        checks.append(check_blocks_registered(
            consumer_log=Path(a.consumer_log), start_marker=a.start_marker))
        try:
            registry = _registry_for(a.template) if a.template else {}
            tail = _log_tail(Path(a.consumer_log), a.start_marker)
            checks.append(check_registry(a.template, registry, tail))
        except Exception as e:  # noqa: BLE001 -- reported as a FAIL
            checks.append(Check("preflight.blocks.registry", "FAIL",
                                f"not checkable: {type(e).__name__}: {e}"))
        template_check = check_template_blocks(
            Path(a.template) if a.template else None)
        if template_check is not None:
            checks.append(template_check)

    for unit in a.unit:
        need_reload, changed = _unit_pending_inputs(unit, run_sh)
        checks.append(check_unit_pending(unit, need_reload, changed))

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
