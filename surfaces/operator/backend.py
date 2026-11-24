"""The `OperatorBackend` protocol (issue #159): one interface, two
implementations -- `surfaces.operator.fixtures.FixtureOperatorBackend`
(test mode) and `surfaces.operator.demo_backend.DemoKitOperatorBackend`
(driving `demo/demo.sh`). `surfaces/operator/app.py` depends only on this
protocol, never on a concrete backend.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from surfaces.operator.models import OperatorStatus, Project


class UnknownProjectError(Exception):
    """`project_id` is not one of `OperatorBackend.projects()`."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        super().__init__(f"unknown project id: {project_id}")


class AlreadyRunningError(Exception):
    """`start()` was called while a meeting is already running."""


class NotRunningError(Exception):
    """`stop()` was called while no meeting is running."""


class NotStoppedError(Exception):
    """`set_project()` was called while a meeting is running."""


class ParticipantsPresentError(Exception):
    """`stop()` was called without `force` while people are in the meeting."""

    def __init__(self, participant_count: int):
        self.participant_count = participant_count
        super().__init__(f"{participant_count} participant(s) still in the meeting")


@runtime_checkable
class OperatorBackend(Protocol):
    def status(self) -> OperatorStatus: ...

    def projects(self) -> list[Project]: ...

    def start(self, project_id: str, title: str) -> OperatorStatus: ...

    def stop(self, confirm: bool, force: bool = False) -> OperatorStatus: ...

    def set_project(self, project_id: str) -> OperatorStatus: ...

    async def run(self) -> None:
        """A background task the app's lifespan runs for the backend's own
        upkeep (e.g. a fixture's scripted timeline); a no-op for a backend
        that needs none."""
        ...
