"""Shared helpers of the operator console tests."""
from __future__ import annotations

from surfaces.operator.models import Call, Disk, Meeting, OperatorStatus, Project, Session, Stt

TOKEN = "console-test-token"


def empty_status() -> OperatorStatus:
    return OperatorStatus(
        services=[], meeting=Meeting(participants=[], count=0),
        stt=Stt(connected=False, engine="", seconds_since_last_final=0.0, minutes_today=0.0),
        session=Session(project=Project(id="", label=""), title=None, session_folder="", agenda=None,
                        utterances=0, last_saved_at=""),
        call=Call(last=None), errors=[], disk=Disk(free_percent=0.0, free_bytes=0, recording_hours_left=None),
        running=False, can_switch_project=True,
    )
