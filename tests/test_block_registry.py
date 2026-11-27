# CoThinker — tests for shared/block_registry.py: block discovery by package
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_registry.py"""
from __future__ import annotations

import sys
import types

import pytest

from shared import block_registry
from shared.blocks import BlockError


def fake_package(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)


class B:
    api_version = 1

    def __init__(self, id_):
        self.id = id_


def test_load_block_imports_blocks_package_by_id(monkeypatch):
    fake_package(monkeypatch, "blocks.fake_one", make_block=lambda: B("fake-one"))
    assert block_registry.load_block("fake-one").id == "fake-one"


def test_missing_package_raises_block_error():
    with pytest.raises(BlockError, match="no block registered"):
        block_registry.load_block("does-not-exist-anywhere")


def test_package_without_make_block_raises(monkeypatch):
    fake_package(monkeypatch, "blocks.fake_two")
    with pytest.raises(BlockError, match="make_block"):
        block_registry.load_block("fake-two")


def test_id_mismatch_raises(monkeypatch):
    fake_package(monkeypatch, "blocks.fake_three", make_block=lambda: B("other"))
    with pytest.raises(BlockError, match="other"):
        block_registry.load_block("fake-three")


def test_every_reflect_block_package_has_make_block():
    """Every block the Reflect template names loads by id through its
    package's make_block() (issue #28). Strict: a package without
    make_block() must turn this red, not be skipped."""
    from pathlib import Path

    from shared.templates import load_template
    root = Path(__file__).resolve().parents[1]
    t = load_template(root / "templates" / "reflect" / "template.toml")
    ids = [ref.id for ref in t.blocks]
    assert ids
    for block_id in ids:
        assert block_registry.load_block(block_id).id == block_id
