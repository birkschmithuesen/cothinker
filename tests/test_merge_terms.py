# CoThinker — tests for blocks/merge_terms (issue #95): the merge-terms
# block.
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_merge_terms.py"""
from __future__ import annotations

import json
from pathlib import Path

from blocks.merge_terms import MergeTermsBlock, term_merge
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore

GOLDEN = Path(__file__).resolve().parent / "golden" / "merge_terms"


def make_fake_embed(vectors_by_form):
    table = {term_merge.embed_text(form): vec for form, vec in vectors_by_form.items()}

    def embed(text):
        return table[text]
    return embed


def make_block(tmp_path, embed=None, traces=None):
    traces = [] if traces is None else traces
    block = MergeTermsBlock(embed_client_factory=lambda llm: embed)
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="en", clock=lambda: 0.0, emit=lambda intent: None,
        artefact=lambda name, text: None,
        trace=lambda event, fields: traces.append((event, fields)))
    block.start(ctx, {})
    return block, traces


def dispatch_terms(block, terms):
    out = block.on_event(CoreEvent("block_output", {"terms": terms}))
    assert len(out) == 1
    assert out[0].kind == "block_output"
    return out[0].payload["merged_terms"]


def test_merge_matches_golden(tmp_path):
    fixture = json.loads((GOLDEN / "case1.input.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLDEN / "case1.expected.json").read_text(encoding="utf-8"))
    embed = make_fake_embed(fixture["embeddings"])
    block, _traces = make_block(tmp_path, embed=embed)
    merged = dispatch_terms(block, fixture["terms"])
    assert merged == expected["merged_terms"]


def test_no_merge_below_threshold(tmp_path):
    embed = make_fake_embed({"alpha": [1.0, 0.0], "zeta": [0.0, 1.0]})
    block, _traces = make_block(tmp_path, embed=embed)
    merged = dispatch_terms(block, [
        {"form": "alpha", "mentions": 1},
        {"form": "zeta", "mentions": 1},
    ])
    assert len(merged) == 2
    assert {m["label"] for m in merged} == {"alpha", "zeta"}
    assert all(m["members"] == [m["label"]] for m in merged)


def test_missing_embedding_client_skips_with_log(tmp_path):
    block, traces = make_block(tmp_path, embed=None)
    merged = dispatch_terms(block, [
        {"form": "widget", "mentions": 1},
        {"form": "widgets", "mentions": 2},
        {"form": "gadget", "mentions": 1},
    ])
    # The free alias-key stage still merges "widget"/"widgets" without any
    # embedding client; "gadget" has no alias match and, with no client to
    # embed it, ends up in a group of its own rather than being dropped.
    by_label = {m["label"]: m for m in merged}
    assert by_label["widgets"]["members"] == ["widget", "widgets"]
    assert by_label["widgets"]["mentions"] == 3
    assert by_label["gadget"]["members"] == ["gadget"]
    assert [event for event, _fields in traces] == ["merge_terms_no_embed_client"]
