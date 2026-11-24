"""The demo-kit `OperatorBackend` (issue #159): drives `demo/demo.sh
start|stop|status` as subprocess calls and reads the units', tap's, STT's
and consumer's state from `status`'s output. Exactly those three verbs
are ever passed to `demo.sh` -- never `--all`, never any other flag,
never any other way of controlling the stack (see
`tests/test_operator_demo_backend.py`'s mutation check).

Field coverage is deliberately smaller than the fixture backend's: `demo.sh
status` (`demo/demo.sh`'s `do_status`) names unit/service state and the
STT/Jitsi reachability, not a participant name, a call attempt, an error
log or an agenda -- so `meeting.participants`, `call.last`, `errors` and
`session.agenda` stay at their empty default here until a later issue
wires a real source for them. "fixture mode serves all fields" (issue
#159 acceptance) is a property of the fixture backend; the demo-kit
backend's job is correctly driving the real stack, not simulating data it
has no source for.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from surfaces.operator.backend import (
    AlreadyRunningError,
    NotRunningError,
    NotStoppedError,
    ParticipantsPresentError,
    UnknownProjectError,
)
from surfaces.operator.models import Call, Disk, ErrorEntry, Meeting, OperatorStatus, Project, Service, Session, Stt

ALLOWED_VERBS = ("start", "stop", "status")

# demo.sh's own unit names (demo/demo.sh's DEMO_UNITS) mapped onto the
# operator contract's service names (issue #159's services[] list);
# "chrome" has no operator-facing name of its own -- it is the browser
# plumbing behind the audio tap, folded into "audio-tap"'s state.
_UNIT_FOR_SERVICE = {
    "meeting": "session",
    "stt": "stt",
    "audio-tap": "tap",
    "minutes": "consumer",
    "stage": "stage",
    "contract": "contract",
}

_STATE_FOR_SYSTEMCTL = {
    "active": "active",
    "activating": "starting",
    "inactive": "stopped",
    "deactivating": "stopped",
    "failed": "failed",
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


_KNOWN_SYSTEMCTL_STATES = set(_STATE_FOR_SYSTEMCTL)


def _parse_unit_states(status_output: str) -> dict[str, str]:
    """`demo.sh status`'s `printf '%-10s %s\\n'` unit lines, turned into
    `{unit: systemctl state}`. Matched on the second token being a known
    systemctl state, not just the first token being a known unit name:
    `demo.sh`'s own `do_status` also prints a line starting with the
    literal word "stt" ("stt /status: ok"/"down"), which would otherwise
    collide with the `stt` unit's own status line."""
    known_units = set(_UNIT_FOR_SERVICE.values())
    states: dict[str, str] = {}
    for line in status_output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in known_units and parts[1] in _KNOWN_SYSTEMCTL_STATES:
            states[parts[0]] = parts[1]
    return states


def _stt_connected(status_output: str) -> bool:
    return "stt /status: ok" in status_output


def _session_folder() -> str:
    """The current run's session directory, written by `demo/demo.sh`'s
    `start_all` into `$XDG_RUNTIME_DIR/cothinker-demo/contract.env` as
    `DEMO_SESSION_DIR` -- the same file the `contract` unit (live mode)
    reads. Empty when no run has started one yet."""
    run_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "cothinker-demo"
    contract_env = run_dir / "contract.env"
    if not contract_env.exists():
        return ""
    for line in contract_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEMO_SESSION_DIR="):
            return line[len("DEMO_SESSION_DIR="):].strip()
    return ""


def _disk_free_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    if usage.total == 0:
        return 0.0
    return round(usage.free / usage.total * 100, 1)


def _disk_free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class DemoKitOperatorBackend:
    def __init__(
        self,
        demo_sh: str | Path,
        projects: list[Project],
        runner: Callable[[list[str]], "subprocess.CompletedProcess[str]"] | None = None,
        disk_path: str | Path = ".",
    ):
        self._demo_sh = Path(demo_sh)
        self._projects = list(projects)
        self._run_subprocess = runner or self._default_runner
        self._disk_path = Path(disk_path)
        self._current_project = self._projects[0] if self._projects else None
        self._current_title: str | None = None
        self._since: dict[str, str] = {}
        self._last_state: dict[str, str] = {}
        self._errors: list[ErrorEntry] = []
        self._recording_folder: str = ""
        self._recording_samples: list[tuple[float, int]] = []

    @staticmethod
    def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def _call(self, verb: str) -> "subprocess.CompletedProcess[str]":
        if verb not in ALLOWED_VERBS:
            raise ValueError(f"not an allowed demo.sh verb: {verb!r}")
        return self._run_subprocess([str(self._demo_sh), verb])

    def _services(self, unit_states: dict[str, str]) -> list[Service]:
        now_iso = _iso(time.time())
        services = []
        for service_name, unit_name in _UNIT_FOR_SERVICE.items():
            raw = unit_states.get(unit_name, "inactive")
            state = _STATE_FOR_SYSTEMCTL.get(raw, "stopped")
            if self._last_state.get(service_name) != state:
                self._last_state[service_name] = state
                self._since[service_name] = now_iso
                if state == "failed":
                    self._record_error(service_name, now_iso)
            services.append(Service(name=service_name, state=state, since=self._since[service_name]))
        return services

    def _record_error(self, service_name: str, at: str) -> None:
        self._errors.append(ErrorEntry(at=at, service=service_name, message="systemd unit reported failed", level="error"))
        self._errors = self._errors[-10:]

    def _recording_hours_left(self, running: bool, session_folder: str, free_bytes: int) -> float | None:
        if not running or not session_folder:
            self._recording_folder = session_folder
            self._recording_samples = []
            return None
        if session_folder != self._recording_folder:
            self._recording_folder = session_folder
            self._recording_samples = []
        folder_path = Path(session_folder)
        if not folder_path.is_dir():
            return None
        self._recording_samples.append((time.time(), _dir_size_bytes(folder_path)))
        self._recording_samples = self._recording_samples[-2:]
        if len(self._recording_samples) < 2:
            return None
        (t0, s0), (t1, s1) = self._recording_samples
        elapsed = t1 - t0
        if elapsed <= 0:
            return None
        rate = (s1 - s0) / elapsed
        if rate <= 0:
            return None
        return round(free_bytes / rate / 3600, 2)

    def status(self) -> OperatorStatus:
        result = self._call("status")
        services = self._services(_parse_unit_states(result.stdout))
        running = next(s.state for s in services if s.name == "meeting") == "active"
        session_folder = _session_folder()
        free_bytes = _disk_free_bytes(self._disk_path)
        return OperatorStatus(
            services=services,
            meeting=Meeting(participants=[], count=0),
            stt=Stt(
                connected=_stt_connected(result.stdout),
                engine="speechmatics",
                seconds_since_last_final=0.0,
                minutes_today=0.0,
            ),
            session=Session(
                project=self._current_project or Project(id="", label=""),
                title=self._current_title,
                session_folder=session_folder,
                agenda=None,
                utterances=0,
                last_saved_at="",
            ),
            call=Call(last=None),
            errors=list(self._errors),
            disk=Disk(
                free_percent=_disk_free_percent(self._disk_path),
                free_bytes=free_bytes,
                recording_hours_left=self._recording_hours_left(running, session_folder, free_bytes),
            ),
            running=running,
            can_switch_project=not running,
        )

    def projects(self) -> list[Project]:
        return list(self._projects)

    def _project_or_raise(self, project_id: str) -> Project:
        for project in self._projects:
            if project.id == project_id:
                return project
        raise UnknownProjectError(project_id)

    def start(self, project_id: str, title: str) -> OperatorStatus:
        if self.status().running:
            raise AlreadyRunningError()
        project = self._project_or_raise(project_id)
        self._current_project = project
        self._current_title = title
        self._call("start")
        return self.status()

    def stop(self, confirm: bool, force: bool = False) -> OperatorStatus:
        if not confirm:
            raise ValueError("stop requires confirm=true")
        current = self.status()
        if not current.running:
            raise NotRunningError()
        if current.meeting.count > 0 and not force:
            raise ParticipantsPresentError(current.meeting.count)
        self._call("stop")
        return self.status()

    def set_project(self, project_id: str) -> OperatorStatus:
        if self.status().running:
            raise NotStoppedError()
        project = self._project_or_raise(project_id)
        self._current_project = project
        return self.status()

    async def run(self) -> None:
        return
