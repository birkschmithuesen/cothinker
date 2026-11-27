# CoThinker — tests of the mic-holder speaker-labels stage
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of `stt/pipeline/speaker_labels/mic_holder.py` (`MicHolderStage`).

The last test runs the stage inside the real in-process transport
(`shared.stt_inprocess.InprocessSession`), through `SttConfig(stages=(...))`,
with a fake in-memory engine -- no network.
"""
from __future__ import annotations

import asyncio
import threading
import time

from shared.speechmatics import SttEvent
from shared.stt import SttConfig, TranscriptEvent
from shared.stt_inprocess import InprocessSession
from stt.pipeline.speaker_labels.mic_holder import MicHolderStage


def until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def ev(type_="final", t_start=0.0, t_end=1.0, audio_epoch=0.0, speaker="S1", extra=None):
    return TranscriptEvent(type=type_, t_start=t_start, t_end=t_end,
                           audio_epoch=audio_epoch, speaker=speaker, extra=dict(extra or {}))


def test_holder_at_exact_change_picks_new_holder():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=100.0)
    stage.set_holder("Ben", at=105.0)
    assert stage.holder_at(105.0) == "Ben"


def test_before_first_change_falls_back_to_current():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=100.0)
    stage.set_holder("Ben", at=105.0)
    assert stage.holder_at(50.0) == "Ben"


def test_none_time_falls_back_to_current():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=100.0)
    stage.set_holder("Ben", at=105.0)
    assert stage.holder_at(None) == "Ben"


def test_unchanged_holder_is_not_recorded_twice():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=100.0)
    stage.set_holder("Ada", at=150.0)
    stage.set_holder("Ben", at=200.0)
    assert stage._times == [100.0, 200.0]
    assert stage._holders == ["Ada", "Ben"]


def test_final_gets_the_holder_at_its_audio_start_not_now():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=100.0)
    stage.set_holder("Ben", at=105.0)
    final = ev(type_="final", t_start=4.5, audio_epoch=100.0, speaker="S1")
    (out,) = stage.feed(final)
    assert out.speaker == "Ada"


def test_nobody_holds_the_mic_gives_no_speaker():
    stage = MicHolderStage()
    final = ev(type_="final", t_start=1.0, audio_epoch=0.0, speaker="S1")
    (out,) = stage.feed(final)
    assert out.speaker is None


def test_diarization_label_kept_in_extra():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=0.0)
    final = ev(type_="final", t_start=1.0, audio_epoch=0.0, speaker="S2")
    (out,) = stage.feed(final)
    assert out.speaker == "Ada"
    assert out.extra["diarization_label"] == "S2"


def test_non_transcript_events_pass_unchanged():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=0.0)
    other = ev(type_="eou", t_start=None, t_end=None, audio_epoch=None, speaker=None)
    (out,) = stage.feed(other)
    assert out is other


# ============================================= integration: in-process transport
class FakeEngine:
    """In-memory stand-in for a Speechmatics stream, same shape as
    `tests/test_stt_inprocess.py`'s: no network, no `websockets`."""

    def __init__(self):
        self.started = False
        self._loop = None
        self._q: asyncio.Queue = asyncio.Queue()

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.started = True

    async def send_pcm(self, chunk):
        pass

    async def events(self):
        while True:
            item = await self._q.get()
            if item is None:
                return
            yield item

    async def close(self):
        await self._q.put(None)

    def push(self, item):
        self._loop.call_soon_threadsafe(self._q.put_nowait, item)


class Reader:
    """Drains `events()` on a thread, like a consumer would."""

    def __init__(self, session):
        self.events: list[TranscriptEvent] = []
        self._t = threading.Thread(target=self._run, args=(session,), daemon=True)
        self._t.start()

    def _run(self, session):
        for ev_ in session.events():
            self.events.append(ev_)

    def of(self, type_):
        return [e for e in self.events if e.type == type_]


def test_runs_inside_the_inprocess_transport():
    stage = MicHolderStage()
    stage.set_holder("Ada", at=time.time() - 10.0)
    eng = FakeEngine()
    cfg = SttConfig(transport="inprocess", url="", stages=(stage,))
    sess = InprocessSession(cfg, lambda speakers: eng, retry_s=(0.05,)).start()
    try:
        assert until(lambda: sess.state == "ok")
        rd = Reader(sess)
        eng.push(SttEvent(kind="final", text="hallo", speaker_label="S1",
                          start_s=0.0, end_s=0.3, msg_id="m", volume=None, words=None))
        eng.push(SttEvent(kind="eou", text="", speaker_label=None,
                          start_s=None, end_s=None, msg_id="m", volume=None, words=None))
        assert until(lambda: rd.of("final"))
        (fin,) = rd.of("final")
        assert fin.speaker == "Ada"
        assert fin.extra["diarization_label"] == "S1"
    finally:
        sess.close()
