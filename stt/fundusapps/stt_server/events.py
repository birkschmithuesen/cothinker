from dataclasses import dataclass, asdict
from typing import Optional, Literal, List
import logging
import queue
import threading


@dataclass
class TranscriptionEvent:
    recognizer_id: str
    type: Literal["partial", "final"]
    text: str
    timestamp: float
    backend: Literal["vosk", "whisper", "whisper-streaming", "elevenlabs-scribe",
                     "infomaniak-whisper", "speechmatics-rt"]
    status: Optional[str] = None
    confidence: Optional[float] = None
    turn_id: Optional[str] = None
    partial_seq: Optional[int] = None
    # For backends that revise partial text mid-utterance (elevenlabs-scribe):
    # True  -> partial strictly extends the prior emitted partial in this turn,
    #          safe to forward to a delta-computing botland callback.
    # False -> partial is a revision (text changed but doesn't extend), bus
    #          publishes for the operator UI but the dispatcher should skip the
    #          botland callback to avoid reposting cumulative text.
    # None  -> backend doesn't distinguish; dispatcher treats as extending
    #          (legacy behaviour for vosk / whisper / whisper-streaming).
    extending: Optional[bool] = None

    # --- audio clock (2026-09-24) -------------------------------------
    # Where the SPEECH of this utterance starts and ends, in seconds on
    # the audio clock, rounded to milliseconds. The audio clock counts the
    # 16 kHz samples that actually passed through STTRecognizer.run()
    # after resampling -- so a position here is also a sample position in
    # the debug WAV, exactly, and in whatever a diarization service is fed
    # from the same stream. See audio_clock.py.
    #
    # None means "this backend does not report them", and that case must
    # stay normal: vosk and whisper-streaming never fill them, and a
    # consumer older than 2026-09-24 has never seen them (AGENTS.md: the
    # wire contract is additive only). Both are set or neither is.
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    # Unix time of audio-clock zero for the recognizer that produced this
    # event, so `audio_epoch + t_start` is a wall-clock instant. COARSE by
    # construction (it is measured at the first processed frame, after the
    # capture queue) -- the precision contract is on t_start/t_end, never
    # on this. Re-anchored after a pause and after a device switch.
    audio_epoch: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Word times (--word-times, infomaniak-whisper only, 2026-10-20):
        # an ATTRIBUTE, not a dataclass field, so an event without them
        # serialises byte for byte as before -- the key only appears when
        # a backend actually set it. Seconds from the chunk start.
        words = getattr(self, "words", None)
        if words is not None:
            d["words"] = words
        # speechmatics-rt (2026-10-26): diarization label and the audio
        # channel, same additive attribute pattern as `words`.
        for extra in ("speaker", "channel"):
            value = getattr(self, extra, None)
            if value is not None:
                d[extra] = value
        return d


@dataclass
class MicGateEvent:
    """A confirmed change of the physical microphone switch (vad.MicGate).

    A second, deliberately narrow event type rather than a TranscriptionEvent
    with an odd `status`: this is not something anybody said, it has no text,
    no turn and no recognizer, and pressing it into the transcription shape
    would put an empty utterance into every consumer that reads `text`.

    Two constraints it has to satisfy, both of them load-bearing:

    * `to_dict()` — the SSE stream in app.py serialises everything on the bus
      with `ev.to_dict()`. A type without it raises inside the generator and
      tears the operator's event stream down mid-show.
    * `type` is a value no existing consumer handles. The Core's SSE client
      (`kg/stt_client.py`) decodes tolerantly and dispatches on
      `type in ("final", "partial")`, so "mic_gate" passes it by untouched.

    `recognizer_id` is a fixed marker rather than a real recognizer: the gate
    watches the capture source, and with two recognizers on one stream there
    is no single one it belongs to.
    """

    mic_on: bool
    level_rms: float
    threshold: float
    timestamp: float
    type: Literal["mic_gate"] = "mic_gate"
    recognizer_id: str = "mic_gate"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThoughtEndEvent:
    """One smart-turn answer for one `final`: is this turn finished? p.

    A third narrow event type, for the same reasons as MicGateEvent: it is
    not something anybody said and carries NO TEXT and NO AUDIO -- only
    the probability, the inference time, and the keys a consumer needs to
    put it next to the final it belongs to (`recognizer_id`, `turn_id`,
    `t_end`). It is published from the tap's own thread (thought_end.py),
    normally after the final it scores -- but that order is not enforced
    (the tap runs in the enrich hook before fan-out, so this event can
    reach a subscriber's queue before or after the final it is about), so
    a consumer must tolerate either order. Never from the audio thread.

    Additive to the wire contract (AGENTS.md): every existing consumer
    ignores an unknown `type` -- gateway/consumer.py reads `final` only,
    the dispatcher in sr.py skips everything that is not a
    TranscriptionEvent, the operator page branches on known types, and
    the Core's client dispatches on final/partial.

    `p` and `ms` are None when there is no answer; `error` is then a
    short token (`worker`, `worker_down`, `no_audio`, or the worker's own
    token), never a message.
    """

    recognizer_id: str
    turn_id: Optional[str]
    t_end: float
    p: Optional[float]
    ms: Optional[float]
    error: Optional[str]
    audio_s: float
    timestamp: float
    candidate: str = "smart_turn"
    type: Literal["thought_end"] = "thought_end"

    def to_dict(self) -> dict:
        return asdict(self)


class EventBus:
    """Thread-safe pub-sub for TranscriptionEvent and MicGateEvent.

    Each subscriber gets its own bounded Queue. publish() drops events for
    subscribers whose queue is full rather than blocking the producer — the
    audio thread must never stall waiting on a slow operator browser tab.
    """

    def __init__(self, max_queue_size: int = 1024, enrich=None):
        self._max_queue_size = max_queue_size
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue] = []
        self._enrich = enrich

    def set_enrich(self, enrich) -> None:
        """Install a callable run on every event just before fan-out.

        One hook, installed by `STTServer` with an `audio_clock.
        TimestampEnricher`. It lives here rather than in each backend
        because the bus is the one place EVERY event passes, whichever
        backend and whichever thread produced it -- and because a backend
        does not know its recognizer's audio clock.
        """
        self._enrich = enrich

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event) -> None:
        enrich = self._enrich
        if enrich is not None:
            try:
                enrich(event)
            except Exception:          # noqa: BLE001
                # This runs on the audio thread. A broken enricher costs
                # two optional fields; an exception here would cost the
                # event, and every event behind it.
                logging.exception("event enrichment failed; publishing "
                                  "the event unchanged")
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # drop for slow consumers
