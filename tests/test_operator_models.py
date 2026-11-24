"""Pydantic models for the operator console contract (issue #159, ADR 0019
style: Pydantic models shared by OpenAPI and AsyncAPI generation).

`OperatorStatus` carries every field the issue's acceptance list names,
so a fixture that fills every field (see test_operator_fixtures.py) proves
the schema has no gap.
"""
from __future__ import annotations

from surfaces.operator.models import (
    Call,
    CallAttempt,
    Disk,
    ErrorEntry,
    Meeting,
    OperatorStatus,
    Participant,
    Project,
    ProjectRequest,
    Service,
    Session,
    SessionAgenda,
    StartRequest,
    StopRequest,
    Stt,
)


def _status(**overrides) -> OperatorStatus:
    base = dict(
        services=[Service(name="meeting", state="active", since="2026-11-24T09:00:00+01:00")],
        meeting=Meeting(participants=[Participant(name="Mira", speaking=True, level_dbfs=-18.0)], count=1),
        stt=Stt(connected=True, engine="speechmatics", seconds_since_last_final=1.5, minutes_today=12.0),
        session=Session(
            project=Project(id="quarterly-planning", label="Quarterly planning"),
            title="Quarterly planning",
            session_folder="2026-11-24-quarterly-planning",
            agenda=SessionAgenda(current_item="Opening round", remaining_s=240.0, index=1, total=3),
            utterances=12,
            last_saved_at="2026-11-24T09:05:00+01:00",
        ),
        call=Call(last=CallAttempt(at="2026-11-24T09:01:00+01:00", answered=True, latency_s=1.2)),
        errors=[ErrorEntry(at="2026-11-24T09:02:00+01:00", service="stt", message="reconnect after timeout", level="warning")],
        disk=Disk(free_percent=71.5, free_bytes=76_000_000_000, recording_hours_left=6.5),
        running=True,
        can_switch_project=False,
    )
    base.update(overrides)
    return OperatorStatus(**base)


def test_operator_status_round_trips_every_field_through_json():
    status = _status()
    payload = status.model_dump(mode="json")
    restored = OperatorStatus.model_validate(payload)
    assert restored == status


def test_service_state_is_restricted_to_the_four_named_states():
    for state in ("active", "starting", "stopped", "failed"):
        Service(name="meeting", state=state, since="2026-11-24T09:00:00+01:00")
    try:
        Service(name="meeting", state="bogus", since="2026-11-24T09:00:00+01:00")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unknown service state")


def test_errors_list_holds_at_most_ten_in_practice_but_schema_allows_a_plain_list():
    status = _status(errors=[
        ErrorEntry(at="2026-11-24T09:00:00+01:00", service="stt", message=f"error {i}", level="error")
        for i in range(10)
    ])
    assert len(status.errors) == 10


def test_start_request_carries_project_id_and_title():
    req = StartRequest(project_id="quarterly-planning", title="Quarterly planning")
    assert req.project_id == "quarterly-planning"
    assert req.title == "Quarterly planning"


def test_stop_request_defaults_force_to_false():
    req = StopRequest(confirm=True)
    assert req.confirm is True
    assert req.force is False


def test_project_request_carries_only_an_id():
    req = ProjectRequest(project_id="quarterly-planning")
    assert req.project_id == "quarterly-planning"


def test_session_title_defaults_to_none_when_no_session_is_running():
    session = Session(
        project=Project(id="quarterly-planning", label="Quarterly planning"),
        session_folder="",
        agenda=None,
        utterances=0,
        last_saved_at="",
    )
    assert session.title is None


def test_session_title_names_the_running_session():
    status = _status()
    assert status.session.title == "Quarterly planning"


def test_error_entry_level_is_restricted_to_info_warning_error():
    for level in ("info", "warning", "error"):
        ErrorEntry(at="2026-11-24T09:02:00+01:00", service="stt", message="reconnect after timeout", level=level)
    try:
        ErrorEntry(at="2026-11-24T09:02:00+01:00", service="stt", message="reconnect after timeout", level="bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unknown error level")


def test_disk_carries_free_bytes_alongside_free_percent():
    disk = Disk(free_percent=71.5, free_bytes=76_000_000_000, recording_hours_left=None)
    assert disk.free_bytes == 76_000_000_000


def test_disk_recording_hours_left_defaults_to_none():
    disk = Disk(free_percent=71.5, free_bytes=76_000_000_000)
    assert disk.recording_hours_left is None
