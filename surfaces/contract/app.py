"""The surface contract's FastAPI app (ADR 0019, ADR 0017: FastAPI for a
new network seam). Serves the snapshot endpoint, the SSE push channel for
view intents, a user-action endpoint and the interactive docs at `/docs`.

Out of scope here: moving the real stage server to FastAPI (issue #132).
This app only serves the contract -- its own fixture-mode state, not a
live gateway instance (ADR 0001: a module needs no instance material).
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from shared.actions_spool import append_action as spool_append_action
from surfaces.contract.live_player import LivePlayer
from surfaces.contract.models import (
    EditAction,
    MessageAction,
    StageStateSnapshot,
    TapAction,
    UserAction,
)
from surfaces.contract.player import ScenarioPlayer

SSE_PING_S = 15.0

# Live mode (issue #183): one write path for every surface, through
# `shared.actions_spool`. Cookie name and rate-limit gap match the
# stage's own `/action` (stage/stage.py) -- one person, one convention.
COOKIE_NAME = "cothinker_pid"
DEFAULT_SPOOL_DIR = "~/.local/state/cothinker/spool"
MIN_ACTION_GAP_S = 0.5
QUEUE_OPS = ("request", "done")
LISTEN_OPS = ("open", "close", "toggle")
LIVE_ACTION_OPS = {"queue:room": QUEUE_OPS, "listen:room": LISTEN_OPS}
# Meeting chat (issue #208): the one intent a `message` may answer, and the
# display-name edit the chat surface sends (the stage's own `/join` writes
# the same spool line, `stage/stage.py`).
CHAT_INTENT_ID = "chat:room"
NAME_INTENT_ID = "participant:name"
NAME_MAX_CHARS = 40
_PID_RE = re.compile(r"[0-9a-f]{16}")


def _default_spool_dir() -> Path:
    return Path(os.environ.get("COTHINKER_SPOOL_DIR", DEFAULT_SPOOL_DIR)).expanduser()


def _is_allowed_live_action(action: UserAction) -> bool:
    """The live-mode action allowlist (issue #183): a `queue:room` floor
    request or hand-off, a `listen:room` open/close/toggle of the call
    window (issue #180, ADR 0024), a meeting-chat message to `chat:room`
    with non-blank text and a non-blank one-line `participant:name` of at
    most NAME_MAX_CHARS (issue #208). Everything else is rejected with 422
    rather than silently spooled. The message length limit lives in the
    model (`MessageAction.text`), so it holds in fixture mode too."""
    if isinstance(action, MessageAction):
        return action.intent_id == CHAT_INTENT_ID and bool(action.text.strip())
    if isinstance(action, EditAction):
        name = action.text.strip()
        return (action.intent_id == NAME_INTENT_ID and bool(name)
                and len(name) <= NAME_MAX_CHARS and "\n" not in name)
    if not isinstance(action, TapAction):
        return False
    ops = LIVE_ACTION_OPS.get(action.intent_id)
    return ops is not None and (action.payload or {}).get("op") in ops


def _spool_payload(action: UserAction) -> dict:
    """The spool line's `payload` for an allowed action. A message's or a
    name's text goes in stripped; nothing the surface claims about the
    person is ever spooled (the person is the cookie pid)."""
    if isinstance(action, MessageAction):
        return {"text": action.text.strip()}
    if isinstance(action, EditAction):
        return {"name": action.text.strip()}
    return action.payload or {}


def create_app(player: ScenarioPlayer | None = None, cors_origins: list[str] | None = None,
               spool_dir: Path | None = None) -> FastAPI:
    spool_dir = Path(spool_dir) if spool_dir is not None else _default_spool_dir()
    _last_action: dict[str, float] = {}
    _action_lock = threading.Lock()

    def _rate_limited(pid: str) -> bool:
        """True if `pid` acted less than MIN_ACTION_GAP_S ago (else records it)."""
        now = time.monotonic()
        with _action_lock:
            last = _last_action.get(pid)
            if last is not None and now - last < MIN_ACTION_GAP_S:
                return True
            _last_action[pid] = now
        return False

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(player.run()) if player is not None else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(
        title="CoThinker surface contract",
        description="The machine-readable contract between a surface and "
        "the core (ADR 0019): stage-state snapshot, the view-intent push "
        "channel and user actions.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Same-origin by default; a caller opts a surface served from another
    # origin into CORS by passing its own origins (issue #147 point 5).
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/snapshot", response_model=StageStateSnapshot, tags=["snapshot"])
    def get_snapshot() -> StageStateSnapshot:
        """The current stage-state snapshot."""
        assert player is not None, "no fixture scenario loaded"
        if player.snapshot is None:
            # Live pointer mode (issue #161) before the consumer's session
            # resolves: say so plainly rather than crash-looping the
            # process (the 502 issue #161 reports) or serving a guess.
            raise HTTPException(status_code=503, detail="no live session yet")
        return player.snapshot

    @app.post("/actions", status_code=202, tags=["actions"])
    def post_action(action: UserAction, request: Request, response: Response) -> dict:
        """A user action sent back by a surface (ADR 0006).

        Fixture mode accepts it without acting. Live mode validates it
        against the action allowlist and appends it to the session's
        action spool -- the same file the stage's own `/action` writes
        (issue #183) -- under a per-person id this endpoint assigns via an
        HttpOnly cookie the first time it sees a request without one.
        """
        if not isinstance(player, LivePlayer):
            return {"received": action.kind, "mode": "fixture"}
        session_dir = player.session_dir
        if session_dir is None:
            raise HTTPException(status_code=503, detail="no live session yet")
        if not _is_allowed_live_action(action):
            raise HTTPException(status_code=422, detail="action not allowed in live mode")
        pid = request.cookies.get(COOKIE_NAME, "")
        if not _PID_RE.fullmatch(pid):
            pid = secrets.token_hex(8)
            response.set_cookie(COOKIE_NAME, pid, httponly=True, samesite="strict", path="/")
        if _rate_limited(pid):
            raise HTTPException(status_code=429, detail="slow down")
        spool_append_action(spool_dir, session_dir.name, pid,
                            action.intent_id, action.kind, _spool_payload(action))
        return {"received": action.kind, "mode": "live"}

    @app.get("/events", tags=["events"])
    async def get_events() -> StreamingResponse:
        """Server-sent events: one `view-intent` event per pushed intent."""
        assert player is not None, "no fixture scenario loaded"
        queue = player.subscribe()

        async def stream():
            try:
                while True:
                    try:
                        intent = await asyncio.wait_for(queue.get(), timeout=SSE_PING_S)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    payload = intent.model_dump_json()
                    yield f"event: view-intent\ndata: {payload}\n\n"
            finally:
                player.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
