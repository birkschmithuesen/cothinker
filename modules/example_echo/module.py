# CoThinker — example_echo: proof module for the module seam (repo split 5.3)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The whole module: count words. See `docs/module-format.md` for the contract
this class satisfies, and `workshop/hooks.py`'s `ModuleHook` for how a
`"block"` `CoreEvent` reaches `on_event` below."""
from __future__ import annotations

from pathlib import Path

from shared.modules import CoreEvent, ModuleContext, Phase, ToolSpec


class EchoModule:
    def __init__(self, *, name: str, api_version: int, module_dir: Path, profile_dir: Path) -> None:
        self.name = name
        self.api_version = api_version
        self._profile_dir = profile_dir

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(name="echo_count", description="Counts the words in a piece of text.",
                          parameters={"type": "object",
                                      "properties": {"text": {"type": "string"}},
                                      "required": ["text"]},
                          fn=self._echo_count)]

    def phases(self) -> list[Phase]:
        return []

    def profile_dir(self) -> Path:
        return self._profile_dir

    def on_event(self, ev: CoreEvent, ctx: ModuleContext) -> None:
        if ev.kind != "block":
            return
        text = ev.payload.get("text", "")
        ctx.state.write("echo.json", self._echo_count(text))

    @staticmethod
    def _echo_count(text: str) -> dict:
        return {"words": len(text.split())}
