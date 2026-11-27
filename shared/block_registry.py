#!/usr/bin/env python3
# CoThinker — shared/block_registry.py: block discovery by package name
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Block id `x-y` lives in package `blocks.x_y`, which exposes
`make_block() -> Block` (issue #28). No list to maintain: adding a block
is adding a package."""
from __future__ import annotations

import importlib
from typing import Callable, Mapping

from shared.blocks import Block, BlockError


def package_name(block_id: str) -> str:
    return "blocks." + block_id.replace("-", "_")


def load_block(block_id: str) -> Block:
    name = package_name(block_id)
    try:
        mod = importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name in (name, "blocks"):
            raise BlockError(f"no block registered for {block_id!r}") from e
        raise
    make = getattr(mod, "make_block", None)
    if make is None:
        raise BlockError(f"{name} has no make_block()")
    block = make()
    if block.id != block_id:
        raise BlockError(f"{name}.make_block() returned block {block.id!r}, expected {block_id!r}")
    return block


def load_from(factories: Mapping[str, Callable[[], Block]]) -> Callable[[str], Block]:
    """A `load` over explicit factories, for a host that builds only some
    blocks (tests, `gateway.consumer.open_minutes`). Unlike `load_block` it
    does not check the block's id: the caller chose the factory."""
    def load(block_id: str) -> Block:
        factory = factories.get(block_id)
        if factory is None:
            raise BlockError(f"no block registered for {block_id!r}")
        return factory()
    return load
