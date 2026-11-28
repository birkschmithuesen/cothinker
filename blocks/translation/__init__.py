# CoThinker — blocks/translation: scene translation and the operator-view
# translation cache
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A group that works bilingually needs the agent to know that another
language's version of a scene exists, and to be able to produce it
(issue #80). The block exposes exactly one tool, `translate_scene(scene,
to)`: `scene` is `{"id", "text"}`, `to` the target language. The result is
always stored as a NEW version (`blocks.translation.scene.record_version`,
`source = "translation"`), never over a version the group wrote by hand
(`source = "group"`).

Config: `quote_translation` (default False -- a `"..."` quote in the scene
text stays as the group wrote it; True lets the model translate it too)
and `operator_language` (the target language of the operator-view
translation cache, `blocks.translation.ui`).

A per-group "the second language becomes available from phase N" switch
is instance content, not hardcoded block behaviour (issue #80): the
instance's `[blocks.translation] second_language_from_phase` config is
read into `self._second_language_from_phase` for whatever reads the
block's own config the same way `shared.instance.InstanceConfig.
block_config` reads any other block's; the block itself does not gate
`translate_scene` on it -- the decision of when to ask for the second
language stays with the agent and the instance's own content.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from blocks.translation.scene import record_version, translate_scene_text
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, ToolSpec

BLOCK_ID = "translation"

DEFAULT_CONFIG: Mapping[str, object] = {
    "quote_translation": False,
    "operator_language": "en",
    "second_language_from_phase": "",
}


def resolve_config(config: Optional[Mapping[str, object]]) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update(config or {})
    return out


class TranslationBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._config = resolve_config(None)
        self._quote_translation = False
        self._operator_language = "en"
        self._second_language_from_phase = ""

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        cfg = self._config = resolve_config(config)
        self._quote_translation = bool(cfg["quote_translation"])
        self._operator_language = str(cfg["operator_language"])
        self._second_language_from_phase = str(cfg.get("second_language_from_phase") or "")
        if ctx.instance is not None:
            self._second_language_from_phase = str(
                ctx.instance.block_config(self.id).get(
                    "second_language_from_phase", self._second_language_from_phase))

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        return ()

    def stop(self) -> None:
        self._ctx = None

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            "translate_scene",
            "Translates a scene's text into another of the group's "
            "languages. Stores the result as a new version; never "
            "overwrites a version the group wrote by hand.",
            {"type": "object",
             "properties": {"scene": {"type": "object"}, "to": {"type": "string"}},
             "required": ["scene", "to"]},
            self._translate_scene)]

    def _translate_scene(self, scene: Mapping[str, object], to: str) -> dict:
        ctx = self._ctx
        scene_id = str(scene["id"])
        text = str(scene["text"])
        translated = translate_scene_text(
            text, to, ctx.llm, quote_translation=self._quote_translation,
            language=ctx.language)
        rev = record_version(ctx.state, scene_id, to, translated, source="translation")
        return {"ok": True, "warnings": [], "error": None, "rev": rev,
                "data": {"translated": {"scene_id": scene_id, "language": to,
                                        "text": translated, "rev": rev}}}


def make_block() -> TranslationBlock:
    return TranslationBlock()
