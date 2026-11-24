"""The operator fixture backend (issue #159): "a fixture backend (test
mode: scripted state changes over ~2 min, commands accepted and
reflected)". Mirrors `surfaces/contract/player.py`'s `Frame`/
`ScenarioPlayer` shape: a list of frames replayed over real time, plus
commands that mutate the current snapshot directly and are reflected in
the very next `status()` call.
"""
from __future__ import annotations

import asyncio
import time

from surfaces.operator.backend import (
    AlreadyRunningError,
    NotRunningError,
    NotStoppedError,
    ParticipantsPresentError,
    UnknownProjectError,
)
from surfaces.operator.fixtures import ALLOWED_PROJECTS, FixtureOperatorBackend, OperatorFrame, build_frames


def test_initial_status_is_stopped_with_the_operator_service_always_on():
    backend = FixtureOperatorBackend()
    status = backend.status()
    assert status.running is False
    assert status.can_switch_project is True
    contract = next(s for s in status.services if s.name == "contract")
    assert contract.state == "active"


def test_fixture_serves_every_field_across_its_scripted_timeline():
    """issue #159 acceptance: "fixture mode serves all fields" -- every
    field must be non-empty/non-None in at least one frame, even though
    not every frame fills every field (the agenda, a call, an error all
    only show up once something has actually happened)."""
    frames = build_frames()
    statuses = [f.status for f in frames]

    assert any(s.meeting.participants for s in statuses)
    assert any(s.stt.connected for s in statuses)
    assert any(s.session.agenda is not None for s in statuses)
    assert any(s.session.session_folder for s in statuses)
    assert any(s.session.utterances > 0 for s in statuses)
    assert any(s.session.last_saved_at for s in statuses)
    assert any(s.call.last is not None for s in statuses)
    assert any(s.errors for s in statuses)
    assert any(s.disk.free_percent > 0 for s in statuses)
    assert any(s.running for s in statuses)
    assert any(not s.running for s in statuses)
    assert any(s.services and all(svc.state == "active" for svc in s.services) for s in statuses)
    assert any(s.session.title for s in statuses)
    assert all(e.level in ("info", "warning", "error") for s in statuses for e in s.errors)
    assert any(s.disk.free_bytes > 0 for s in statuses)
    assert any(s.disk.recording_hours_left is not None for s in statuses)
    assert any(s.disk.recording_hours_left is None for s in statuses)


def test_timeline_spans_about_two_minutes():
    frames = build_frames()
    total_s = sum(f.delay_s for f in frames)
    assert 90.0 <= total_s <= 150.0


def test_start_from_stopped_is_accepted_and_reflected():
    backend = FixtureOperatorBackend()
    project_id = ALLOWED_PROJECTS[0].id
    status = backend.start(project_id, "Quarterly planning")
    assert status.running is True
    assert status.session.project.id == project_id
    assert status.session.title == "Quarterly planning"
    assert backend.status().running is True   # reflected in the next read


def test_start_with_unknown_project_raises_and_leaves_backend_stopped():
    backend = FixtureOperatorBackend()
    try:
        backend.start("not-a-real-project", "Some title")
    except UnknownProjectError as exc:
        assert exc.project_id == "not-a-real-project"
    else:
        raise AssertionError("expected UnknownProjectError")
    assert backend.status().running is False   # nothing started


def test_start_when_already_running_raises_already_running_error():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    try:
        backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    except AlreadyRunningError:
        pass
    else:
        raise AssertionError("expected AlreadyRunningError")


def test_stop_with_participants_raises_unless_forced():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    backend.current = backend.current.model_copy(update={
        "meeting": backend.current.meeting.model_copy(update={"count": 2}),
    })
    try:
        backend.stop(confirm=True)
    except ParticipantsPresentError as exc:
        assert exc.participant_count == 2
    else:
        raise AssertionError("expected ParticipantsPresentError")
    assert backend.status().running is True   # still running

    status = backend.stop(confirm=True, force=True)
    assert status.running is False


def test_stop_without_participants_succeeds():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    status = backend.stop(confirm=True)
    assert status.running is False
    assert status.can_switch_project is True


def test_stop_while_not_running_raises_not_running_error():
    backend = FixtureOperatorBackend()
    try:
        backend.stop(confirm=True)
    except NotRunningError:
        pass
    else:
        raise AssertionError("expected NotRunningError")


def test_set_project_while_running_raises_not_stopped_error():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    try:
        backend.set_project(ALLOWED_PROJECTS[1].id)
    except NotStoppedError:
        pass
    else:
        raise AssertionError("expected NotStoppedError")


def test_set_project_while_stopped_updates_the_session_project():
    backend = FixtureOperatorBackend()
    status = backend.set_project(ALLOWED_PROJECTS[1].id)
    assert status.session.project.id == ALLOWED_PROJECTS[1].id


def test_set_project_with_unknown_id_raises_unknown_project_error():
    backend = FixtureOperatorBackend()
    try:
        backend.set_project("not-a-real-project")
    except UnknownProjectError:
        pass
    else:
        raise AssertionError("expected UnknownProjectError")


def test_run_replays_frames_in_order_over_real_time():
    frames = build_frames()
    tiny_frames = [OperatorFrame(delay_s=0.01, status=f.status) for f in frames[:3]]
    backend = FixtureOperatorBackend(frames=tiny_frames)

    async def _drive():
        task = asyncio.create_task(backend.run())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(_drive())
    assert backend.status() == tiny_frames[-1].status


def test_projects_returns_the_fixed_allowlist():
    backend = FixtureOperatorBackend()
    assert backend.projects() == list(ALLOWED_PROJECTS)


def test_build_frames_times_are_anchored_to_now_not_a_fixed_past_date():
    """issue #147 point 2's lesson applied here too: a hardcoded wall-clock
    label drifts out of the future; build frames relative to `now`."""
    before = time.time()
    frames = build_frames()
    after = time.time()
    total_s = sum(f.delay_s for f in frames)

    errors = [e for f in frames for e in f.status.errors]
    assert errors
    for error in errors:
        from datetime import datetime
        stamp = datetime.fromisoformat(error.at).timestamp()
        assert before <= stamp <= after + total_s
