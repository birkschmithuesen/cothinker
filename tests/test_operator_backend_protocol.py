"""`OperatorBackend` protocol and its typed exceptions (issue #159): both
the fixture backend and the demo-kit backend implement this protocol, and
`surfaces/operator/app.py` only ever catches these exception types to
turn them into the acceptance list's HTTP statuses (400/409).
"""
from __future__ import annotations

from surfaces.operator.backend import (
    AlreadyRunningError,
    NotRunningError,
    NotStoppedError,
    OperatorBackend,
    ParticipantsPresentError,
    UnknownProjectError,
)


def test_unknown_project_error_carries_the_rejected_id():
    exc = UnknownProjectError("not-a-real-project")
    assert exc.project_id == "not-a-real-project"


def test_participants_present_error_carries_the_count():
    exc = ParticipantsPresentError(3)
    assert exc.participant_count == 3


def test_a_fully_implemented_backend_satisfies_the_protocol():
    class _Minimal:
        def status(self):
            raise NotImplementedError

        def projects(self):
            raise NotImplementedError

        def start(self, project_id, title):
            raise NotImplementedError

        def stop(self, confirm, force=False):
            raise NotImplementedError

        def set_project(self, project_id):
            raise NotImplementedError

        async def run(self):
            raise NotImplementedError

    assert isinstance(_Minimal(), OperatorBackend)


def test_an_incomplete_backend_does_not_satisfy_the_protocol():
    class _Incomplete:
        def status(self):
            raise NotImplementedError

    assert not isinstance(_Incomplete(), OperatorBackend)


def test_distinct_exception_types_for_each_conflict():
    assert issubclass(AlreadyRunningError, Exception)
    assert issubclass(NotRunningError, Exception)
    assert issubclass(NotStoppedError, Exception)
