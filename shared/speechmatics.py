#!/usr/bin/env python3
# CoThinker — shared Speechmatics EU realtime STT client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The wire config (`sm_config`) and the recognition read loop
(`SpeechmaticsStream`), shared by every consumer of the Speechmatics
client: the meeting STT backend
(`stt/fundusapps/stt_server/backends/speechmatics_rt_backend.py`, which
subclasses `shared.utterance.UtteranceCollector` to publish onto its own
`EventBus`) and the table hub, which also holds enrollment, the excerpt
pacing helper and the manual probe CLI.

Speaker labels sent over the wire are always placeholders `P1`..`Pn`,
n = `MAX_SPEAKERS` (`docs/kurator-kernmotivation.md` / spec section 5,
"names never go to the model").

No live calls happen in the automated tests: `tests/fake_speechmatics.py`
is the only server `tests/test_speechmatics_client.py` talks to.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from typing import AsyncIterator, Mapping, Sequence

SPEECHMATICS_URL = "wss://eu.rt.speechmatics.com/v2"
RATE = 16000
BYTES_PER_S = RATE * 2  # s16le mono
CHUNK_S = 0.1  # 100 ms per send, paced in real time
# The table's placeholder-label budget: how many "names never go to the
# model" labels (P1..Pn) a table can seat. 5 today; the hub's
# enrollment UI caps a table's roster at the same number.
DEFAULT_TABLE_SIZE = 5
MAX_SPEAKERS = DEFAULT_TABLE_SIZE


def effective_max_speakers(max_speakers: int | None) -> int:
    """The Speechmatics diarization label budget actually in effect for one
    connection: `max_speakers` if given, else `MAX_SPEAKERS + 3` (MUST 5's
    own default -- see `sm_config`'s docstring). Pure,
    so the table hub's reconnect guard (MUST 1) can read the same cap
    `sm_config` sends over the wire without a second, driftable copy of
    the "None -> +3" rule."""
    return MAX_SPEAKERS + 3 if max_speakers is None else int(max_speakers)


def additional_vocab_for_roles(roles: Sequence[Mapping[str, object]]) -> list[dict]:
    """Speechmatics `additional_vocab` entries (custom dictionary): one per
    role's `given` name (with any `sounds_like` the role lists), `short` and
    `object` -- content only, duplicates collapsed, order preserved. Without
    them the engine never hears these names, and a mis-heard name is a miss
    a downstream role matcher can not recover from."""
    seen: set = set()
    out: list[dict] = []

    def add(content: object, sounds_like: list[str] | None = None) -> None:
        if not isinstance(content, str) or not content.strip() or content in seen:
            return
        seen.add(content)
        entry: dict = {"content": content}
        if sounds_like:
            entry["sounds_like"] = sounds_like
        out.append(entry)

    for role in roles:
        sounds_like = [s for s in (role.get("sounds_like") or [])
                       if isinstance(s, str) and s.strip()]
        add(role.get("given"), sounds_like or None)
        add(role.get("short"))
        add(role.get("object"))
    return out


def sm_config(speakers: list[dict] | None, get_speakers: bool, language: str,
              max_delay_s: float, eou_silence_s: float, *,
              max_speakers: int | None = None,
              prefer_current_speaker: bool = False,
              speaker_sensitivity: float | None = None,
              additional_vocab: list[dict] | None = None) -> dict:
    """The `StartRecognition` message. `speakers` carries placeholder labels
    (`P1`..`Pn`, n = `MAX_SPEAKERS`) with their enrolled `speaker_identifiers`, never names.

    `max_speakers` None means `MAX_SPEAKERS + 3`: the table's persons plus
    three slots for a moderator and neighbour-bleed voices (S17 spec §5;
    raised from +1 by MUST 5 -- a 7th voice past the old cap
    got merged into an existing label; unmeasured so far, docs/stt.md).
    `prefer_current_speaker` is off by default: on the 26.10. meeting audio,
    turning it off (with max_speakers 10) cut speaker confusion 0.037 ->
    0.021 (docs/stt.md).

    `additional_vocab`: the custom-dictionary entries
    Speechmatics' own docs describe (docs.speechmatics.com/speech-to-text/
    features/custom-dictionary) -- built by the table hub from a table's
    roles file. Omitted (not an empty list) when there is nothing to
    send, same style as `speakers`/`get_speakers`.

    `speaker_sensitivity`: Speechmatics realtime speaker-
    identification's own knob for how strongly an enrolled speaker is
    preferred over detecting a new, generic one (docs.speechmatics.com/
    speech-to-text/realtime/speaker-identification) -- lower values make a
    new voice more likely to be recognised as new rather than swallowed
    into an enrolled label (live bug: table 01, 31.10., one participant's
    whole intro came back as another's enrolled "P3"; see docs/stt.md).
    Omitted by default,
    same "not sent unless given" style as `additional_vocab`/`speakers`/
    `get_speakers` -- never defaulted to a hardcoded value inside this
    function; `DEFAULT_SPEAKER_SENSITIVITY` below is the one place a
    caller may opt into the measured value."""
    dcfg: dict = {"max_speakers": effective_max_speakers(max_speakers),
                  "prefer_current_speaker": bool(prefer_current_speaker)}
    if speakers:
        dcfg["speakers"] = speakers
    if get_speakers:
        dcfg["get_speakers"] = True
    if speaker_sensitivity is not None:
        dcfg["speaker_sensitivity"] = speaker_sensitivity
    tcfg: dict = {
        "language": language,
        "model": "enhanced",
        "diarization": "speaker",
        "enable_partials": True,
        "max_delay": max_delay_s,
        "speaker_diarization_config": dcfg,
        # ANNAHME 0.5 s; a later chain (S9) measures this.
        "conversation_config": {"end_of_utterance_silence_trigger": eou_silence_s},
        # MUST 4a: `{}` makes Speechmatics attach a per-word "volume"
        # alternative at all (docs.speechmatics.com/speech-to-text/features/audio-filtering --
        # without this key the field never arrives and SttEvent.volume/.words are always
        # None, so role_enroll's gate silently passes everything via "None passes"
        # semantics). Measure only: no `volume_threshold` here -- the gate is relative
        # (role_enroll.py) and must still see the quiet words to compare them.
        "audio_filtering_config": {},
    }
    if additional_vocab:
        tcfg["additional_vocab"] = additional_vocab
    return {
        "message": "StartRecognition",
        "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": RATE},
        "transcription_config": tcfg,
    }


@dataclass
class SttEvent:
    kind: str  # "partial" | "final" | "eou" | "error"
    text: str
    speaker_label: str | None
    start_s: float | None
    end_s: float | None
    msg_id: str
    # Mean of the words' own "volume" alternative (the
    # role-enrollment volume gate), or None when no word carried one --
    # "unknown" on purpose, never coerced to 0.0: a caller that treats
    # missing data as "silent" would gate out every older/partial answer.
    volume: float | None = None
    # MUST 4b: the individual (word, volume) pairs this mean was
    # computed from, in the order they appear in `text` -- the per-utterance mean
    # mixes an enrolled speaker's own words with any bleed words diarization
    # attached to the same label; a per-word filter needs the words themselves.
    # Punctuation carries no volume and is not a "word" here. Empty when no word
    # result arrived at all (e.g. the fallback stream's batch transcription).
    # Per-word diarization labels for the label filter --
    # [{"w", "label", "volume"}], None when the message had no word results.
    # Additive: speaker_label stays the majority label.
    words: list | None = None


def _word_results(msg: dict) -> list[dict]:
    return [r for r in msg.get("results", []) if r.get("type") == "word"]


def _word_volume(r: dict) -> float | None:
    """`volume` sits on the word result itself, a sibling of "start_time"/
    "end_time" (Speechmatics docs, audio-filtering, "Volume labelling"),
    not inside "alternatives"."""
    return r.get("volume")


def _transcript_results(msg: dict) -> list[dict]:
    return [r for r in msg.get("results", []) if r.get("type") in ("word", "punctuation")]


def _join_transcript(results: list[dict]) -> str:
    """Joins word and punctuation results, respecting `attaches_to`:
    a "previous" mark (period, comma) gets no space before it, a "next"
    mark (opening quote) gets no space after it."""
    parts: list[str] = []
    suppress_next_space = False
    for r in results:
        alts = r.get("alternatives") or [{}]
        content = alts[0].get("content", "")
        if not content:
            continue
        attach = r.get("attaches_to") if r.get("type") == "punctuation" else None
        no_space_before = suppress_next_space or attach == "previous"
        if parts and not no_space_before:
            parts.append(" ")
        parts.append(content)
        suppress_next_space = attach == "next"
    return "".join(parts)


class SpeechmaticsStream:
    """One realtime connection to Speechmatics EU for one table.

    `speakers`, if given, is the enrollment config from the table hub's
    `enroll()`: `[{"label": "P1", "speaker_identifiers": [...]}, ...]`.
    """

    def __init__(self, key: str, language: str, speakers: list[dict] | None,
                 max_delay_s: float = 1.0, eou_silence_s: float = 0.5,
                 url: str = SPEECHMATICS_URL, *, max_speakers: int | None = None,
                 prefer_current_speaker: bool = False,
                 speaker_sensitivity: float | None = None,
                 additional_vocab: list[dict] | None = None) -> None:
        self._key = key
        self._language = language
        self._speakers = speakers
        self._max_delay_s = max_delay_s
        self._eou_silence_s = eou_silence_s
        self._max_speakers = max_speakers
        self._prefer_current_speaker = prefer_current_speaker
        self._speaker_sensitivity = speaker_sensitivity
        self._additional_vocab = additional_vocab
        self._url = url
        self._ws = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._msg_seq = 0
        self._chunks_sent = 0

    def _next_id(self) -> str:
        self._msg_seq += 1
        return f"m{self._msg_seq}"

    async def start(self) -> None:
        import websockets

        self._ws = await websockets.connect(
            self._url, additional_headers={"Authorization": f"Bearer {self._key}"},
            max_size=None)
        await self._ws.send(json.dumps(sm_config(
            self._speakers, False, self._language, self._max_delay_s, self._eou_silence_s,
            max_speakers=self._max_speakers, prefer_current_speaker=self._prefer_current_speaker,
            speaker_sensitivity=self._speaker_sensitivity,
            additional_vocab=self._additional_vocab)))
        while True:
            msg = json.loads(await self._ws.recv())
            kind = msg.get("message")
            if kind == "RecognitionStarted":
                break
            if kind == "Error":
                await self._queue.put(self._error_event(msg))
                await self._queue.put(None)
                return
        self._reader_task = asyncio.create_task(self._read_loop())

    async def send_pcm(self, chunk: bytes) -> None:
        if not chunk or self._ws is None:
            return
        await self._ws.send(chunk)
        self._chunks_sent += 1

    async def events(self) -> AsyncIterator[SttEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({
                    "message": "EndOfStream", "last_seq_no": self._chunks_sent}))
            except Exception:
                pass
        if self._reader_task is not None:
            await self._reader_task
        if self._ws is not None:
            await self._ws.close()

    def _error_event(self, msg: dict) -> SttEvent:
        return SttEvent(kind="error", text=str(msg.get("type", "error")),
                         speaker_label=None, start_s=None, end_s=None,
                         msg_id=self._next_id())

    def _transcript_event(self, kind: str, msg: dict) -> SttEvent:
        word_results = [r for r in _word_results(msg) if r.get("alternatives")]
        words = [r["alternatives"][0] for r in word_results]
        text = _join_transcript(_transcript_results(msg))
        speakers = [w["speaker"] for w in words if w.get("speaker")]
        label = Counter(speakers).most_common(1)[0][0] if speakers else None
        starts = [r["start_time"] for r in word_results if "start_time" in r]
        ends = [r["end_time"] for r in word_results if "end_time" in r]
        # Speechmatics docs (audio-filtering, "Volume labelling"): "volume" is a
        # sibling of "start_time"/"end_time" on the word result, not inside
        # "alternatives".
        word_volumes = [_word_volume(r) for r in word_results if r.get("alternatives")]
        volumes = [v for v in word_volumes if isinstance(v, (int, float))]
        # MUST 2a: start_s/end_s per word (stream-relative seconds, same
        # scale as this event's own aggregate start_s/end_s) -- additive next to
        # "w"/"label"/"volume", so select_enrollment_clip() can carve just the bytes a
        # specific label actually spoke out of a longer, mixed-voice turn recording.
        items = [{"w": a.get("content", ""), "label": a.get("speaker") or None,
                  "volume": v if isinstance(v, (int, float)) else None,
                  "start_s": r.get("start_time"), "end_s": r.get("end_time")}
                 for r, a, v in zip(word_results, words, word_volumes)]
        return SttEvent(kind="partial" if kind == "AddPartialTranscript" else "final",
                         text=text, speaker_label=label,
                         start_s=min(starts) if starts else None,
                         end_s=max(ends) if ends else None,
                         msg_id=self._next_id(),
                         volume=sum(volumes) / len(volumes) if volumes else None,
                         words=items or None)

    def _eou_event(self, msg: dict) -> SttEvent:
        meta = msg.get("metadata", {})
        return SttEvent(kind="eou", text="", speaker_label=None,
                         start_s=meta.get("start_time"), end_s=meta.get("end_time"),
                         msg_id=self._next_id())

    async def _read_loop(self) -> None:
        import websockets

        try:
            while True:
                try:
                    raw = await self._ws.recv()
                except websockets.ConnectionClosed:
                    break
                msg = json.loads(raw)
                kind = msg.get("message")
                if kind in ("AddPartialTranscript", "AddTranscript"):
                    await self._queue.put(self._transcript_event(kind, msg))
                elif kind == "EndOfUtterance":
                    await self._queue.put(self._eou_event(msg))
                elif kind == "EndOfTranscript":
                    break
                elif kind == "Error":
                    await self._queue.put(self._error_event(msg))
                    break
        finally:
            await self._queue.put(None)
