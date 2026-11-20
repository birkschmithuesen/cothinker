"""Speech detectors for `VadChunker`: the decision, and the span.

Two jobs that have to stay apart, because they have different stakes:

* The **decision** -- is this frame speech? -- cuts utterances. For `rms`
  it is bit-for-bit the decision this server has always made, and
  `tests/test_vad.py` (untouched) is what proves it.
* The **span** -- where inside a speech frame the speech actually starts
  and ends. A recognizer frame is 4096 native samples, ~93 ms at 16 kHz
  after resampling, so a frame-granular timestamp is up to 93 ms off and
  cannot carry the 50 ms the card promises. The RMS detector therefore
  refines to 10 ms sub-windows -- and only that; it never lets a
  sub-window change the frame's verdict.

`first`/`last` in a `Detection` are ABSOLUTE positions on the audio
clock, because the caller already knows the frame's offset and a
frame-relative number would be one subtraction away from a silent bug.
"""
from __future__ import annotations

import logging
from collections import namedtuple
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
SUB_WINDOW = 160          # 10 ms at 16 kHz -- the RMS refinement
SILERO_WINDOW = 512       # 32 ms, what the model was trained on
SILERO_CONTEXT = 64       # samples carried over from the previous window

DEFAULT_MODEL_PATH = (Path(__file__).resolve().parent
                      / "vad_models" / "silero_vad.onnx")

#: `is_speech` drives the chunker exactly as before. `first`/`last` are
#: absolute sample positions, or None when the frame carried no speech.
Detection = namedtuple("Detection", "is_speech first last")


def rms(frame: np.ndarray) -> float:
    """The VAD's own RMS, 0..1 -- the same number `AudioSource.level_rms`
    reports, and `tests/test_vad_threshold.py` pins that equality."""
    if frame.size == 0:
        return 0.0
    f = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(f * f)))


class RmsDetector:
    """Today's energy threshold, plus a 10 ms refinement of the span."""

    def __init__(self, threshold: float) -> None:
        self.threshold = float(threshold)

    def set_threshold(self, value: float) -> None:
        """A single float store, and therefore atomic under the GIL -- the
        recognizer thread reads either the old or the new value, never a
        torn one. Keep it one assignment (see VadChunker's own note)."""
        self.threshold = float(value)

    def analyse(self, frame: np.ndarray, start_sample: int) -> Detection:
        if rms(frame) < self.threshold:
            return Detection(False, None, None)
        first = last = None
        for i in range(0, frame.size, SUB_WINDOW):
            window = frame[i:i + SUB_WINDOW]
            if window.size == 0:
                continue
            if rms(window) >= self.threshold:
                if first is None:
                    first = start_sample + i
                last = start_sample + i + window.size
        if first is None:
            # Unreachable with this arithmetic: a frame's mean square is
            # the sample-weighted average of its sub-windows', so at least
            # one of them is at or above the threshold whenever the frame
            # is. Kept as the fallback the docstring promises, for the day
            # somebody changes that arithmetic.
            first, last = start_sample, start_sample + frame.size
        return Detection(True, first, last)


class SileroDetector:
    """Silero VAD over plain onnxruntime -- no torch, no silero-vad package.

    Stateful and strictly sequential, like the model it wraps: the 64
    context samples and the `[2,1,128]` hidden state are carried from one
    window to the next, and samples left over at a frame boundary are
    buffered until the next frame completes a window. A recognizer frame
    is 1486 samples and a window is 512, so that remainder is the normal
    case, not an edge case.

    Measured on a formant-synthesised signal (speech 1.000-3.000 s,
    threshold 0.5): the first window at or above 0.5 starts at 1.024 s
    (+24 ms) -- within the 50 ms bound. The END of speech is NOT reliable
    on this kind of synthetic signal: probability peaks just after onset
    and falls back under 0.5 within ~90 ms, well before the signal's own
    end, because a steady synthetic buzz does not hold the model's
    attention the way real speech does. See
    `tests/test_vad_silero.py::test_silero_end_of_speech_is_an_open_question`
    (marked `xfail`; the start is pinned separately by
    `test_silero_finds_the_speech_start_within_its_measured_tolerance`)
    and `docs/stt.md` -- the end-of-speech tolerance for `--vad silero` is
    an open question, not a measured guarantee. Cost: 0.136 ms per 32 ms
    window measured on the vServer CPU on 2026-09-24 (the plan had stated
    0.464 ms).
    """

    def __init__(self, threshold: float = 0.5, model_path=None) -> None:
        try:
            import onnxruntime
        except ImportError as exc:
            raise RuntimeError(
                "--vad silero needs the onnxruntime package "
                "(pip install onnxruntime, ~50 MB). The default --vad rms "
                "runs without it."
            ) from exc
        self.threshold = float(threshold)
        self.model_path = Path(model_path or DEFAULT_MODEL_PATH)
        if not self.model_path.exists():
            raise RuntimeError(
                f"--vad silero: model not found at {self.model_path}. It is "
                "vendored in this repo; see vad_models/UPSTREAM.md."
            )
        options = onnxruntime.SessionOptions()
        # One thread each: this runs per audio frame beside everything
        # else, and a thread pool per recognizer buys nothing at 0.5 ms.
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), sess_options=options,
            providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(SILERO_CONTEXT, dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self._tail_start = 0

    def set_threshold(self, value: float) -> None:
        """Deliberately ignored.

        `/vad_threshold` and the slider on the operator page move the RMS
        energy threshold, on a 0..1 level scale. Silero's threshold is a
        probability and means something else entirely, so silently
        applying one to the other would mis-set the VAD with a number that
        looks plausible. Set it with --silero-threshold at startup.
        """
        logging.info(
            "VAD threshold %.4f ignored: this recognizer runs --vad silero, "
            "whose threshold is a probability set with --silero-threshold "
            "(currently %.2f).", float(value), self.threshold)

    def analyse(self, frame: np.ndarray, start_sample: int) -> Detection:
        x = np.concatenate([self._tail,
                            frame.astype(np.float32) / 32768.0])
        # Absolute position of x[0]: the leftover's own origin if there is
        # one, otherwise this frame's. Both are correct across a device
        # switch too: the audio clock stays contiguous through one (sr.py
        # keeps counting samples; a switch only re-anchors audio_epoch,
        # the wall-clock side), so `_tail_start + len(_tail) ==
        # start_sample` always holds -- there is no gap to be wrong by.
        base = self._tail_start if self._tail.size else start_sample
        first = last = None
        i = 0
        while x.size - i >= SILERO_WINDOW:
            window = x[i:i + SILERO_WINDOW]
            probe = np.concatenate([self._context, window])[None, :] \
                .astype(np.float32)
            out, self._state = self._session.run(
                ["output", "stateN"],
                {"input": probe, "state": self._state, "sr": self._sr})
            self._context = window[-SILERO_CONTEXT:].copy()
            if float(out[0][0]) >= self.threshold:
                if first is None:
                    first = base + i
                last = base + i + SILERO_WINDOW
            i += SILERO_WINDOW
        self._tail = x[i:].copy()
        self._tail_start = base + i
        return Detection(first is not None, first, last)


def make_detector(kind: str, energy_threshold: float,
                  silero_threshold: float = 0.5, model_path=None):
    """The one place a `--vad` value turns into an object."""
    if kind == "rms":
        return RmsDetector(energy_threshold)
    if kind == "silero":
        return SileroDetector(silero_threshold, model_path=model_path)
    raise ValueError(f"unknown VAD kind: {kind!r} (known: rms, silero)")
