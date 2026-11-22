# CoThinker — speaking-share block: wires talk_share / room_talk_time to the block contract
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_speaking_share.py"""
from __future__ import annotations

from pathlib import Path

from blocks.speaking_share import SpeakingShareBlock
from blocks.speaking_share.talk_share import ANONYMOUS
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def make_ctx(tmp_path, emitted):
    return BlockContext(instance=None, session_dir=Path(tmp_path),
                         state=StateStore(Path(tmp_path) / "speaking-share"), agent=None, llm=None,
                         language="de", clock=lambda: 0.0,
                         emit=emitted.append, artefact=lambda name, text: None)


def test_shares_sum_to_one(tmp_path):
    block = SpeakingShareBlock()
    emitted = []
    block.start(make_ctx(tmp_path, emitted), {})
    block.on_event(CoreEvent("utterance", {"speaker": "S1", "text": "eins zwei drei"}))
    block.on_event(CoreEvent("utterance", {"speaker": "S2", "text": "vier fuenf"}))
    block.on_event(CoreEvent("tick", {"now": 10.0}))
    assert len(emitted) == 1
    intent = emitted[0]
    assert intent.id == "share:room" and intent.block == block.id
    assert intent.kind == "share" and intent.audience == "room"
    assert intent.payload["total_words"] > 0
    assert abs(sum(row["share"] for row in intent.payload["shares"]) - 1.0) < 1e-9


def test_unlabelled_speech_not_counted(tmp_path):
    """The ported TalkShareTracker counts an unmapped/unlabelled speaker
    under its own `Anonymous` row rather than dropping it (see
    blocks/speaking_share/talk_share.py's `ANONYMOUS` bucket); this test
    documents that real, ported behaviour instead of a "dropped" one the
    archive code never implemented."""
    block = SpeakingShareBlock()
    emitted = []
    block.start(make_ctx(tmp_path, emitted), {})
    block.on_event(CoreEvent("utterance", {"speaker": None, "text": "a b c"}))
    block.on_event(CoreEvent("tick", {"now": 10.0}))
    assert len(emitted) == 1
    by_person = {row["person"]: row["words"] for row in emitted[0].payload["shares"]}
    assert by_person.get(ANONYMOUS) == 3


def test_throttle_limits_emits(tmp_path):
    block = SpeakingShareBlock()
    emitted = []
    block.start(make_ctx(tmp_path, emitted), {"throttle_s": 5})
    block.on_event(CoreEvent("utterance", {"speaker": "S1", "text": "hallo"}))
    block.on_event(CoreEvent("tick", {"now": 0.0}))
    assert len(emitted) == 1
    block.on_event(CoreEvent("tick", {"now": 2.0}))
    assert len(emitted) == 1
    block.on_event(CoreEvent("tick", {"now": 5.0}))
    assert len(emitted) == 2


def test_holder_change_counts_talk_time(tmp_path):
    block = SpeakingShareBlock()
    emitted = []
    block.start(make_ctx(tmp_path, emitted), {})
    block.on_event(CoreEvent("block_output", {"holder": {"before": None, "after": "P1", "at": 10.0}}))
    block.on_event(CoreEvent("block_output", {"holder": {"before": "P1", "after": "P2", "at": 25.0}}))
    assert block._room_talk.seconds("P1", 99.0) == 15.0
