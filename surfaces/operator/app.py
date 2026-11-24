"""The operator console's FastAPI app (issue #159, ADR 0019 style): status
snapshot, SSE push and the start/stop/project commands, under the
`/operator/` prefix with its own token -- a separate FastAPI app from
`surfaces.contract.app`'s participant/room surface, never sharing the app
or the token with it.

Out of scope here: moving the real deployment to this app (a later, human/
ops step); this module only serves the contract over an `OperatorBackend`
(ADR 0019 point 2's fixture-first approach, applied to a second audience).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from surfaces.operator.auth import check_token
from surfaces.operator.backend import (
    AlreadyRunningError,
    NotRunningError,
    NotStoppedError,
    OperatorBackend,
    ParticipantsPresentError,
    UnknownProjectError,
)
from surfaces.operator.models import OperatorStatus, Project, ProjectRequest, StartRequest, StopRequest

SSE_PUSH_INTERVAL_S_DEFAULT = 1.0


def create_app(
    backend: OperatorBackend,
    token: str,
    cors_origins: list[str] | None = None,
    push_interval_s: float = SSE_PUSH_INTERVAL_S_DEFAULT,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(backend.run())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(
        title="CoThinker operator console",
        description="The operator console contract (ADR 0019 style, issue"
        " #159): the status snapshot, its SSE push channel and the"
        " start/stop/project commands. Audience: operator -- a separate"
        " token from the participant/room surface, never shared with it.",
        version="0.1.0",
        lifespan=lifespan,
    )

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def require_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        candidate = header[len("Bearer "):] if header.startswith("Bearer ") else None
        if not check_token(candidate, token):
            # Never echoes `candidate`: the detail names only the check that
            # failed, never the value that failed it.
            raise HTTPException(status_code=401, detail="missing or invalid operator token")

    router = APIRouter(prefix="/operator", dependencies=[Depends(require_auth)])

    def _status_dict() -> dict:
        return backend.status().model_dump(mode="json")

    @router.get("/status", response_model=OperatorStatus, tags=["status"])
    def get_status() -> OperatorStatus:
        return backend.status()

    @router.get("/projects", response_model=list[Project], tags=["projects"])
    def get_projects() -> list[Project]:
        return backend.projects()

    @router.post("/start", status_code=202, tags=["commands"])
    def post_start(req: StartRequest) -> dict:
        try:
            status = backend.start(req.project_id, req.title)
        except UnknownProjectError as exc:
            return JSONResponse(status_code=400, content={
                "detail": f"unknown project id: {exc.project_id}",
                "status": _status_dict(),
            })
        except AlreadyRunningError:
            return JSONResponse(status_code=409, content={
                "detail": "already running",
                "status": _status_dict(),
            })
        return {"status": status.model_dump(mode="json")}

    @router.post("/stop", status_code=202, tags=["commands"])
    def post_stop(req: StopRequest) -> dict:
        try:
            status = backend.stop(confirm=req.confirm, force=req.force)
        except ParticipantsPresentError as exc:
            return JSONResponse(status_code=409, content={
                "detail": "participants still in the meeting",
                "participant_count": exc.participant_count,
                "status": _status_dict(),
            })
        except NotRunningError:
            return JSONResponse(status_code=409, content={
                "detail": "not running",
                "status": _status_dict(),
            })
        except ValueError as exc:
            return JSONResponse(status_code=400, content={
                "detail": str(exc),
                "status": _status_dict(),
            })
        return {"status": status.model_dump(mode="json")}

    @router.post("/project", status_code=202, tags=["commands"])
    def post_project(req: ProjectRequest) -> dict:
        try:
            status = backend.set_project(req.project_id)
        except UnknownProjectError as exc:
            return JSONResponse(status_code=400, content={
                "detail": f"unknown project id: {exc.project_id}",
                "status": _status_dict(),
            })
        except NotStoppedError:
            return JSONResponse(status_code=409, content={
                "detail": "running",
                "status": _status_dict(),
            })
        return {"status": status.model_dump(mode="json")}

    @router.get("/events", tags=["events"])
    async def get_events(request: Request) -> StreamingResponse:
        """`operator-status`: the full snapshot, pushed on change, at most
        once every `push_interval_s` (default 1s, issue #159's "at most
        1/s")."""

        async def stream():
            last: OperatorStatus | None = None
            while True:
                if await request.is_disconnected():
                    return
                current = backend.status()
                if current != last:
                    last = current
                    yield f"event: operator-status\ndata: {current.model_dump_json()}\n\n"
                await asyncio.sleep(push_interval_s)

        return StreamingResponse(stream(), media_type="text/event-stream")

    app.include_router(router)
    return app
