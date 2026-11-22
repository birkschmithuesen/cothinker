#!/usr/bin/env python3
# CoThinker — STT API: event type and SSE transport
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The STT API (`docs/architecture/target.md` §2.1, ADR 0003/0004): one
event type that *is* the `/events` wire, behind one API with two
transports. This module holds the event type, the SSE transport, the
transcript-stage and audio-session protocols and `open_stt()`; the
in-process transport (audio pushed by the caller) lives in
`shared/stt_inprocess.py`.

`TranscriptEvent` is additive-only on the wire: a field is never renamed
or removed, `from_wire()` is lenient about what it finds, and `to_wire()`
reproduces the dict it was built from byte for byte.
"""
from __future__ import annotations

import json
import urllib.request
import weakref
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Callable, Iterable, Iterator, Mapping, Protocol, Sequence

# The named wire keys. `channel` carries the backend's audio channel id as
# a string (e.g. "0") on the wire today, even though it is eventually a
# diarization-agnostic int in the target shape -- lenient parsing keeps
# whatever string a backend actually sends rather than discarding it.
_STR_FIELDS = ("type", "recognizer_id", "text", "turn_id", "speaker",
              "channel", "backend")
_NUM_FIELDS = ("timestamp", "t_start", "t_end", "audio_epoch")
_SCHEMA_FIELDS = _STR_FIELDS + _NUM_FIELDS


@dataclass(frozen=True)
class TranscriptEvent:
    """One line of the `/events` wire.

    `extra` holds every wire key outside the named fields (e.g. `words`),
    untouched. `wire` holds the original dict `from_wire()` was built
    from, so `to_wire()` can reproduce it exactly; `replace()` drops it,
    since the changed event no longer matches that dict.
    """

    type: str | None = None
    recognizer_id: str | None = None
    timestamp: float | None = None
    text: str | None = None
    turn_id: str | None = None
    t_start: float | None = None
    t_end: float | None = None
    audio_epoch: float | None = None
    speaker: str | None = None
    channel: str | None = None
    backend: str | None = None
    extra: dict = field(default_factory=dict)
    wire: dict | None = None

    @classmethod
    def from_wire(cls, d: dict) -> "TranscriptEvent":
        kwargs: dict = {}
        for name in _STR_FIELDS:
            v = d.get(name)
            kwargs[name] = v if isinstance(v, str) else None
        for name in _NUM_FIELDS:
            v = d.get(name)
            kwargs[name] = (float(v)
                            if isinstance(v, (int, float)) and not isinstance(v, bool)
                            else None)
        extra = {k: v for k, v in d.items() if k not in _SCHEMA_FIELDS}
        return cls(**kwargs, extra=extra, wire=dict(d))

    def to_wire(self) -> dict:
        """The original dict when built from the wire, otherwise one
        rebuilt from the fields and `extra` (used after `replace()`)."""
        if self.wire is not None:
            return dict(self.wire)
        d = {name: getattr(self, name) for name in _SCHEMA_FIELDS
            if getattr(self, name) is not None}
        d.update(self.extra)
        return d

    def replace(self, **changes) -> "TranscriptEvent":
        changes["wire"] = None
        return _dc_replace(self, **changes)


class SttSource(Protocol):
    """What every consumer of the STT API sees, whichever transport."""

    def events(self) -> Iterator[TranscriptEvent]: ...

    def close(self) -> None: ...


class TranscriptStage(Protocol):
    """A caller-supplied processing step applied to every event."""

    name: str

    def feed(self, ev: TranscriptEvent) -> Iterable[TranscriptEvent]: ...


class SttAudioSession(SttSource, Protocol):
    """The in-process transport: the caller pushes audio, reads events."""

    state: str  # "connecting" | "ok" | "degraded" | "closed"

    def send_pcm(self, pcm16k: bytes) -> None:
        """s16le mono 16 kHz. Never blocks; the order is the call order."""

    def flush(self, timeout_s: float = 2.0) -> list[TranscriptEvent]:
        """Force-flushes every open utterance and returns the resulting
        finals (after the stages); they are NOT delivered through
        `events()` again."""


_stage_errors_logged: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def run_stages(stages: Sequence[TranscriptStage], ev: TranscriptEvent,
               log: Callable[[str], None]) -> list[TranscriptEvent]:
    """Feeds `ev` through each stage in order (fan-out allowed). A stage that
    raises is logged once per (stage, exception type) and the event goes on
    unchanged."""
    events = [ev]
    for stage in stages:
        out: list[TranscriptEvent] = []
        for e in events:
            try:
                out.extend(list(stage.feed(e)))
            except Exception as exc:       # noqa: BLE001
                key = (getattr(stage, "name", repr(stage)), type(exc).__name__)
                try:                       # the seen-set lives as long as the `log` callable
                    seen = _stage_errors_logged.setdefault(log, set())
                except TypeError:          # not weak-referenceable: log every time
                    seen = set()
                if key not in seen:
                    seen.add(key)
                    log(f"stt: stage {key[0]!r} failed: {key[1]}")
                out.append(e)
        events = out
    return events


@dataclass(frozen=True)
class SttConfig:
    transport: str
    url: str
    read_timeout_s: float = 300.0
    # In-process transport only (ignored by "sse").
    engine: str = "speechmatics-eu"        # the only in-process engine
    language: str = "de"
    speakers: tuple[Mapping[str, object], ...] | None = None
    max_speakers: int | None = None
    max_delay_s: float = 1.0
    eou_silence_s: float = 0.5
    prefer_current_speaker: bool = False
    additional_vocab: tuple[Mapping[str, object], ...] | None = None
    engine_url: str | None = None
    recognizer_id: str = "inprocess"
    stages: tuple[TranscriptStage, ...] = ()
    api_key: str | None = field(default=None, repr=False)


# ==================================================================== SSE
def parse_sse(zeilen: Iterable) -> Iterator[dict]:
    """Splits an SSE stream into JSON objects.

    Robust against anything a foreign server might send: CRLF, comments,
    `event:`/`id:`/`retry:`, `data:` with and without a space, multi-line
    data fields (pretty-printed JSON), broken JSON, invalid bytes.

    An event normally ends with a blank line. Because a buffer that only
    waits for the blank line would delay an entire utterance during the
    conversation, after every data line we check whether what has been
    collected so far already forms a complete object.
    """
    daten: list[str] = []

    def fertig() -> Iterator[dict]:
        if not daten:
            return
        roh = "\n".join(daten)
        daten.clear()
        try:
            obj = json.loads(roh)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(obj, dict):
            yield obj

    for roh_zeile in zeilen:
        if isinstance(roh_zeile, (bytes, bytearray)):
            zeile = bytes(roh_zeile).decode("utf-8", "replace")
        else:
            zeile = str(roh_zeile)
        zeile = zeile.rstrip("\r\n")
        if not zeile:
            yield from fertig()
            continue
        if zeile.startswith(":"):          # comment / keep-alive
            continue
        feld, _, wert = zeile.partition(":")
        if wert.startswith(" "):
            wert = wert[1:]
        if feld != "data":
            continue                       # event:, id:, retry: not needed
        daten.append(wert)
        try:                               # already complete? then emit now
            obj = json.loads("\n".join(daten))
        except (json.JSONDecodeError, ValueError):
            continue
        daten.clear()
        if isinstance(obj, dict):
            yield obj
    yield from fertig()                    # remainder at end of stream


class SseSource:
    """One GET of `<url>/events`, the remote-server transport.

    The connection opens lazily, on the first iteration of `events()` --
    same as the old `sse_events()` generator -- so connection errors and
    read timeouts raise out of the `for` loop the caller already has
    around it, and the caller reconnects exactly as today.
    """

    def __init__(self, cfg: SttConfig) -> None:
        self._cfg = cfg
        self._response = None

    def events(self) -> Iterator[TranscriptEvent]:
        url = self._cfg.url.rstrip("/") + "/events"
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=self._cfg.read_timeout_s) as r:
            self._response = r
            for d in parse_sse(r):
                yield TranscriptEvent.from_wire(d)

    def close(self) -> None:
        if self._response is not None:
            try:
                self._response.close()
            except Exception:              # noqa: BLE001
                pass


def open_stt(cfg: SttConfig, *, engine_factory: Callable | None = None) -> SttSource:
    """`"sse"`: the remote-server transport. `"inprocess"`: a started audio
    session; the only engine is `"speechmatics-eu"` (any other name is an
    error and nothing else is tried) and `api_key` is required unless a test
    passes `engine_factory(speakers)` -- an object with the surface of
    `shared.speechmatics.SpeechmaticsStream`."""
    if cfg.transport == "sse":
        return SseSource(cfg)
    if cfg.transport == "inprocess":
        if cfg.engine != "speechmatics-eu":
            raise ValueError(f"engine {cfg.engine!r} is not available in-process")
        if engine_factory is None:
            if not cfg.api_key:
                raise ValueError("an in-process STT session needs an api_key")
            engine_factory = _speechmatics_factory(cfg)
        from shared.stt_inprocess import InprocessSession
        return InprocessSession(cfg, engine_factory).start()
    raise ValueError(f"unknown STT transport: {cfg.transport!r}")


def _speechmatics_factory(cfg: SttConfig) -> Callable:
    from shared.speechmatics import SPEECHMATICS_URL, SpeechmaticsStream

    def make(speakers):
        return SpeechmaticsStream(
            cfg.api_key, cfg.language, list(speakers) if speakers else None,
            max_delay_s=cfg.max_delay_s, eou_silence_s=cfg.eou_silence_s,
            url=cfg.engine_url or SPEECHMATICS_URL, max_speakers=cfg.max_speakers,
            prefer_current_speaker=cfg.prefer_current_speaker,
            additional_vocab=list(cfg.additional_vocab) if cfg.additional_vocab else None)
    return make
