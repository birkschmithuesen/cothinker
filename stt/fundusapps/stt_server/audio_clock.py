"""The audio clock, and the enrichment that puts it on a `final` event.

Three small pure pieces, deliberately free of audio, devices and I/O so
they can be tested on numbers alone:

* `AudioClock` counts the 16 kHz samples that ACTUALLY passed through
  `STTRecognizer.run()` after resampling -- exactly the samples written
  to the debug WAV, and exactly the samples handed to the chunker or the
  backend. Frames dropped while muted are NOT counted, because they are
  not part of the stream. A position on this clock is therefore also a
  sample position in that WAV, and in whatever a later diarization
  service is fed from the same stream.

  Why counting output samples is exact: the resampler was measured
  (44100->16000 and 48000->16000, 4096-sample blocks) to put a step onset
  at 1.013 s into its output at 1.013 s -- 0.0 ms of delay -- and to hold
  back 143 samples of filter tail at the very end. So the count of
  resampled OUTPUT samples is the position, with nothing to correct for.

* `SpanRegistry` remembers, per turn id, where the speech of that turn
  began and ended on that clock. Bounded: a four-hour meeting must not
  grow a dict forever, and nobody ever asks for the first turn again.

* `TimestampEnricher` sits on the `EventBus` and fills the fields on
  `final` events on their way out. It lives here rather than in the
  backends because the two paths that produce spans (the VAD chunker and
  the ElevenLabs word timestamps) have nothing else in common, and
  because a backend does not know its recognizer's clock.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

SAMPLE_RATE = 16000


class AudioClock:
    """Sample counter for one recognizer, plus a wall-clock anchor."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = int(sample_rate)
        self._samples = 0
        self._epoch: Optional[float] = None

    @property
    def samples(self) -> int:
        return self._samples

    @property
    def epoch(self) -> Optional[float]:
        """Unix time of sample 0, or None before the first frame."""
        return self._epoch

    def advance(self, n_samples: int, now: Optional[float] = None) -> int:
        """Count `n_samples` that just passed through.

        Returns the position of the FIRST of them -- the offset the caller
        needs for the frame it is about to hand on, not the one after it.
        """
        start = self._samples
        self._samples += int(n_samples)
        if self._epoch is None:
            now = time.time() if now is None else now
            self._epoch = now - self._samples / self.sample_rate
        return start

    def reset_epoch(self) -> None:
        """Drop the anchor; the next `advance()` computes a new one.

        Called after a resume and after a device switch. While muted the
        wall clock moves and this one does not, so an anchor taken before
        the pause would report every later utterance as too early by the
        length of the pause. The SAMPLE COUNT is deliberately not reset:
        it is the position in the debug WAV, and that file has no gap.
        """
        self._epoch = None

    def seconds(self, sample: int) -> float:
        """A sample position as seconds, rounded to milliseconds."""
        return round(sample / self.sample_rate, 3)


class SpanRegistry:
    """turn_id -> (first speech sample, last speech sample end). Bounded."""

    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = int(max_entries)
        self._spans: "OrderedDict[str, Tuple[int, int]]" = OrderedDict()
        self._lock = threading.Lock()

    def record(self, turn_id, start_sample: int, end_sample: int) -> None:
        if not turn_id:
            return
        with self._lock:
            self._spans[turn_id] = (int(start_sample), int(end_sample))
            self._spans.move_to_end(turn_id)
            while len(self._spans) > self.max_entries:
                self._spans.popitem(last=False)

    def get(self, turn_id) -> Optional[Tuple[int, int]]:
        if not turn_id:
            return None
        with self._lock:
            return self._spans.get(turn_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._spans)


class TimestampEnricher:
    """Fills `t_start`/`t_end`/`audio_epoch` on `final` events.

    One instance per server, dispatching on `recognizer_id`: two
    recognizers on one bus have two clocks and two registries, and mixing
    them up would put one speaker's timestamps on the other's words.

    Never raises for a shape it does not recognise -- it runs inside
    `EventBus.publish()`, i.e. on the audio thread.
    """

    def __init__(self) -> None:
        self._state: Dict[str, Tuple[AudioClock, SpanRegistry]] = {}
        self._lock = threading.Lock()

    def register(self, recognizer_id, clock: AudioClock,
                 registry: SpanRegistry) -> None:
        with self._lock:
            self._state[str(recognizer_id)] = (clock, registry)

    def __call__(self, event) -> None:
        if getattr(event, "type", None) != "final":
            return
        with self._lock:
            state = self._state.get(str(getattr(event, "recognizer_id", "")))
            if state is None:
                # speechmatics-rt puts the diarization label into
                # recognizer_id and keeps the audio channel in `channel`.
                state = self._state.get(str(getattr(event, "channel", "")))
        if state is None:
            return
        clock, registry = state
        # The epoch is filled even when the span is not: the ElevenLabs
        # backend brings its own t_start but has no way to know the
        # recognizer's anchor, and the two answers are independent.
        if getattr(event, "audio_epoch", None) is None:
            event.audio_epoch = clock.epoch
        if getattr(event, "t_start", None) is not None:
            return
        span = registry.get(getattr(event, "turn_id", None))
        if span is None:
            return
        event.t_start = clock.seconds(span[0])
        event.t_end = clock.seconds(span[1])
