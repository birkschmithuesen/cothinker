"""The operator fixture backend (issue #159): "a fixture backend (test
mode: scripted state changes over ~2 min, commands accepted and
reflected)". Mirrors `surfaces/contract/player.py`'s `Frame`/
`ScenarioPlayer` shape -- a list of frames replayed over real time -- but
holds a single current `OperatorStatus` snapshot (not view intents): the
operator contract pushes a full snapshot on change, never a discrete
intent.

Commands (`start`/`stop`/`set_project`) mutate the current snapshot
directly and are reflected in the very next `status()` call, independent
of the scripted timeline below; a caller that never awaits `run()` (every
unit test in `tests/test_operator_fixtures.py` but the one that drives it
explicitly) only ever sees command-driven state, never the autonomous
script.

Invented data, no real persons, same spirit as
`surfaces/contract/fixtures/meeting/__init__.py`.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from surfaces.operator.backend import (
    AlreadyRunningError,
    NotRunningError,
    NotStoppedError,
    ParticipantsPresentError,
    UnknownProjectError,
)
from surfaces.operator.models import (
    Call,
    CallAttempt,
    Disk,
    ErrorEntry,
    Meeting,
    OperatorStatus,
    Participant,
    Project,
    Service,
    Session,
    SessionAgenda,
    Stt,
)

SERVICE_NAMES = ("meeting", "stt", "audio-tap", "minutes", "stage", "contract")

ALLOWED_PROJECTS = (
    Project(id="quarterly-planning", label="Quarterly planning"),
    Project(id="design-retro", label="Design retro"),
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _services(*, running: bool, since: str) -> list[Service]:
    return [
        Service(name=name, state=("active" if running or name == "contract" else "stopped"), since=since)
        for name in SERVICE_NAMES
    ]


@dataclass
class OperatorFrame:
    """One step of the fixture's scripted timeline; `delay_s` is the real
    seconds to wait after the PREVIOUS frame before `status` becomes
    current (same convention as `surfaces.contract.player.Frame`)."""

    delay_s: float
    status: OperatorStatus


def build_frames(now: float | None = None) -> list[OperatorFrame]:
    """The fixture's frames, anchored to `now` (default: the moment this
    is called) -- a full autonomous demo loop: idle, start, a participant
    joins and speaks, a second one joins, a call attempt and an error
    land, the meeting winds down, stop. Every field named in issue #159's
    acceptance list is non-empty in at least one frame."""
    now = time.time() if now is None else now
    t0, t1, t2, t3, t4, t5, t6 = (_iso(now + offset) for offset in (0, 20, 40, 60, 80, 100, 120))

    stopped = OperatorStatus(
        services=_services(running=False, since=t0),
        meeting=Meeting(participants=[], count=0),
        stt=Stt(connected=False, engine="speechmatics", seconds_since_last_final=0.0, minutes_today=0.0),
        session=Session(
            project=ALLOWED_PROJECTS[0], session_folder="", agenda=None, utterances=0, last_saved_at="",
        ),
        call=Call(last=None),
        errors=[],
        disk=Disk(free_percent=74.0),
        running=False,
        can_switch_project=True,
    )

    starting = stopped.model_copy(update={
        "services": _services(running=True, since=t1),
        "running": True,
        "can_switch_project": False,
        "session": stopped.session.model_copy(update={
            "session_folder": "2026-11-24-quarterly-planning",
        }),
    })

    mira_joins = starting.model_copy(update={
        "meeting": Meeting(participants=[Participant(name="Mira", speaking=True, level_dbfs=-18.0)], count=1),
        "stt": Stt(connected=True, engine="speechmatics", seconds_since_last_final=0.4, minutes_today=0.3),
        "session": starting.session.model_copy(update={
            "agenda": SessionAgenda(current_item="Opening round", remaining_s=280.0, index=1, total=3),
            "utterances": 2,
            "last_saved_at": t2,
        }),
    })

    jonas_joins = mira_joins.model_copy(update={
        "meeting": Meeting(
            participants=[
                Participant(name="Mira", speaking=False, level_dbfs=-40.0),
                Participant(name="Jonas", speaking=True, level_dbfs=-16.5),
            ],
            count=2,
        ),
        "call": Call(last=CallAttempt(at=t3, answered=True, latency_s=1.4)),
        "session": mira_joins.session.model_copy(update={
            "agenda": SessionAgenda(current_item="Decisions & risks", remaining_s=260.0, index=2, total=3),
            "utterances": 5,
            "last_saved_at": t3,
        }),
    })

    error_lands = jonas_joins.model_copy(update={
        "stt": Stt(connected=True, engine="speechmatics", seconds_since_last_final=8.0, minutes_today=1.1),
        "errors": [ErrorEntry(at=t4, service="stt", message="reconnect after timeout")],
        "disk": Disk(free_percent=71.0),
        "session": jonas_joins.session.model_copy(update={"utterances": 7, "last_saved_at": t4}),
    })

    closing = error_lands.model_copy(update={
        "meeting": Meeting(participants=[Participant(name="Jonas", speaking=True, level_dbfs=-20.0)], count=1),
        "stt": Stt(connected=True, engine="speechmatics", seconds_since_last_final=0.6, minutes_today=1.6),
        "session": error_lands.session.model_copy(update={
            "agenda": SessionAgenda(current_item="Closing round", remaining_s=90.0, index=3, total=3),
            "utterances": 9,
            "last_saved_at": t5,
        }),
    })

    stopped_again = closing.model_copy(update={
        "services": _services(running=False, since=t6),
        "meeting": Meeting(participants=[], count=0),
        "running": False,
        "can_switch_project": True,
    })

    return [
        OperatorFrame(delay_s=0.0, status=stopped),
        OperatorFrame(delay_s=20.0, status=starting),
        OperatorFrame(delay_s=20.0, status=mira_joins),
        OperatorFrame(delay_s=20.0, status=jonas_joins),
        OperatorFrame(delay_s=20.0, status=error_lands),
        OperatorFrame(delay_s=20.0, status=closing),
        OperatorFrame(delay_s=20.0, status=stopped_again),
    ]


class FixtureOperatorBackend:
    """Test-mode `OperatorBackend`: `status()` reflects whichever happened
    most recently -- a command, or (while `run()` is awaited) the next
    scripted frame. `current` is a public attribute, like
    `ScenarioPlayer.snapshot`, so a test can poke it directly to set up a
    specific state (e.g. participants present) without replaying frames."""

    def __init__(self, frames: list[OperatorFrame] | None = None, projects: tuple[Project, ...] | None = None):
        self._frames = frames if frames is not None else build_frames()
        self._projects = projects if projects is not None else ALLOWED_PROJECTS
        self.current = self._frames[0].status

    def status(self) -> OperatorStatus:
        return self.current

    def projects(self) -> list[Project]:
        return list(self._projects)

    def _project_or_raise(self, project_id: str) -> Project:
        for project in self._projects:
            if project.id == project_id:
                return project
        raise UnknownProjectError(project_id)

    def start(self, project_id: str, title: str) -> OperatorStatus:
        if self.current.running:
            raise AlreadyRunningError()
        project = self._project_or_raise(project_id)
        now_iso = _iso(time.time())
        today = datetime.now(tz=timezone.utc).astimezone().date().isoformat()
        self.current = self.current.model_copy(update={
            "running": True,
            "can_switch_project": False,
            "services": _services(running=True, since=now_iso),
            "session": self.current.session.model_copy(update={
                "project": project,
                "session_folder": f"{today}-{project_id}",
            }),
        })
        return self.current

    def stop(self, confirm: bool, force: bool = False) -> OperatorStatus:
        if not confirm:
            raise ValueError("stop requires confirm=true")
        if not self.current.running:
            raise NotRunningError()
        if self.current.meeting.count > 0 and not force:
            raise ParticipantsPresentError(self.current.meeting.count)
        now_iso = _iso(time.time())
        self.current = self.current.model_copy(update={
            "running": False,
            "can_switch_project": True,
            "services": _services(running=False, since=now_iso),
            "meeting": Meeting(participants=[], count=0),
        })
        return self.current

    def set_project(self, project_id: str) -> OperatorStatus:
        if self.current.running:
            raise NotStoppedError()
        project = self._project_or_raise(project_id)
        self.current = self.current.model_copy(update={
            "session": self.current.session.model_copy(update={"project": project}),
        })
        return self.current

    async def run(self) -> None:
        """Replay every frame once, in order, respecting its `delay_s`."""
        for frame in self._frames:
            await asyncio.sleep(frame.delay_s)
            self.current = frame.status
