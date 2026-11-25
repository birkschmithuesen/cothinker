"""Speechmatics EU realtime backend (`speechmatics-rt`).

A thin adapter around the shared Speechmatics client `shared/speechmatics.py`
(`SpeechmaticsStream`) -- there is exactly one Speechmatics client in this
repo, and this file only translates its `SttEvent`s into the STT server's
`TranscriptionEvent`s.

Event shape, compared with the other backends:

* `partial`: every distinct `AddPartialTranscript`, as today's ElevenLabs
  partials (the consumer ignores partials; the operator page shows them).
* `final`: Speechmatics sends `AddTranscript` per few words, not per
  utterance. A consumer that treats every final as one utterance would get
  one-word lines, so finals are collected per speaker and published as ONE
  final on `EndOfUtterance`, on a speaker change, or after `max_utt_s`.
* `recognizer_id` carries the Speechmatics diarization label (`S1`, `S2`,
  ..., `UU` = unknown) instead of the audio channel. That is the field
  `gateway/consumer.py` reads as the speaker (`spk`), so the label shows in
  the protocol without any consumer change. The channel id stays available
  as the extra `channel` key; the label also as `speaker`.
* `t_start`/`t_end`: Speechmatics word times are relative to the start of
  the WebSocket session; the audio-clock offset of the first chunk sent in
  that session turns them into audio-clock seconds, as in ElevenLabs.

The API key comes from the environment (`--api-key-env`, default
`SPEECHMATICS_API_KEY`), never from argv, and is never logged. Errors are
logged as the Speechmatics error type token only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

import numpy as np

from fundusapps.common.turnid import ulid_now
from fundusapps.stt_server.backends.base import RecognizerBackend
from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.track_speaker_resolver import build_speaker_resolver

BACKEND_NAME = "speechmatics-rt"
# Defaults measured on the 26.10. meeting audio (docs/stt.md, "Speechmatics
# defaults"): max_delay 2.0 cut WER vs batch 0.098 -> 0.076; max_speakers 10
# without prefer_current_speaker raised name attribution 0.943 -> 0.971.
DEFAULT_MAX_DELAY_S = 2.0
DEFAULT_MAX_SPEAKERS = 10
DEFAULT_EOU_SILENCE_S = 0.5
RATE = 16000

# stt/fundusapps/stt_server/backends/<this file> -> repo root, so the
# shared utterance collector and Speechmatics client are importable when
# the server runs with cwd stt/.
_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from shared.utterance import DEFAULT_MAX_UTT_S, UtteranceCollector  # noqa: E402


# The wake name, always in the custom dictionary unless the operator
# passes their own --vocab list: "Hey Robo" is the one phrase the system
# must hear, and Speechmatics heard "Hugo"/"Bobo" without it (issue #150).
DEFAULT_VOCAB = ("Robo:Robbo,Roboh",)


def parse_vocab(entries) -> list[dict]:
    """`["Robo:Robbo,Roboh", "CoThinker"]` -> Speechmatics `additional_vocab`
    (`[{"content": "Robo", "sounds_like": ["Robbo", "Roboh"]}, {"content":
    "CoThinker"}]`). Empty entries are dropped."""
    out: list[dict] = []
    for raw in entries or ():
        word, _, alts = str(raw).partition(":")
        word = word.strip()
        if not word:
            continue
        item: dict = {"content": word}
        sounds = [a.strip() for a in alts.split(",") if a.strip()]
        if sounds:
            item["sounds_like"] = sounds
        out.append(item)
    return out


def _import_stream():
    from shared.speechmatics import SPEECHMATICS_URL, SpeechmaticsStream
    return SPEECHMATICS_URL, SpeechmaticsStream


class _UtteranceCollector(UtteranceCollector):
    """`shared.utterance.UtteranceCollector`, publishing each merged final
    (and every live partial) as a `TranscriptionEvent` on `bus`."""

    def __init__(self, channel_id: str, bus: EventBus, max_utt_s: float = DEFAULT_MAX_UTT_S,
                 speaker_resolver: Optional[Callable[[float, float], Optional[str]]] = None):
        super().__init__(max_utt_s)
        self.channel_id = channel_id
        self.bus = bus
        # Issue #128 (Jitsi/BBB per-track energy attribution): when set,
        # called with (t_start, t_end) on THIS event's own audio clock for
        # every partial/final; a non-empty return overrides `speaker`
        # (normally the Speechmatics diarization label) with the
        # participant's real display name. None (the default) leaves
        # today's S1/S2 label behaviour untouched -- no caller wires this
        # yet; see fundusapps.bbb_tap.speaker_attribution.
        self.speaker_resolver = speaker_resolver
        #: audio-clock sample offset of the first chunk of this session
        self.session_base_samples: Optional[int] = None
        self._turn_id: Optional[str] = None
        self._partial_seq = 0
        self._last_partial = ""

    def _rid(self, label: Optional[str]) -> str:
        return label or self.channel_id

    def _clock(self, s: Optional[float]) -> Optional[float]:
        if s is None:
            return None
        base = (self.session_base_samples or 0) / RATE
        return round(base + float(s), 3)

    def _event(self, type_: str, text: str, label: Optional[str],
               start: Optional[float], end: Optional[float]) -> TranscriptionEvent:
        t_start, t_end = self._clock(start), self._clock(end)
        if t_start is None or t_end is None:
            t_start = t_end = None
        ev = TranscriptionEvent(
            recognizer_id=self._rid(label), type=type_, text=text,
            timestamp=time.time(), backend=BACKEND_NAME,
            turn_id=self._turn_id,
            partial_seq=self._partial_seq if type_ == "partial" else None,
            extending=False if type_ == "partial" else None,
            t_start=t_start, t_end=t_end)
        speaker = label
        if self.speaker_resolver is not None and t_start is not None and t_end is not None:
            resolved = self.speaker_resolver(t_start, t_end)
            if resolved:
                speaker = resolved
        # Extra keys, serialised by TranscriptionEvent.to_dict() only when set.
        setattr(ev, "speaker", speaker)
        setattr(ev, "channel", self.channel_id)
        return ev

    def on_partial(self, text: str, label: Optional[str],
                   start: Optional[float], end: Optional[float]) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._turn_id is None:
            self._turn_id = ulid_now()
        full = " ".join(self._words + [text])
        if full == self._last_partial:
            return
        self._last_partial = full
        self.bus.publish(self._event("partial", full, label or self._label,
                                     self._start if self._start is not None else start, end))
        self._partial_seq += 1

    def _on_before_append(self) -> None:
        if self._turn_id is None:
            self._turn_id = ulid_now()

    def _reset(self) -> None:
        super()._reset()
        self._turn_id = None
        self._partial_seq = 0
        self._last_partial = ""

    def _emit(self, text: str, label: Optional[str],
              start: Optional[float], end: Optional[float],
              volume: Optional[float] = None) -> None:
        self.bus.publish(self._event("final", text, label, start, end))


class SpeechmaticsRtBackend(RecognizerBackend):
    """One Speechmatics realtime session per recognizer, on one asyncio
    loop in a background thread. Reconnects with backoff on any error."""

    accepts_audio_offset = True

    def __init__(self, event_bus: EventBus, api_key: str, language: str = "de",
                 max_delay_s: float = DEFAULT_MAX_DELAY_S,
                 eou_silence_s: float = DEFAULT_EOU_SILENCE_S,
                 max_utt_s: float = DEFAULT_MAX_UTT_S,
                 max_speakers: int = DEFAULT_MAX_SPEAKERS,
                 prefer_current_speaker: bool = False,
                 url: Optional[str] = None,
                 stream_factory: Optional[Callable] = None,
                 speaker_resolver: Optional[Callable[[float, float], Optional[str]]] = None,
                 additional_vocab: Optional[list] = None):
        super().__init__(event_bus)
        default_url, stream_cls = _import_stream()
        self._api_key = api_key
        self._language = language
        self._max_delay_s = float(max_delay_s)
        self._eou_silence_s = float(eou_silence_s)
        self._max_utt_s = float(max_utt_s)
        self._max_speakers = int(max_speakers)
        self._prefer_current_speaker = bool(prefer_current_speaker)
        # Issue #128: passed straight through to every per-channel
        # _UtteranceCollector; see its own docstring/comment for the
        # contract. None (the default) is a no-op.
        self._speaker_resolver = speaker_resolver
        self._additional_vocab = (parse_vocab(DEFAULT_VOCAB) if additional_vocab is None
                                  else list(additional_vocab))
        self._url = url or default_url
        self._stream_factory = stream_factory or (
            lambda: stream_cls(self._api_key, self._language, None,
                               max_delay_s=self._max_delay_s,
                               eou_silence_s=self._eou_silence_s, url=self._url,
                               max_speakers=self._max_speakers,
                               prefer_current_speaker=self._prefer_current_speaker,
                               additional_vocab=self._additional_vocab or None))
        self._collectors: Dict[str, _UtteranceCollector] = {}
        self._queues: Dict[str, "asyncio.Queue"] = {}
        self._tasks: Dict[str, "asyncio.Task"] = {}
        self._stop = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        ready = threading.Event()

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, name="sm-rt-loop", daemon=True)
        self._thread.start()
        ready.wait(timeout=5.0)
        logging.info(f"[sm-rt] language={language} max_delay={self._max_delay_s} "
                     f"eou_silence={self._eou_silence_s} max_speakers={self._max_speakers} "
                     f"prefer_current_speaker={self._prefer_current_speaker} "
                     f"vocab={[v['content'] for v in self._additional_vocab]} url={self._url}")

    @classmethod
    def from_args(cls, event_bus: EventBus, args) -> "SpeechmaticsRtBackend":
        api_key = (os.environ.get(args.api_key_env) or "").strip()
        if not api_key:
            raise RuntimeError(f"{args.api_key_env} not set; required for speechmatics-rt backend")
        return cls(event_bus=event_bus, api_key=api_key, language=args.language,
                   max_delay_s=args.max_delay, eou_silence_s=args.eou_silence,
                   max_utt_s=args.max_utt,
                   max_speakers=getattr(args, "max_speakers", DEFAULT_MAX_SPEAKERS),
                   prefer_current_speaker=bool(getattr(args, "prefer_current_speaker", False)),
                   url=args.url or None,
                   # Issue #128: None (the default build_speaker_resolver
                   # returns) for every input path except the Jitsi/BBB
                   # --per-track tap, which sets args.per_track_sidecar.
                   speaker_resolver=build_speaker_resolver(args),
                   additional_vocab=(parse_vocab(args.vocab)
                                     if getattr(args, "vocab", None) else None))

    def wants_streaming(self) -> bool:
        return True

    def process_chunk(self, audio: np.ndarray, recognizer_id: str,
                      turn_id: Optional[str] = None,
                      audio_offset: Optional[int] = None) -> Iterator[TranscriptionEvent]:
        if audio.dtype != np.int16:
            audio = audio.astype(np.int16)
        self._loop.call_soon_threadsafe(self._enqueue, str(recognizer_id),
                                        int(audio_offset or 0), audio.tobytes())
        return iter(())

    def _enqueue(self, rid: str, offset: int, pcm: bytes) -> None:
        q = self._queues.get(rid)
        if q is None:
            q = asyncio.Queue()
            self._queues[rid] = q
            self._collectors[rid] = _UtteranceCollector(
                rid, self._bus, self._max_utt_s, speaker_resolver=self._speaker_resolver)
            self._tasks[rid] = self._loop.create_task(self._run(rid))
        q.put_nowait((offset, pcm))

    async def _run(self, rid: str) -> None:
        q = self._queues[rid]
        col = self._collectors[rid]
        backoff = 1.0
        while not self._stop.is_set():
            stream = self._stream_factory()
            reader = None
            try:
                await stream.start()
                col.session_base_samples = None
                reader = asyncio.create_task(self._read(stream, col))
                while not self._stop.is_set() and not reader.done():
                    try:
                        offset, pcm = await asyncio.wait_for(q.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if col.session_base_samples is None:
                        col.session_base_samples = offset
                    await stream.send_pcm(pcm)
                    backoff = 1.0
                if self._stop.is_set():
                    await stream.close()
                    return
                logging.warning(f"[sm-rt] rid={rid} session ended; reconnecting in {backoff:.0f}s")
            except Exception as e:  # noqa: BLE001 -- token only, never a message
                logging.warning(f"[sm-rt] rid={rid} session error {type(e).__name__}; "
                                f"reconnecting in {backoff:.0f}s")
            finally:
                col.flush()
                if reader is not None and not reader.done():
                    reader.cancel()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _read(self, stream, col: _UtteranceCollector) -> None:
        async for ev in stream.events():
            if ev.kind == "partial":
                col.on_partial(ev.text, ev.speaker_label, ev.start_s, ev.end_s)
            elif ev.kind == "final":
                col.on_final(ev.text, ev.speaker_label, ev.start_s, ev.end_s)
            elif ev.kind == "eou":
                col.on_eou()
            elif ev.kind == "error":
                logging.error(f"[sm-rt] speechmatics error type={ev.text}")
                return

    def close(self) -> None:
        self._stop.set()
        time.sleep(0.6)
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        for col in self._collectors.values():
            col.flush()
