# CoThinker — blocks/meeting_chat: participants give the agent tasks by text
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `meeting-chat` (issue #208, ADR 0027): the meeting chat
thread. Participants type a task for the agent; the agent's answer comes
back into the same thread.

Consumes `action` events (a `UserAction` the host turned into a
`CoreEvent`, its fields flat in the payload): `chat:room` (`kind`
`message`, `payload` `{"text"}`) is a chat message by `person`;
`participant:name` (`kind` `edit`, `payload` `{"name"}`) records a display
name for `person`. Consumes `block_output` events from `session-host`
shaped `{"chat_answer": {"ids", "ok", "text", "reason"}}`: the host's
answer to one or more chat requests.

Emits the view intent `chat:room` (kind `chat`, audience `room`), payload
`{"messages": [...]}` (the contract's `ChatSnapshot`), the last
CHAT_THREAD_MAX messages. Returns two `block_output` events per new
message, in this order: `chat_record {"speaker", "text"}` (the line for the
session record) and `chat_request {"id", "name", "text"}` (the order for
the agent); one `chat_record` per agent answer.

The action spool is replayed from its start after a consumer restart
(`gateway/actions_reader.py`). A message id is derived from the spool
line (`<person>-<at in ms>`) and every handled id is kept in block state
(`chat.json`), so a replayed message is neither asked nor recorded twice.
A message still `pending` when the block starts lost its agent job with
the old process: it becomes `failed`. Message text is never logged.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

__all__ = ["MeetingChatBlock", "BLOCK_ID", "INTENT_ID", "CHAT_THREAD_MAX",
           "CHAT_ANSWER_MAX_CHARS", "AGENT_SPEAKER", "GUEST_NAME"]

BLOCK_ID = "meeting-chat"
INTENT_ID = "chat:room"
NAME_INTENT_ID = "participant:name"
HOST_ID = "session-host"
STATE_FILE = "chat.json"
# Pinned equal to `surfaces.contract.models.CHAT_THREAD_MAX` by this block's
# tests (a block does not import a surface package).
CHAT_THREAD_MAX = 50
CHAT_ANSWER_MAX_CHARS = 2000
NAME_MAX_CHARS = 40
# Speaker labels of the session record (`## Rohtranskript`).
AGENT_SPEAKER = "Robo (Chat)"
GUEST_NAME = "Guest"


def _label(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _capped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MeetingChatBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._lock = threading.RLock()
        self._messages: list[dict] = []
        self._handled: set[str] = set()
        self._names: dict[str, str] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        try:
            data = ctx.state.read(STATE_FILE)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        messages = data.get("messages")
        handled = data.get("handled")
        with self._lock:
            self._messages = ([dict(m) for m in messages if isinstance(m, dict)]
                              if isinstance(messages, list) else [])
            self._handled = ({str(h) for h in handled}
                             if isinstance(handled, list) else set())
            died = [m for m in self._messages if m.get("status") == "pending"]
            for m in died:
                m["status"] = "failed"
            if died:
                self._save()
            if self._messages:
                self._emit()

    def stop(self) -> None:
        pass

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        p = ev.payload if isinstance(ev.payload, Mapping) else {}
        if ev.kind == "action":
            if p.get("intent_id") == NAME_INTENT_ID and p.get("kind") == "edit":
                self._on_name(p)
            elif p.get("intent_id") == INTENT_ID and p.get("kind") == "message":
                return self._on_message(p)
            return ()
        if (ev.kind == "block_output" and p.get("from") == HOST_ID
                and isinstance(p.get("chat_answer"), Mapping)):
            return self._on_answer(p["chat_answer"])
        return ()

    # ------------------------------------------------------------- actions
    def _on_name(self, p: Mapping) -> None:
        person = str(p.get("person") or "")
        inner = p.get("payload") if isinstance(p.get("payload"), Mapping) else {}
        # `*` would break the emphasis of `*<speaker>:*` in the record.
        raw = str(inner.get("name") or "").replace("*", "")
        name = " ".join(raw.split())[:NAME_MAX_CHARS]
        agent_name = AGENT_SPEAKER.partition(" (")[0]
        if person and name and name.casefold() != agent_name.casefold():
            with self._lock:
                self._names[person] = name

    def _on_message(self, p: Mapping) -> Sequence[CoreEvent]:
        person = str(p.get("person") or "")
        inner = p.get("payload") if isinstance(p.get("payload"), Mapping) else {}
        text = str(inner.get("text") or "").strip()
        at = p.get("at")
        if not person or not text or not isinstance(at, (int, float)):
            return ()
        message_id = f"{person}-{int(round(at * 1000))}"
        with self._lock:
            if message_id in self._handled:
                return ()
            self._handled.add(message_id)
            name = self._names.get(person, "")
            self._append({"id": message_id, "role": "participant", "name": name,
                          "text": text, "at": _label(at), "reply_to": None,
                          "status": "pending"})
            self._save()
            self._emit()
        return (
            CoreEvent("block_output", {"from": BLOCK_ID, "chat_record": {
                "speaker": f"{name or GUEST_NAME} (Chat)", "text": text}}),
            CoreEvent("block_output", {"from": BLOCK_ID, "chat_request": {
                "id": message_id, "name": name, "text": text}}),
        )

    def _on_answer(self, answer: Mapping) -> Sequence[CoreEvent]:
        ids = {str(i) for i in (answer.get("ids") or [])}
        text = str(answer.get("text") or "").strip()
        if answer.get("ok") and text:
            status = "answered"
        elif answer.get("reason") == "unavailable":
            status = "unavailable"
        else:
            status = "failed"
        with self._lock:
            targets = [m for m in self._messages
                       if m.get("role") == "participant" and m.get("id") in ids
                       and m.get("status") == "pending"]
            if not targets:
                return ()
            for m in targets:
                m["status"] = status
            out: list[CoreEvent] = []
            if status == "answered":
                self._append({"id": f"agent-{targets[-1]['id']}", "role": "agent",
                              "name": "", "text": _capped(text, CHAT_ANSWER_MAX_CHARS),
                              "at": _label(self._ctx.clock()),
                              "reply_to": targets[-1]["id"], "status": "answered"})
                out.append(CoreEvent("block_output", {"from": BLOCK_ID, "chat_record": {
                    "speaker": AGENT_SPEAKER, "text": text}}))
            self._save()
            self._emit()
        return out

    # ------------------------------------------------------------- helpers
    def _append(self, message: dict) -> None:
        self._messages.append(message)
        del self._messages[:-CHAT_THREAD_MAX]

    def _save(self) -> None:
        try:
            self._ctx.state.write(STATE_FILE, {"messages": self._messages,
                                               "handled": sorted(self._handled)})
        except OSError:
            pass    # the thread still shows; only a restart would forget it

    def _emit(self) -> None:
        self._ctx.emit(ViewIntent(
            id=INTENT_ID, block=self.id, kind="chat", audience="room",
            payload={"messages": [dict(m) for m in self._messages]}))
