"""The surface contract's FastAPI app (ADR 0019, ADR 0017: FastAPI for a
new network seam). Serves the snapshot endpoint, the SSE push channel for
view intents, a user-action endpoint and the interactive docs at `/docs`.

Out of scope here: moving the real stage server to FastAPI (issue #132).
This app only serves the contract -- its own fixture-mode state, not a
live gateway instance (ADR 0001: a module needs no instance material).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from surfaces.contract.models import StageStateSnapshot, UserAction
from surfaces.contract.player import ScenarioPlayer

SSE_PING_S = 15.0


def create_app(player: ScenarioPlayer | None = None, cors_origins: list[str] | None = None) -> FastAPI:
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
        return player.snapshot

    @app.post("/actions", status_code=202, tags=["actions"])
    def post_action(action: UserAction) -> dict:
        """A user action sent back by a surface (ADR 0006); accepted, not acted on."""
        return {"received": action.kind}

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
