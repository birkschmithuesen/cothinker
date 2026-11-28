# CoThinker — blocks/piece_model: the InScribe piece, one per group (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `piece-model` block (long format, ADR 0011): one JSON piece per
group, kept in the group's project folder and written only through
`PieceStore` (lock, revision counter, history snapshots, undo as a new
revision, a busy flag while a text run writes).

This package carries the store and the read side only (issue #69): write
tools and the block's wiring into a session runtime are later issues.
"""
from __future__ import annotations
