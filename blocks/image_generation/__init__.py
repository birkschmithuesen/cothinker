# CoThinker — blocks/image_generation: the image-generation block (issue #97)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Turns an image request from another block into an image artefact,
through whatever image provider the instance configures.

`ImageGenerationBlock` consumes `block_output` events whose payload
carries an `image_request`:

    {"image_request": {"prompt": str, "size": {"width": int, "height": int},
                       "ref": str}}

Provider and model come from the block's own configuration
(`[blocks."image-generation"]` keys `provider`, `model`; `start()` reads
them from the `config` the host already merges from the template and the
instance -- the same path every other block gets its configuration
through). Without both, the block makes no backend call and emits
`block_output {"image": {"ref", "error": "not_configured"}}`. A
configured request renders through `render` (injected; `make_block()`'s
default always fails, since choosing a provider is a separate task) and
either emits `block_output {"image": {"ref", "artefact"}}` -- the artefact
name `ctx.artefact_bytes` wrote the PNG under -- or, on a backend failure,
`block_output {"image": {"ref", "error"}}`. A backend exception is always
reported this way, never raised.
"""
from __future__ import annotations

import re
from typing import Callable, Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent

DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024

Render = Callable[[str, str, str, int, int], bytes]  # (provider, model, prompt, width, height) -> PNG bytes

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _no_backend_wired(provider: str, model: str, prompt: str, width: int, height: int) -> bytes:
    """`make_block()`'s default `render`: choosing a provider is out of this
    task's scope, so there is nothing to call yet."""
    raise RuntimeError(f"no image backend wired for provider {provider!r}")


def _artefact_name(ref: object) -> str:
    base = _UNSAFE_NAME_CHARS.sub("_", str(ref)) if ref else "image"
    return f"{base}.png"


class ImageGenerationBlock:
    id = "image-generation"
    api_version = BLOCK_API_VERSION

    def __init__(self, render: Render = _no_backend_wired) -> None:
        self._render = render
        self._ctx: Optional[BlockContext] = None
        self._provider: Optional[str] = None
        self._model: Optional[str] = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._provider = config.get("provider") or None
        self._model = config.get("model") or None

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output":
            return ()
        request = ev.payload.get("image_request")
        if request is None:
            return ()
        ref = request.get("ref")
        if not self._provider or not self._model:
            return (CoreEvent("block_output", {"image": {"ref": ref, "error": "not_configured"}}),)
        prompt = str(request.get("prompt", ""))
        size = request.get("size") or {}
        width = int(size.get("width", DEFAULT_WIDTH))
        height = int(size.get("height", DEFAULT_HEIGHT))
        try:
            data = self._render(self._provider, self._model, prompt, width, height)
        except Exception as e:  # noqa: BLE001 -- a backend failure is reported, never raised
            return (CoreEvent("block_output", {"image": {"ref": ref, "error": str(e)}}),)
        name = _artefact_name(ref)
        self._ctx.artefact_bytes(name, data)
        return (CoreEvent("block_output", {"image": {"ref": ref, "artefact": name}}),)

    def stop(self) -> None:
        pass


def make_block() -> ImageGenerationBlock:
    return ImageGenerationBlock()
