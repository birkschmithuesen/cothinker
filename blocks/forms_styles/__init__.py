# CoThinker — blocks/forms_styles: the forms & styles block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The form catalogue (theatre forms, optional music structures), the scene
forms (dialogue, monologue, chorus, song, rap) and the writing styles, as
read tools for the agent (`find_forms`, `form_info`, `style_info`) plus one
write tool, `set_scene_form`, that records a scene's form/style choice in
this block's own session state -- never the catalogue, which is read-only
content shipped with the block.

Writing scene text in a chosen form is a separate, later block
(`write-text`, see the issue tracker); this block only catalogues forms and
styles and tracks which one a scene currently uses.

Setting a scene's form or style marks that scene's text `stale`: a
structure change never silently rewrites prose that was written for the
old structure (global rule -- prose is only ever rewritten on request).

Config: `music_forms` (default `False`) -- an instance opts a group into
the music-structure catalogue; disabled, `formen_musik`'s entries neither
match nor resolve.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Sequence

from blocks.forms_styles import catalogue
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, ToolSpec

BLOCK_ID = "forms-styles"

_HERE = Path(__file__).resolve().parent
CATALOGUE_DIR = _HERE / "catalogue"
PROMPTS_DIR = _HERE / "prompts"
THEATRE_DIR = CATALOGUE_DIR / "theatre"
MUSIC_DIR = CATALOGUE_DIR / "music"
SCENE_FORMS_FILE = CATALOGUE_DIR / "scene_forms.toml"
STYLES_FILE = CATALOGUE_DIR / "styles.toml"
STYLE_PROMPTS_DIR = PROMPTS_DIR / "styles"
SCENE_FORM_PROMPTS_DIR = PROMPTS_DIR / "scene_forms"

_FIND_FORMS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["query"],
    "properties": {"query": {"type": "string"}},
}
_FORM_INFO_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["slug"],
    "properties": {"slug": {"type": "string"}},
}
_STYLE_INFO_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["slug"],
    "properties": {"slug": {"type": "string"}},
}
_SET_SCENE_FORM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["scene"],
    "properties": {
        "scene": {"type": "string"},
        "form": {"type": "string"},
        "style": {"type": "string"},
    },
}


class FormsStylesBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._theatre_forms = catalogue.load_forms(THEATRE_DIR)
        self._scene_forms, self.default_scene_form = catalogue.load_scene_forms(SCENE_FORMS_FILE)
        self._styles = catalogue.load_styles(STYLES_FILE)
        self._music_forms: dict[str, catalogue.Form] = {}

    # -- block contract ------------------------------------------------
    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        if config and config.get("music_forms", False):
            self._music_forms = catalogue.load_forms(MUSIC_DIR)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        return ()

    def stop(self) -> None:
        pass

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name="find_forms", read_only=True,
                     description="Finds performative forms (theatre, and music structures "
                                 "where enabled) whose name or keywords match a query. "
                                 f"Returns at most {catalogue.MAX_RESULTS} hits.",
                     parameters=_FIND_FORMS_SCHEMA, fn=self._find_forms),
            ToolSpec(name="form_info", read_only=True,
                     description="The full description of one form from `find_forms`.",
                     parameters=_FORM_INFO_SCHEMA, fn=self._form_info),
            ToolSpec(name="style_info", read_only=True,
                     description="The full description and rule block of one writing style.",
                     parameters=_STYLE_INFO_SCHEMA, fn=self._style_info),
            ToolSpec(name="set_scene_form", read_only=False,
                     description="Sets a scene's form and/or style. Marks the scene's text "
                                 "stale: it is never rewritten silently.",
                     parameters=_SET_SCENE_FORM_SCHEMA, fn=self._set_scene_form),
        ]

    # -- the forms a session knows about --------------------------------
    def all_forms(self) -> dict[str, catalogue.Form]:
        merged = dict(self._theatre_forms)
        merged.update(self._music_forms)
        return merged

    def scene_forms(self) -> Mapping[str, catalogue.SceneForm]:
        return self._scene_forms

    def styles(self) -> Mapping[str, catalogue.Style]:
        return self._styles

    # -- tool implementations -------------------------------------------
    def _find_forms(self, query: str) -> dict:
        forms = self.all_forms()
        slugs = catalogue.find_forms(forms, query)
        return {"data": {"forms": [{"slug": s, "name": forms[s].name} for s in slugs]}}

    def _form_info(self, slug: str) -> dict:
        form = self.all_forms().get(slug)
        if form is None:
            return {"ok": False, "error": f"unknown_form: {slug}"}
        return {"data": {"slug": form.slug, "name": form.name,
                         "keywords": list(form.keywords), "info": form.info}}

    def _style_info(self, slug: str) -> dict:
        style = self._styles.get(slug)
        if style is None:
            return {"ok": False, "error": f"unknown_style: {slug}"}
        rule_block = catalogue.load_style_rule_block(STYLE_PROMPTS_DIR / f"{slug}.en.md")
        return {"data": {"slug": style.slug, "name": style.name,
                         "summary": style.summary, "rule_block": rule_block}}

    def _set_scene_form(self, scene: str, form: Optional[str] = None,
                        style: Optional[str] = None) -> dict:
        if self._ctx is None:
            return {"ok": False, "error": "block_not_started"}
        if form is None and style is None:
            return {"ok": False, "error": "bad_args: give form, style or both"}
        if form is not None and form not in self._scene_forms:
            return {"ok": False, "error": f"unknown_scene_form: {form}"}
        if style is not None and style not in self._styles:
            return {"ok": False, "error": f"unknown_style: {style}"}

        def apply(data: dict) -> dict:
            record = dict(data.get(scene, {}))
            if form is not None:
                record["form"] = form
            if style is not None:
                record["style"] = style
            record["stale"] = True
            return {**data, scene: record}

        data = self._ctx.state.update("scenes.json", apply)
        return {"data": {"scene": scene, **data[scene]}}


def make_block() -> FormsStylesBlock:
    return FormsStylesBlock()
