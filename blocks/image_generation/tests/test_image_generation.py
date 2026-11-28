# CoThinker — tests for blocks/image_generation (issue #97): the
# image-generation block driven by the block host.
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_image_generation.py

The backend is faked throughout: no test here makes a network call. The
success case plays back a golden fixture (`tests/golden/image_generation/`)
recorded once, not regenerated per run.
"""
from __future__ import annotations

import json
from pathlib import Path

from blocks.image_generation import ImageGenerationBlock
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, StateStore

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "image_generation"


def load_golden(case: str) -> tuple[dict, dict, bytes]:
    request = json.loads((GOLDEN / f"{case}.input.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLDEN / f"{case}.expected.json").read_text(encoding="utf-8"))
    png = (GOLDEN / f"{case}.png").read_bytes()
    return request, expected, png


def make_block(tmp_path: Path, config: dict, render):
    block = ImageGenerationBlock(render=render)
    artefacts: dict[str, bytes] = {}
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="de", clock=lambda: 0.0, emit=lambda intent: None,
        artefact=lambda name, text: None,
        artefact_bytes=lambda name, data: artefacts.__setitem__(name, data))
    block.start(ctx, config)
    return block, artefacts


def dispatch_request(block, request: dict):
    return block.on_event(CoreEvent("block_output", {"image_request": request}))


def test_request_produces_artefact_matches_golden(tmp_path):
    request, expected, png = load_golden("poster")

    def render(provider, model, prompt, width, height):
        assert provider == "test-provider"
        assert model == "test-model"
        return png

    block, artefacts = make_block(
        tmp_path, {"provider": "test-provider", "model": "test-model"}, render)
    out = dispatch_request(block, request)

    assert len(out) == 1
    assert out[0].kind == "block_output"
    assert out[0].payload == {"image": expected}
    assert artefacts == {expected["artefact"]: png}


def test_unconfigured_provider_reports_not_configured(tmp_path):
    request, _expected, _png = load_golden("poster")

    def render(provider, model, prompt, width, height):
        raise AssertionError("no backend call without a configured provider")

    block, artefacts = make_block(tmp_path, {}, render)
    out = dispatch_request(block, request)

    assert len(out) == 1
    assert out[0].payload == {"image": {"ref": request["ref"], "error": "not_configured"}}
    assert artefacts == {}


def test_backend_error_is_reported_not_raised(tmp_path):
    request, _expected, _png = load_golden("poster")

    def render(provider, model, prompt, width, height):
        raise RuntimeError("backend refused the request")

    block, artefacts = make_block(
        tmp_path, {"provider": "test-provider", "model": "test-model"}, render)
    out = dispatch_request(block, request)

    assert len(out) == 1
    assert out[0].payload == {
        "image": {"ref": request["ref"], "error": "backend refused the request"}}
    assert artefacts == {}
