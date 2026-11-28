# CoThinker — blocks/choice_list/: the choice-list block (issue #86, plan
# task E.2.18)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `choice-list`: a generic tap list that lets people accept,
reject or edit each item of an offered list, counts them, and reports the
result when the list is done.

Another block offers a list through `block_output.offer_choices`; the agent
offers one through the `offer_choices` tool -- both paths build the same
single-group list and go through `_offer`. A later offer of the same
`list_id` keeps every item's current state (matched by item id): re-running
a discussion's question list must not throw away what was already decided
on the still-open items.

An "edited" item counts as kept on `choose done`: editing refines the text
of an item people still want, it does not withdraw it -- so `choices_done`'s
`accepted` list carries both plain-accepted and edited item ids, and
`edited` on top carries the ones whose text changed.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, ToolSpec
from shared.surface import ViewIntent

from .views import build_view

BLOCK_ID = "choice-list"
STATE_FILE = "choice-lists.json"


class ChoiceListBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: BlockContext | None = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "block_output":
            offer = ev.payload.get("offer_choices")
            if offer is not None:
                self._offer(offer)
            return ()
        if ev.kind == "action":
            return self._on_action(ev.payload)
        return ()

    def stop(self) -> None:
        pass

    def tools(self) -> Sequence[ToolSpec]:
        return (
            ToolSpec(
                name="offer_choices",
                description="Offer a list of items for the room to accept, reject or edit.",
                parameters={
                    "type": "object",
                    "properties": {
                        "list_id": {"type": "string"},
                        "title": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["list_id", "title", "items"],
                },
                fn=self._tool_offer,
            ),
            ToolSpec(
                name="choice_status",
                description="The current rows, states and counter of an offered list.",
                parameters={
                    "type": "object",
                    "properties": {"list_id": {"type": "string"}},
                    "required": ["list_id"],
                },
                fn=self._tool_status,
                read_only=True,
            ),
        )

    # -- offer_choices ----------------------------------------------------
    def _offer(self, offer: Mapping) -> None:
        list_id = offer["list_id"]
        previous = self._ctx.state.read(STATE_FILE).get(list_id, {}).get("items", {})
        items: dict = {}
        groups = []
        for group in offer["groups"]:
            ids = []
            for item in group["items"]:
                item_id = item["id"]
                ids.append(item_id)
                kept = previous.get(item_id)
                items[item_id] = {
                    "text": item["text"],
                    "state": kept["state"] if kept else "open",
                    "edited_text": kept.get("edited_text") if kept else None,
                }
            groups.append({"heading": group["heading"], "items": ids})
        data = {
            "title": offer["title"],
            "groups": groups,
            "items": items,
            "allow_edit": offer.get("allow_edit", True),
            "done_label": offer.get("done_label", "Done"),
        }
        self._ctx.state.update(STATE_FILE, lambda cur, list_id=list_id, data=data: {**cur, list_id: data})
        self._emit(list_id, data)

    def _tool_offer(self, list_id: str, title: str, items: Sequence[Mapping]) -> dict:
        self._offer({"list_id": list_id, "title": title,
                     "groups": [{"heading": title, "items": list(items)}]})
        data = self._ctx.state.read(STATE_FILE)[list_id]
        return {"ok": True, "data": build_view(list_id, data)}

    def _tool_status(self, list_id: str) -> dict:
        data = self._ctx.state.read(STATE_FILE).get(list_id)
        if data is None:
            return {"ok": False, "error": f"unknown_list: {list_id}"}
        return {"ok": True, "data": build_view(list_id, data)}

    def _emit(self, list_id: str, data: Mapping) -> None:
        self._ctx.emit(ViewIntent(
            id=f"choices:{list_id}", block=self.id, kind="choice_list",
            audience="room", payload=build_view(list_id, data)))

    # -- actions ------------------------------------------------------------
    def _on_action(self, payload: Mapping) -> Sequence[CoreEvent]:
        intent_id = payload.get("intent_id") or ""
        if not intent_id.startswith("choices:"):
            return ()
        list_id = intent_id[len("choices:"):]
        lists = self._ctx.state.read(STATE_FILE)
        data = lists.get(list_id)
        if data is None:
            return ()
        kind = payload.get("kind")
        action_payload = payload.get("payload") or {}
        item_id = action_payload.get("item")
        if kind in ("accept", "reject") and item_id in data["items"]:
            data["items"][item_id]["state"] = "accepted" if kind == "accept" else "rejected"
        elif kind == "edit" and item_id in data["items"]:
            data["items"][item_id]["state"] = "edited"
            data["items"][item_id]["edited_text"] = action_payload.get("text")
        elif kind == "choose" and action_payload.get("done"):
            self._ctx.state.update(STATE_FILE, lambda cur, list_id=list_id, data=data: {**cur, list_id: data})
            return self._done(list_id, data)
        else:
            return ()
        self._ctx.state.update(STATE_FILE, lambda cur, list_id=list_id, data=data: {**cur, list_id: data})
        self._emit(list_id, data)
        return ()

    def _done(self, list_id: str, data: Mapping) -> Sequence[CoreEvent]:
        accepted = [item_id for item_id, item in data["items"].items()
                    if item["state"] in ("accepted", "edited")]
        edited = {item_id: item["edited_text"] for item_id, item in data["items"].items()
                  if item["state"] == "edited"}
        return (CoreEvent("block_output", {"choices_done": {
            "list_id": list_id, "accepted": accepted, "edited": edited}}),)


def make_block() -> ChoiceListBlock:
    return ChoiceListBlock()
