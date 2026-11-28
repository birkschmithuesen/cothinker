"""Optional, rootless network impairment for synthetic participants.

A participant command is run inside its own user + network namespace
(`unshare --user --map-root-user --net`). `slirp4netns` gives that namespace a
`tap0` uplink from the outside, and `tc qdisc ... netem` on `tap0` adds packet
loss and delay. Nothing here ever needs `sudo`; when the tools or the kernel
setting are missing, `available()` says why and the caller reports SKIPPED.

Usage as a wrapper:
    python3 -m demo.acceptance.netem --spec loss=2%,delay=80ms -- <command...>
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional

_VALUE_SHAPES = {
    "loss": re.compile(r"\d{1,3}(\.\d{1,3})?%"),
    "delay": re.compile(r"\d{1,5}(\.\d{1,3})?ms"),
}
_TOOLS = ("unshare", "slirp4netns", "tc")
_PROBE_TIMEOUT_S = 10.0
_TAP_WAIT_S = 10.0

# Waits for the uplink device, applies the qdisc from positional arguments
# (never interpolated into the script text), then replaces itself by the
# participant command.
_SCRIPT = (
    'i=0; until tc qdisc show dev tap0 >/dev/null 2>&1; do '
    'i=$((i+1)); [ "$i" -gt 150 ] && { echo "netem: tap0 never appeared" >&2; exit 97; }; '
    'sleep 0.1; done; '
    'tc qdisc add dev tap0 root netem loss "$1" delay "$2" || exit 98; '
    'shift 2; exec "$@"'
)


def parse_spec(s: str) -> dict:
    """`"loss=2%,delay=80ms"` -> `{"loss": "2%", "delay": "80ms"}`; strict."""
    out: dict = {}
    for part in s.split(","):
        key, sep, val = part.partition("=")
        if not sep or key not in _VALUE_SHAPES:
            raise ValueError(f"bad netem spec item {part!r}; keys: {sorted(_VALUE_SHAPES)}")
        if key in out:
            raise ValueError(f"netem key {key!r} given twice")
        if not _VALUE_SHAPES[key].fullmatch(val):
            raise ValueError(f"bad netem value for {key}: {val!r}")
        out[key] = val
    return out


def _probe() -> tuple[bool, str]:
    try:
        r = subprocess.run(["unshare", "--user", "--map-root-user", "--net", "true"],
                           capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, "unshare probe timed out"
    except OSError as exc:
        return False, f"unshare probe could not run: {exc}"
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        return False, "unshare --user --net refused: " + (detail[-1] if detail else f"exit {r.returncode}")
    return True, ""


def available(which=shutil.which, probe=_probe) -> tuple[bool, str]:
    missing = [t for t in _TOOLS if not which(t)]
    if missing:
        return False, "missing tool(s) for rootless netem: " + ", ".join(missing)
    return probe()


def _spec_text(spec: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in spec.items())


def wrap(cmd: list[str], spec: dict) -> list[str]:
    return [sys.executable, "-m", "demo.acceptance.netem",
            "--spec", _spec_text(spec), "--", *cmd]


def inner_argv(spec: dict, cmd: list[str]) -> list[str]:
    return ["unshare", "--user", "--map-root-user", "--net", "--fork", "--",
            "sh", "-c", _SCRIPT, "sh", spec.get("loss", "0%"), spec.get("delay", "0ms"),
            *cmd]


def _net_ns(pid: int):
    try:
        return os.readlink(f"/proc/{pid}/ns/net")
    except OSError:
        return None


# Exit codes reserved for impairment setup failures. Neither the participant
# nor Chrome use them, so a caller can tell "netem could not be set up" apart
# from a real participant failure.
EXIT_SLIRP_FAILED = 95
EXIT_HANDOFF_TIMEOUT = 96
EXIT_TAP_TIMEOUT = 97   # raised by the inner script
EXIT_TC_FAILED = 98     # raised by the inner script
FAILURE_REASONS = {
    EXIT_SLIRP_FAILED: "slirp4netns exited before the participant finished",
    EXIT_HANDOFF_TIMEOUT: "the network namespace was not ready for slirp4netns in time",
    EXIT_TAP_TIMEOUT: "tap0 never appeared inside the namespace",
    EXIT_TC_FAILED: "tc netem could not be applied to tap0",
}
EXIT_TERMINATED = 128 + signal.SIGTERM


def failure_reason(code) -> Optional[str]:
    """Reason text when `code` is a netem setup failure, else None."""
    return FAILURE_REASONS.get(code)


def _exit_code(rc: int) -> int:
    return 128 - rc if rc < 0 else rc  # signal death -> 128 + signum


def _stop_group(child, killpg, grace_s: float) -> None:
    """Ends the whole process group of `child` (unshare, shell, participant,
    browser) so nothing is orphaned, and reaps the child."""
    try:
        killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        child.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        try:
            killpg(child.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        child.wait()


def _sigterm_to_interrupt(signum, _frame):
    raise KeyboardInterrupt(f"signal {signum}")


def run(spec: dict, cmd: list[str], popen=subprocess.Popen, ns_of=_net_ns,
        sleep=time.sleep, killpg=os.killpg, handoff_s: float = _TAP_WAIT_S,
        grace_s: float = 5.0, clock=time.time) -> int:
    child = popen(inner_argv(spec, cmd), start_new_session=True)
    slirp = None
    errlog = tempfile.TemporaryFile()
    try:
        mine = ns_of(os.getpid())
        deadline = clock() + handoff_s
        while ns_of(child.pid) in (None, mine):  # child has not unshared yet
            if child.poll() is not None:
                return _exit_code(child.returncode)
            if clock() > deadline:
                print("netem: namespace handoff timed out; stopping the participant",
                      file=sys.stderr)
                return EXIT_HANDOFF_TIMEOUT
            sleep(0.02)
        slirp = popen(["slirp4netns", "--configure", "--mtu=65520", str(child.pid), "tap0"],
                      stdout=subprocess.DEVNULL, stderr=errlog)
        while child.poll() is None:
            if slirp.poll() is not None:
                errlog.seek(0)
                tail = errlog.read().decode("utf-8", "replace").strip()[-300:]
                print(f"netem: slirp4netns exited early (code {slirp.returncode}): "
                      f"{tail or 'no output'}", file=sys.stderr)
                return EXIT_SLIRP_FAILED
            sleep(0.1)
        return _exit_code(child.returncode)
    except KeyboardInterrupt:
        return EXIT_TERMINATED
    finally:
        _stop_group(child, killpg, grace_s)
        if slirp is not None:
            if slirp.poll() is None:
                slirp.terminate()
            try:
                slirp.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                slirp.kill()
                slirp.wait()
        errlog.close()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: netem --spec loss=2%,delay=80ms -- <command...>", file=sys.stderr)
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--spec", required=True)
    args = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]
    if not cmd:
        print("netem: no command given", file=sys.stderr)
        return 2
    try:
        spec = parse_spec(args.spec)
    except ValueError as exc:
        print(f"netem: {exc}", file=sys.stderr)
        return 2
    previous = signal.signal(signal.SIGTERM, _sigterm_to_interrupt)
    try:
        return run(spec, cmd)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
