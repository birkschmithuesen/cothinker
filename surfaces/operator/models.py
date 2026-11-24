"""Pydantic models for the operator console contract (issue #159).

Mirrors `surfaces/contract/models.py`'s style (per-field `description` and
`examples`) so the same `generate_spec`-shaped tooling can turn these into
OpenAPI (request/answer) and AsyncAPI (the `/operator/events` push
channel). Every field named in issue #159's acceptance list is here as a
full field, not a `planned` placeholder: this issue is a full deliverable,
unlike the participant contract's still-growing schema.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SERVICE_NAMES = ("meeting", "stt", "audio-tap", "minutes", "stage", "contract")
ServiceName = Literal["meeting", "stt", "audio-tap", "minutes", "stage", "contract"]
ServiceState = Literal["active", "starting", "stopped", "failed"]


class Service(BaseModel):
    name: ServiceName = Field(description="Which part of the stack this entry reports on.", examples=["stt"])
    state: ServiceState = Field(description="The service's current lifecycle state.", examples=["active"])
    since: str = Field(description="Wall-clock label of when this service entered `state`.", examples=["2026-11-24T09:00:00+01:00"])


class Participant(BaseModel):
    name: str = Field(description="Display name of the participant.", examples=["Mira"])
    speaking: bool = Field(description="Whether this participant currently holds the floor.", examples=[True])
    level_dbfs: float = Field(description="Current audio level in dBFS.", examples=[-18.0])


class Meeting(BaseModel):
    participants: list[Participant] = Field(default_factory=list, description="Everyone currently in the meeting.")
    count: int = Field(description="Number of participants currently in the meeting.", examples=[1])


class Stt(BaseModel):
    connected: bool = Field(description="Whether the speech-to-text engine is connected.", examples=[True])
    engine: str = Field(description="Name of the speech-to-text engine in use.", examples=["speechmatics"])
    seconds_since_last_final: float = Field(description="Seconds since the last final transcript line.", examples=[1.5])
    minutes_today: float = Field(description="Minutes of audio transcribed today.", examples=[12.0])


class Project(BaseModel):
    id: str = Field(description="Identity of this working folder; never a free path.", examples=["quarterly-planning"])
    label: str = Field(description="Display label for this working folder.", examples=["Quarterly planning"])


class SessionAgenda(BaseModel):
    current_item: str = Field(description="Title of the current agenda item.", examples=["Opening round"])
    remaining_s: float = Field(description="Seconds remaining on the current item.", examples=[240.0])
    index: int = Field(description="1-based position of the current item.", examples=[1])
    total: int = Field(description="Total number of agenda items.", examples=[3])


class Session(BaseModel):
    project: Project
    session_folder: str = Field(description="Name of the current session's folder; empty when none is running.", examples=["2026-11-24-quarterly-planning"])
    agenda: SessionAgenda | None = Field(default=None, description="`None` for a session without an agenda.")
    utterances: int = Field(description="Number of transcript utterances recorded so far.", examples=[12])
    last_saved_at: str = Field(description="Wall-clock label of the last save; empty when nothing was saved yet.", examples=["2026-11-24T09:05:00+01:00"])


class CallAttempt(BaseModel):
    at: str = Field(description="Wall-clock label of this call attempt.", examples=["2026-11-24T09:01:00+01:00"])
    answered: bool = Field(description="Whether the call was answered.", examples=[True])
    latency_s: float = Field(description="Seconds between dialling and the call being answered.", examples=[1.2])


class Call(BaseModel):
    last: CallAttempt | None = Field(default=None, description="The most recent call attempt; `None` before the first one.")


class ErrorEntry(BaseModel):
    at: str = Field(description="Wall-clock label of when the error happened.", examples=["2026-11-24T09:02:00+01:00"])
    service: str = Field(description="Which service raised the error.", examples=["stt"])
    message: str = Field(
        description="A redacted error message: no tokens, no paths outside the"
        " project, no transcript text.",
        examples=["reconnect after timeout"],
    )


class Disk(BaseModel):
    free_percent: float = Field(description="Free disk space, as a percentage.", examples=[71.5])


class OperatorStatus(BaseModel):
    """The full snapshot `GET /operator/status` returns and `GET
    /operator/events` pushes (event `operator-status`) on change, at most
    once a second."""

    services: list[Service] = Field(default_factory=list)
    meeting: Meeting
    stt: Stt
    session: Session
    call: Call
    errors: list[ErrorEntry] = Field(default_factory=list, description="The last 10 errors, oldest first.")
    disk: Disk
    running: bool = Field(description="Whether a meeting is currently running.", examples=[True])
    can_switch_project: bool = Field(description="Whether `POST /operator/project` would be accepted right now.", examples=[False])


class ProjectsResponse(BaseModel):
    projects: list[Project] = Field(description="The allowed working folders, from deployment config.")


class StartRequest(BaseModel):
    project_id: str = Field(description="Id of an allowed working folder.", examples=["quarterly-planning"])
    title: str = Field(description="Title for the new session.", examples=["Quarterly planning"])


class StopRequest(BaseModel):
    confirm: bool = Field(description="Must be `true`; a safety guard against an accidental stop.", examples=[True])
    force: bool = Field(default=False, description="Stop even if participants are still in the meeting.", examples=[False])


class ProjectRequest(BaseModel):
    project_id: str = Field(description="Id of an allowed working folder.", examples=["quarterly-planning"])


class CommandResult(BaseModel):
    """Every command response carries the resulting status."""

    status: OperatorStatus
