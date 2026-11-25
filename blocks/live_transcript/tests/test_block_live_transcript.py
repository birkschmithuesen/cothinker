# CoThinker — tests of blocks/live_transcript (the LiveTranscriptBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests: feeds `utterance`/`partial` CoreEvents, same
shapes `gateway/block_host_wiring.py::feed_utterance` and the new
`partial` host event carry, and checks the `transcript:recent` /
`transcript:partial` view intents it emits (issue #181).

    python3 -m pytest -q blocks/live_transcript/tests/test_block_live_transcript.py
"""
from __future__ import annotations

from pathlib import Path

from blocks.live_transcript import LiveTranscriptBlock, _wall_clock_label
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, emitted, clock=lambda: 0.0):
    return BlockContext(instance=None, session_dir=Path(tmp_path),
                        state=StateStore(Path(tmp_path) / "live-transcript"),
                        agent=None, llm=None, language="en", clock=clock,
                        emit=emitted.append, artefact=lambda name, text: None)


def _new_block(tmp_path, config=None, clock=lambda: 0.0):
    emitted = []
    block = LiveTranscriptBlock()
    block.start(_ctx(tmp_path, emitted, clock), config or {})
    return block, emitted


def _utterance(speaker, text, turn_id, at):
    return CoreEvent("utterance", {"text": text, "speaker": speaker,
                                   "turn_id": turn_id, "at": at})


def _partial(turn_id, speaker, text, at):
    return CoreEvent("partial", {"turn_id": turn_id, "speaker": speaker,
                                 "text": text, "at": at})


def _recent_intents(emitted):
    return [i for i in emitted if i.id == "transcript:recent"]


def _partial_intents(emitted):
    return [i for i in emitted if i.id == "transcript:partial"]


def test_a_final_emits_transcript_recent_with_the_new_line(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", "hallo", "T1", at=10.0))
    recent = _recent_intents(emitted)
    assert len(recent) == 1
    assert recent[-1].kind == "transcript" and recent[-1].audience == "room"
    assert recent[-1].payload == [
        {"speaker": "Mira", "started_at": _wall_clock_label(10.0), "text": "hallo"},
    ]


def test_recent_stays_capped_at_ten_oldest_first(tmp_path):
    block, emitted = _new_block(tmp_path)
    for i in range(12):
        block.on_event(_utterance("Mira", f"line {i}", f"T{i}", at=float(i)))
    payload = _recent_intents(emitted)[-1].payload
    assert len(payload) == 10
    assert payload[0]["text"] == "line 2"
    assert payload[-1]["text"] == "line 11"


def test_a_final_with_empty_text_emits_nothing(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", "   ", "T1", at=10.0))
    assert emitted == []


def test_partials_build_up_the_in_progress_line(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_partial("T1", "Mira", "hal", at=5.0))
    intent = _partial_intents(emitted)[-1]
    assert intent.kind == "transcript_partial" and intent.audience == "room"
    assert intent.payload == {
        "turn_id": "T1", "speaker": "Mira", "text": "hal",
        "started_at": _wall_clock_label(5.0),
    }


def test_rapid_partials_for_one_turn_are_throttled(tmp_path):
    clock = {"t": 0.0}
    block, emitted = _new_block(tmp_path, clock=lambda: clock["t"])
    clock["t"] = 0.0
    block.on_event(_partial("T1", "Mira", "h", at=0.0))
    count_after_first = len(_partial_intents(emitted))
    clock["t"] = 0.05
    block.on_event(_partial("T1", "Mira", "ha", at=0.05))
    assert len(_partial_intents(emitted)) == count_after_first
    clock["t"] = 0.4
    block.on_event(_partial("T1", "Mira", "hal", at=0.4))
    assert len(_partial_intents(emitted)) == count_after_first + 1
    assert _partial_intents(emitted)[-1].payload["text"] == "hal"


def test_final_clears_the_matching_open_partial(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_partial("T1", "Mira", "hal", at=5.0))
    block.on_event(_utterance("Mira", "hallo", "T1", at=5.4))
    cleared = _partial_intents(emitted)[-1]
    assert cleared.payload == {
        "turn_id": "T1", "speaker": "Mira", "text": "",
        "started_at": _wall_clock_label(5.0),
    }


def test_final_for_a_different_turn_does_not_clear_the_open_partial(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_partial("T1", "Mira", "hal", at=5.0))
    block.on_event(_utterance("Jonas", "servus", "T2", at=5.4))
    assert len(_partial_intents(emitted)) == 1  # only the original partial


def test_block_is_registered_in_the_block_host_registry():
    """issue #181: same registration pattern as every other block
    (gateway/block_host_wiring.py)."""
    from gateway import block_host_wiring as bhw

    assert bhw.REGISTRY["live-transcript"] is LiveTranscriptBlock
