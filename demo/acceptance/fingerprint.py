"""Fingerprint of the RUNNING deployment (ADR 0025 rule 6, spec E8).

Inputs: per unit the running process's argv (captures hand-written drop-ins),
an allow-listed slice of its environment, `systemctl --user cat` (unit +
drop-ins on disk); the lane profiles' behaviour files; the deployed commit and
local diff; demo.env. Excluded: secrets.env (no test tool reads a secret),
run.env and the session pointer (change on every start), logs, project data.
Only hashes leave this module."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UNITS = ["session", "stage", "stt", "chrome", "tap", "consumer", "contract"]
PROFILE_FILES = ("config.yaml", "SOUL.md")
_ENV_ALLOW = re.compile(rb"^(HERMES_PROFILE_DIR\w*|LANE_\w+|COTHINKER_\w+)=")
_ENV_DENY = (b"COTHINKER_SESSION_POINTER=",)


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_sh(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=False).stdout


@dataclass(frozen=True)
class Fingerprint:
    digest: str
    parts: dict[str, str]

    def to_dict(self) -> dict:
        return {"digest": self.digest, "parts": dict(self.parts)}

    def changed(self, other: "Fingerprint") -> list[str]:
        keys = set(self.parts) | set(other.parts)
        return sorted(k for k in keys if self.parts.get(k) != other.parts.get(k))


def from_inputs(inputs: dict) -> Fingerprint:
    parts = {k: _h(v) for k, v in sorted(inputs.items())}
    return Fingerprint(_h(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()), parts)


def filter_environ(raw: bytes) -> bytes:
    keep = [e for e in raw.split(b"\0") if _ENV_ALLOW.match(e) and not e.startswith(_ENV_DENY)]
    return b"\n".join(sorted(keep))


def _read_proc(path: Path, transform=lambda b: b) -> bytes:
    try:
        return transform(path.read_bytes())
    except OSError:  # process exited meanwhile, or not permitted
        return b"<unreadable>"


def _untracked(repo: Path, sh) -> bytes:
    names = sorted(n for n in sh(["git", "-C", str(repo), "ls-files", "--others",
                                  "--exclude-standard"]).splitlines() if n)
    out = []
    for n in names:
        try:
            body = _h((repo / n).read_bytes())
        except OSError:
            body = "<unreadable>"
        out.append(f"{n}\0{body}")
    return "\n".join(out).encode()


def collect(units, *, repo: Path, demo_env: Path, profiles: dict, sh=run_sh,
            proc: Path = Path("/proc")) -> dict:
    inputs: dict = {}
    for u in units:
        name = f"cothinker-demo-{u}.service"
        inputs[f"unit:{u}:cat"] = sh(["systemctl", "--user", "cat", name]).encode()
        pid = sh(["systemctl", "--user", "show", "-p", "MainPID", "--value", name]).strip()
        if pid and pid != "0" and (proc / pid).exists():
            inputs[f"unit:{u}:argv"] = _read_proc(proc / pid / "cmdline")
            inputs[f"unit:{u}:env"] = _read_proc(proc / pid / "environ", filter_environ)
        else:
            inputs[f"unit:{u}:argv"] = b"<not running>"
            inputs[f"unit:{u}:env"] = b"<not running>"
    for lane, d in sorted(profiles.items()):
        for f in PROFILE_FILES:
            p = Path(d) / f
            inputs[f"profile:{lane}:{f}"] = p.read_bytes() if p.exists() else b"<missing>"
        skills = Path(d) / "skills"
        if skills.is_dir():
            for f in sorted(x for x in skills.rglob("*") if x.is_file()):
                inputs[f"profile:{lane}:skills/{f.relative_to(skills).as_posix()}"] = f.read_bytes()
        else:
            inputs[f"profile:{lane}:skills"] = b"<missing>"
    inputs["repo:head"] = sh(["git", "-C", str(repo), "rev-parse", "HEAD"]).encode()
    inputs["repo:diff"] = sh(["git", "-C", str(repo), "diff", "HEAD"]).encode()
    inputs["repo:untracked"] = _untracked(repo, sh)
    inputs["demo.env"] = demo_env.read_bytes() if demo_env.exists() else b"<missing>"
    return inputs
