"""The demo-kit OperatorBackend (issue #159): "drives `demo/demo.sh
start|stop|status` ... Exactly those three verbs are ever passed to
demo.sh ... never shells out to anything else to control the stack."
Modeled after tests/test_demo_sh.py's style, but this is a Python object
calling subprocess, so the natural check is a mutation test against an
injected runner spy, not a parse of the shell script.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from surfaces.operator.backend import AlreadyRunningError, NotRunningError, UnknownProjectError
from surfaces.operator.demo_backend import DemoKitOperatorBackend
from surfaces.operator.models import Project

DEMO_SH = Path("/fake/demo/demo.sh")
PROJECTS = [Project(id="quarterly-planning", label="Quarterly planning")]


def _status_stdout(running: bool) -> str:
    state = "active" if running else "inactive"
    lines = [f"{unit:<10} {state}" for unit in ("session", "stage", "stt", "chrome", "tap", "consumer", "contract")]
    lines.append("stt /status: ok" if running else "stt /status: down")
    lines.append("jitsi web: ok" if running else "jitsi web: down")
    lines.append("listening:")
    return "\n".join(lines) + "\n"


def _spy(calls: list[list[str]], state: dict):
    def runner(args: list[str]) -> subprocess.CompletedProcess:
        calls.append(args)
        verb = args[-1]
        if verb == "start":
            state["running"] = True
        elif verb == "stop":
            state["running"] = False
        return subprocess.CompletedProcess(args, 0, stdout=_status_stdout(state["running"]), stderr="")
    return runner


def test_backend_only_ever_calls_demo_sh_with_start_stop_or_status(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )

    backend.status()
    backend.start("quarterly-planning", "Quarterly planning")
    backend.stop(confirm=True)
    try:
        backend.start("not-a-real-project", "Nope")
    except UnknownProjectError:
        pass
    backend.set_project("quarterly-planning")

    assert calls
    for args in calls:
        assert args[0] == str(DEMO_SH)
        assert len(args) == 2, f"unexpected extra argument(s): {args!r}"
        assert args[1] in ("start", "stop", "status"), f"disallowed verb: {args!r}"


def test_unknown_project_on_start_never_calls_demo_sh_start(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )

    try:
        backend.start("not-a-real-project", "Nope")
    except UnknownProjectError:
        pass
    else:
        raise AssertionError("expected UnknownProjectError")

    assert all(args[1] != "start" for args in calls)
    assert state["running"] is False


def test_start_when_already_running_raises_without_calling_start_again(tmp_path):
    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    try:
        backend.start("quarterly-planning", "Quarterly planning")
    except AlreadyRunningError:
        pass
    else:
        raise AssertionError("expected AlreadyRunningError")
    assert all(args[1] != "start" for args in calls)


def test_meeting_participant_count_is_zero_until_a_later_issue_wires_it_up():
    """`demo.sh status` names no participant, only unit/service state
    (docstring decision, issue #159): the demo-kit backend reports
    `meeting.count == 0` rather than guessing. `ParticipantsPresentError`
    is still exercised end-to-end against the fixture backend
    (tests/test_operator_app.py); wiring a real participant count into
    this backend is later work, not part of this issue's acceptance list."""
    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path="."
    )
    status = backend.status()
    assert status.meeting.count == 0
    assert status.meeting.participants == []


def test_stop_while_not_running_raises_not_running_error(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    try:
        backend.stop(confirm=True)
    except NotRunningError:
        pass
    else:
        raise AssertionError("expected NotRunningError")


def test_status_maps_unit_states_onto_the_operator_service_names(tmp_path):
    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    names = {s.name for s in status.services}
    assert names == {"meeting", "stt", "audio-tap", "minutes", "stage", "contract"}
    assert all(s.state == "active" for s in status.services)
    assert status.running is True
    assert status.stt.connected is True


def test_status_reports_stopped_services_when_demo_sh_reports_inactive(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert all(s.state == "stopped" for s in status.services)
    assert status.running is False
    assert status.can_switch_project is True


def test_disk_free_percent_is_a_real_measurement_of_disk_path(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert 0.0 < status.disk.free_percent <= 100.0


def test_disk_free_bytes_is_a_real_measurement_of_disk_path(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.disk.free_bytes > 0


def test_recording_hours_left_is_none_while_not_running(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.disk.recording_hours_left is None


def test_recording_hours_left_is_none_right_after_recording_starts(tmp_path, monkeypatch):
    run_dir = tmp_path / "xdg-runtime"
    session_dir = tmp_path / "session"
    (run_dir / "cothinker-demo").mkdir(parents=True)
    session_dir.mkdir()
    (run_dir / "cothinker-demo" / "contract.env").write_text(
        f"DEMO_SESSION_DIR={session_dir}\n", encoding="utf-8",
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    (session_dir / "audio.wav").write_bytes(b"x" * 1000)

    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.disk.recording_hours_left is None   # only one sample so far: no rate yet


def test_recording_hours_left_is_a_positive_number_once_the_session_folder_is_growing(tmp_path, monkeypatch):
    run_dir = tmp_path / "xdg-runtime"
    session_dir = tmp_path / "session"
    (run_dir / "cothinker-demo").mkdir(parents=True)
    session_dir.mkdir()
    (run_dir / "cothinker-demo" / "contract.env").write_text(
        f"DEMO_SESSION_DIR={session_dir}\n", encoding="utf-8",
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    (session_dir / "audio.wav").write_bytes(b"x" * 1000)

    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    backend.status()   # first sample
    time.sleep(0.05)
    (session_dir / "audio.wav").write_bytes(b"x" * 2000)
    status = backend.status()   # second sample: a rate is now measurable
    assert status.disk.recording_hours_left is not None
    assert status.disk.recording_hours_left > 0.0


def test_recording_hours_left_resets_when_the_session_folder_changes(tmp_path, monkeypatch):
    run_dir = tmp_path / "xdg-runtime"
    session_dir = tmp_path / "session"
    (run_dir / "cothinker-demo").mkdir(parents=True)
    session_dir.mkdir()
    env_file = run_dir / "cothinker-demo" / "contract.env"
    env_file.write_text(f"DEMO_SESSION_DIR={session_dir}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    (session_dir / "audio.wav").write_bytes(b"x" * 1000)

    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    backend.status()
    time.sleep(0.05)
    (session_dir / "audio.wav").write_bytes(b"x" * 2000)
    backend.status()   # a rate is measurable for the first session

    other_session_dir = tmp_path / "other-session"
    other_session_dir.mkdir()
    (other_session_dir / "audio.wav").write_bytes(b"x" * 1000)
    env_file.write_text(f"DEMO_SESSION_DIR={other_session_dir}\n", encoding="utf-8")
    status = backend.status()   # new session folder: only one sample again
    assert status.disk.recording_hours_left is None


def test_start_sets_the_session_title(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.start("quarterly-planning", "Quarterly planning")
    assert status.session.title == "Quarterly planning"


def test_session_title_is_none_until_a_session_starts(tmp_path):
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.session.title is None


def test_a_failed_unit_produces_an_error_entry_at_error_level(tmp_path):
    def runner(args: list[str]) -> subprocess.CompletedProcess:
        lines = [f"{unit:<10} {'failed' if unit == 'stt' else 'active'}"
                  for unit in ("session", "stage", "stt", "chrome", "tap", "consumer", "contract")]
        lines.append("stt /status: down")
        lines.append("jitsi web: ok")
        lines.append("listening:")
        return subprocess.CompletedProcess(args, 0, stdout="\n".join(lines) + "\n", stderr="")

    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=runner, disk_path=tmp_path,
    )
    status = backend.status()
    assert any(e.service == "stt" and e.level == "error" for e in status.errors)


def test_a_failed_unit_is_only_recorded_once_per_transition(tmp_path):
    def runner(args: list[str]) -> subprocess.CompletedProcess:
        lines = [f"{unit:<10} {'failed' if unit == 'stt' else 'active'}"
                  for unit in ("session", "stage", "stt", "chrome", "tap", "consumer", "contract")]
        lines.append("stt /status: down")
        lines.append("jitsi web: ok")
        lines.append("listening:")
        return subprocess.CompletedProcess(args, 0, stdout="\n".join(lines) + "\n", stderr="")

    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=runner, disk_path=tmp_path,
    )
    backend.status()
    status = backend.status()
    stt_errors = [e for e in status.errors if e.service == "stt"]
    assert len(stt_errors) == 1


def test_no_errors_when_every_unit_is_active(tmp_path):
    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.errors == []


def test_session_folder_is_read_from_the_contract_env_file(tmp_path, monkeypatch):
    run_dir = tmp_path / "xdg-runtime"
    (run_dir / "cothinker-demo").mkdir(parents=True)
    (run_dir / "cothinker-demo" / "contract.env").write_text(
        "DEMO_SESSION_DIR=/some/project/sessions/2026-11-24-quarterly-planning\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))

    calls: list[list[str]] = []
    state = {"running": True}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.session.session_folder == "/some/project/sessions/2026-11-24-quarterly-planning"


def test_session_folder_is_empty_when_no_contract_env_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "no-such-runtime-dir"))
    calls: list[list[str]] = []
    state = {"running": False}
    backend = DemoKitOperatorBackend(
        demo_sh=DEMO_SH, projects=PROJECTS, runner=_spy(calls, state), disk_path=tmp_path,
    )
    status = backend.status()
    assert status.session.session_folder == ""
