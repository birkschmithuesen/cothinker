# CoThinker — blocks/translation/scene.py: scene text translation
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Translates a scene's text into another of the group's languages.

A quote (`"..."`) inside the scene text is, by default, a line the group
wrote verbatim and stays exactly as written; `quote_translation` (a block
config option, instance content supplies the value) turns that off and
lets the model translate quotes too.

A scene's translated versions are kept as an append-only history in the
block's `StateStore` (one JSON file per scene, `record_version`/
`versions_for`): a translation is always a NEW version with
`source = "translation"`; nothing already stored is ever overwritten, so a
version the group wrote by hand (`source = "group"`) stays exactly as the
group left it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Optional, Sequence

from shared.llm import LlmPort
from shared.modules import StateStore

_QUOTE_RE = re.compile(r'"([^"]*)"')
_PLACEHOLDER = "\x00Q{}\x00"
_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def mask_quotes(text: str) -> tuple[str, list[str]]:
    """Replaces every `"..."` span with a placeholder token, so the model
    never sees (and cannot touch) the group's verbatim quotes."""
    spans: list[str] = []

    def repl(m: re.Match) -> str:
        spans.append(m.group(0))
        return _PLACEHOLDER.format(len(spans) - 1)

    return _QUOTE_RE.sub(repl, text), spans


def unmask_quotes(text: str, spans: Sequence[str]) -> str:
    for i, original in enumerate(spans):
        text = text.replace(_PLACEHOLDER.format(i), original)
    return text


def load_scene_prompt(language: str, to: str, template_dir: Optional[Path] = None) -> str:
    base = Path(template_dir) if template_dir is not None else _PROMPT_DIR
    path = base / f"scene.{language}.md"
    if not path.exists():
        path = base / "scene.en.md"
    return path.read_text(encoding="utf-8").format(to=to)


def translate_scene_text(text: str, to: str, llm: LlmPort, *, quote_translation: bool = False,
                         language: str = "en", template_dir: Optional[Path] = None) -> str:
    """Translates `text` into `to`. With `quote_translation` False (the
    default), every `"..."` span is masked before the call and restored
    afterwards untouched; with it True, quotes go through translation like
    the rest of the text."""
    if quote_translation:
        masked, spans = text, []
    else:
        masked, spans = mask_quotes(text)
    system = load_scene_prompt(language, to, template_dir)
    raw = llm.complete(system, masked)
    return unmask_quotes(raw, spans) if spans else raw


def scene_file(scene_id: str) -> str:
    return f"scene-{scene_id}.json"


def record_version(state: StateStore, scene_id: str, language: str, text: str, *,
                   source: str) -> int:
    """Appends a new version for `scene_id`/`language`; never mutates or
    removes an existing one. Returns the new version's `rev`."""
    def add(data: dict) -> dict:
        versions = data.setdefault("versions", [])
        rev = len(versions) + 1
        versions.append({"rev": rev, "language": language, "text": text, "source": source})
        return data
    data = state.update(scene_file(scene_id), add)
    return data["versions"][-1]["rev"]


def versions_for(state: StateStore, scene_id: str, language: Optional[str] = None) -> list[dict]:
    data = state.read(scene_file(scene_id))
    versions = data.get("versions", [])
    if language is not None:
        versions = [v for v in versions if v["language"] == language]
    return list(versions)
