# CoThinker — tests of blocks/voice_attribution (the VoiceAttributionBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests: feeds `utterance`/`tick` CoreEvents, same shapes
`speaking-share` consumes (`gateway/block_host_wiring.py::feed_utterance`),
and checks the `voice:room` view intent it emits (issue #164).

    python3 -m pytest -q tests/test_block_voice_attribution.py
"""
from __future__ import annotations

from pathlib import Path

from blocks.voice_attribution import VoiceAttributionBlock, _wall_clock_label
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, emitted, clock=lambda: 0.0):
    return BlockContext(instance=None, session_dir=Path(tmp_path),
                        state=StateStore(Path(tmp_path) / "voice-attribution"),
                        agent=None, llm=None, language="en", clock=clock,
                        emit=emitted.append, artefact=lambda name, text: None)


def _new_block(tmp_path, config=None, clock=lambda: 0.0):
    emitted = []
    block = VoiceAttributionBlock()
    block.start(_ctx(tmp_path, emitted, clock), config or {})
    return block, emitted


def _utterance(speaker, at, text="hallo"):
    return CoreEvent("utterance", {"text": text, "speaker": speaker, "at": at})


def _tick(now):
    return CoreEvent("tick", {"now": now})


def test_two_alternating_speakers_set_speaking_and_recent(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", at=0.0))
    block.on_event(_utterance("Jonas", at=5.0))
    payload = emitted[-1].payload
    assert payload["speaking"] == "Jonas"
    assert payload["since"] == _wall_clock_label(5.0)
    assert [row["name"] for row in payload["recent"]] == ["Mira"]
    assert payload["recent"][0]["last_at"] == _wall_clock_label(5.0)


def test_intent_shape_matches_the_surface_api(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", at=0.0))
    intent = emitted[-1]
    assert intent.id == "voice:room" and intent.block == block.id
    assert intent.kind == "voice" and intent.audience == "room"


def test_silence_past_the_hold_window_clears_speaking(tmp_path):
    block, emitted = _new_block(tmp_path, config={"hold_s": 2.0})
    block.on_event(_utterance("Mira", at=0.0))
    block.on_event(_tick(now=1.0))
    assert emitted[-1].payload["speaking"] == "Mira"
    block.on_event(_tick(now=3.0))
    payload = emitted[-1].payload
    assert payload["speaking"] is None
    assert payload["since"] is None
    assert payload["recent"][0]["name"] == "Mira"


def test_tick_alone_does_not_re_emit_an_unchanged_payload(tmp_path):
    block, emitted = _new_block(tmp_path, config={"hold_s": 2.0})
    block.on_event(_utterance("Mira", at=0.0))
    count_after_utterance = len(emitted)
    block.on_event(_tick(now=0.5))
    block.on_event(_tick(now=1.0))
    assert len(emitted) == count_after_utterance


def test_repeated_utterance_from_the_same_speaker_does_not_re_emit(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", at=0.0))
    count = len(emitted)
    block.on_event(_utterance("Mira", at=0.3))
    assert len(emitted) == count


def test_rapid_changes_are_rate_limited_to_the_minimum_gap(tmp_path):
    """Max 2 events/s: a payload change within 0.5 s of the last emit is
    dropped rather than emitted, so a tick never re-emits faster than the
    bound either."""
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance("Mira", at=0.0))
    assert len(emitted) == 1
    block.on_event(_utterance("Jonas", at=0.2))
    assert len(emitted) == 1
    block.on_event(_utterance("Tamar", at=0.6))
    assert len(emitted) == 2
    assert emitted[-1].payload["speaking"] == "Tamar"


def test_empty_speaker_label_does_not_change_or_emit(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_utterance(None, at=0.0))
    assert emitted == []


def test_block_is_registered_in_the_block_host_registry():
    """issue #164: the block host's REGISTRY grows one line per block, same
    as every other block (gateway/block_host_wiring.py). Not wired into
    the Reflect template's own block list here -- ADR 0010 fixes Reflect
    at exactly 12 blocks; adding a 13th is a template/ADR decision for the
    maintainer, not silently made by this change."""
    from gateway import block_host_wiring as bhw

    assert bhw.REGISTRY["voice-attribution"] is VoiceAttributionBlock
