# CoThinker — level-3 template run test: the call window end to end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""ADR 0022 level 3 for the call window (issue #180, ADR 0024): a recorded
transcript runs through the REAL `gateway.segmenter.BlockBuilder`, the
production Reflect template and the production block registry
(`shared.block_registry.load_block`, which already finds
`blocks.call_window.CallWindowBlock`) -- no fake of `wire_call_window`/
`dispatch_call_window_state`, the exact functions `gateway/consumer.py`
calls in a live session.

The transcript is issue #180's own one-breath acceptance example,
delivered exactly as `tests/test_segmenter.py`'s level-1
`test_a_sentence_with_the_wake_phrase_and_a_pause_before_the_end_phrase_
stays_one_call` does: "Hey Robo. Ich gebe dir." opens the call, a 4 s
pause follows (well under the 8 s `call_silence_s` default, so it must
NOT cut the call -- the whole point of the window), then
"Infrastruktur. Server. Oberflächen. Robo, Ende." both carries the
content and closes it. Exactly one call must come out of
`BlockBuilder.output`, and the `listen:room` view intent must have gone
from idle to listening and back to idle with `last_close` recorded --
through the production block host, not a hand-rolled substitute.

    python3 -m pytest -q tests/test_call_window_reflect_run.py
"""
from __future__ import annotations

from pathlib import Path

from gateway import block_host_wiring as bhw
from gateway.segmenter import Block, BlockBuilder
from shared.block_registry import load_block

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "reflect" / "template.toml"


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = t


def test_call_window_reflect_run_sends_exactly_one_call_with_all_three_keywords(tmp_path):
    clock = FakeClock(0.0)
    calls: list[Block] = []

    bb = BlockBuilder(output=calls.append, now=clock)

    intents: list = []
    blocks = bhw.build_session_blocks(
        session_dir=tmp_path, sink=intents.append, log=lambda *a: None,
        clock=clock, template_path=TEMPLATE_PATH, load=load_block)

    bhw.wire_call_window(blocks, bb)
    bhw.dispatch_call_window_state(blocks, bb)   # idle, at the start

    bb.sentence("Hey Robo. Ich gebe dir.")
    bhw.dispatch_call_window_state(blocks, bb)

    clock.set(4.0)                                # pause -- must NOT cut the call
    bb.sentence("Infrastruktur. Server. Oberflächen. Robo, Ende.")
    bhw.dispatch_call_window_state(blocks, bb)

    assert len(calls) == 1, f"expected exactly one call, got {len(calls)}: {calls}"
    call = calls[0]
    assert call.wake_word is True
    assert call.reason == "end"
    for keyword in ("Infrastruktur", "Server", "Oberflächen"):
        assert keyword in call.text
    assert "Ende" not in call.text

    listen_intents = [i for i in intents if i.id == "listen:room"]
    assert listen_intents, "no listen:room intent was emitted"
    states = [i.payload["state"] for i in listen_intents]
    assert states[0] == "idle"
    assert "listening" in states
    assert states[-1] == "idle"
    assert listen_intents[-1].payload["last_close"] == {"reason": "end", "at": 4.0}
