# CoThinker — fake Speechmatics EU realtime server for tests
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A scripted stand-in for the Speechmatics EU realtime websocket, used
only by `tests/test_speechmatics_client.py`. No real network call and no
API key: `SpeechmaticsStream` connects to this instead.

Usage:

    async with FakeSpeechmaticsServer(script=[...]) as srv:
        stream = SpeechmaticsStream("key", "de", None, url=srv.url)
        await stream.start()
        ...
    srv.received  # every StartRecognition/EndOfStream message it got
"""
from __future__ import annotations

import json


class FakeSpeechmaticsServer:
    """One scripted connection.

    `script` is a list of server-to-client JSON messages (dicts), sent in
    order right after `RecognitionStarted`. `start_message` replaces
    `RecognitionStarted` itself -- set it to an `Error` message to test
    that `SpeechmaticsStream.start()` surfaces a connect-time failure.
    After the script is exhausted the handler keeps reading client
    messages (recording every one) and answers `EndOfStream` with
    `EndOfTranscript`, mirroring the real server's shutdown handshake.
    """

    def __init__(self, script: list[dict] | None = None,
                start_message: dict | None = None) -> None:
        self.script = list(script or [])
        self.start_message = start_message or {"message": "RecognitionStarted"}
        self.received: list = []
        self.authorization: str | None = None
        self._server = None

    async def __aenter__(self) -> "FakeSpeechmaticsServer":
        import websockets

        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"ws://127.0.0.1:{port}"

    async def _handle(self, ws) -> None:
        import websockets

        self.authorization = ws.request.headers.get("Authorization")
        start_raw = await ws.recv()
        self.received.append(json.loads(start_raw))
        await ws.send(json.dumps(self.start_message))
        if self.start_message.get("message") != "RecognitionStarted":
            return
        for msg in self.script:
            await ws.send(json.dumps(msg))
        while True:
            try:
                raw = await ws.recv()
            except websockets.ConnectionClosed:
                return
            if isinstance(raw, (bytes, bytearray)):
                self.received.append(bytes(raw))
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            self.received.append(obj)
            if obj.get("message") == "EndOfStream":
                await ws.send(json.dumps({"message": "EndOfTranscript"}))
                return
