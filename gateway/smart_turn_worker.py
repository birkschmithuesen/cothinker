#!/usr/bin/env python3
# CoThinker — smart-turn-v3.2 thought-end worker (runs under a foreign Python)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Answer one question per stdin line: is this turn finished? p.

RUNS UNDER A DIFFERENT PYTHON than the rest of this repo: it needs numpy,
onnxruntime and faster_whisper, and the repo's interpreter has none of
them. The parent (bench/thought_end.py, through
gateway/thought_end.WorkerClient) starts it as a SUBPROCESS and speaks
JSON lines to it. All three heavy imports sit inside `load_scorer()`, so
`python3 tests/test_thought_end.py` can import this file under 3.9 and
test the protocol loop against a fake `decide`.

THE PROTOCOL (see gateway/thought_end.py for the shared description):

  out  {"ready": true, "load_s": 1.4}
  in   {"id": 7, "t_start": 100.0, "t_end": 106.2}
  out  {"id": 7, "p": 0.83, "ms": 47.2, "cpu_ms": 46.9}
  out  {"id": 7, "error": "<a token>"}
  out  {"id": -1, "error": "bad_request"}
  in   {"id": 8, "pcm": "<base64>"}          the live path (plan 2026-09-26)
  out  {"id": 8, "error": "unsupported"}     that request kind is not served

`pcm` is PCM16 little-endian, mono, 16 kHz -- the window that the STT
server on the recording machine cuts from its ring buffer. The audio
arrives over a local pipe from a parent process on the same machine; it
never leaves the machine.

`t_start`/`t_end` are seconds on the AUDIO CLOCK of the WAV this worker
was started with -- the same clock sub-project A puts on a `final`
(sample position divided by 16000). The worker keeps the LAST 8 s of that
span and front-pads with zeros, which is pipecat's convention: the buffer
holds the current turn, and a turn shorter than the window is padded at
the front, not the back.

THE FRONTEND IS A REBUILD, AND IT WAS MEASURED. pipecat calls
`transformers.WhisperFeatureExtractor(chunk_length=8)` with
`do_normalize=True`; this file uses faster_whisper's numpy
FeatureExtractor with the same parameters on a hand-normalised array,
because transformers is not installed in any interpreter on this machine
that also has onnxruntime. Maximum absolute difference between the two on
a synthetic 8 s input: 1.0e-5, mean 4.0e-8 (plan F4). The ORDER matters
and is easy to get wrong: pad to 8 s FIRST, normalise the padded array,
only then the mel frontend.

The ONNX graph already ends on a sigmoid ("ONNX model returns sigmoid
probabilities", pipecat inference.py) -- the output is the probability,
and a second sigmoid here would squash every answer towards 0.5.

NO TEXT IN ANY ANSWER: an id, a probability and two durations. On failure
the exception's TYPE NAME, never its message.
"""
from __future__ import annotations

import os

# BEFORE numpy is imported anywhere, and that is the whole point of these
# five lines sitting above the other imports. Measured on this vServer
# (plan F4): with them, one decision costs 46-49 ms of CPU; without them
# the wall time stays at ~50 ms but the CPU time is 191-200 ms, because
# numpy's BLAS spins four threads through a matrix that takes
# microseconds. A bench that reports CPU cost has to report the cost of
# the configuration it recommends, so this is not tunable.
for _VAR in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_VAR] = "1"

import argparse  # noqa: E402
import base64  # noqa: E402
import binascii  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import wave  # noqa: E402

SAMPLE_RATE = 16000
WINDOW_S = 8.0
N_MELS = 80
N_FRAMES = 800
HOP_LENGTH = 160
N_FFT = 400
SAMPLE_WIDTH = 2


def frame_range(t_begin, t_end, total_frames, *, sample_rate=SAMPLE_RATE,
                window_s=WINDOW_S):
    """(first frame, frame count, front-pad frames) of one decision window.

    Pure arithmetic, so it can be tested without a file and without
    numpy. `count + pad` is always exactly the window length: the model
    has one input shape, and anything else would be a silent misfeed.
    """
    window = int(round(float(window_s) * sample_rate))
    end = int(round(float(t_end) * sample_rate))
    if end > int(total_frames):
        end = int(total_frames)
    if end < 0:
        end = 0
    begin = int(round(float(t_begin) * sample_rate))
    if begin < 0:
        begin = 0
    if begin > end:
        begin = end
    if end - begin > window:
        begin = end - window
    count = end - begin
    return begin, count, window - count


class WavReader:
    """The recording, read one frame range at a time. 16 kHz mono PCM16.

    The format is checked rather than assumed: the whole audio clock is a
    sample count divided by 16000, and a 44.1 kHz file would place every
    decision several minutes off without ever raising.
    """

    def __init__(self, path) -> None:
        self.handle = wave.open(str(path), "rb")
        channels = self.handle.getnchannels()
        width = self.handle.getsampwidth()
        rate = self.handle.getframerate()
        if channels != 1 or width != SAMPLE_WIDTH or rate != SAMPLE_RATE:
            self.handle.close()
            raise ValueError(
                f"wav is {channels} channels, {width * 8} bit, {rate} Hz")
        self.frames = self.handle.getnframes()

    def read(self, first: int, count: int) -> bytes:
        if count <= 0:
            return b""
        self.handle.setpos(int(first))
        return self.handle.readframes(int(count))

    def close(self) -> None:
        self.handle.close()


def serve(stdin, stdout, decide, load_s: float = 0.0, score_pcm=None) -> int:
    """The protocol loop.

    `decide(t_start, t_end) -> p` answers a request that names a span of
    the WAV the worker was started with; `score_pcm(raw) -> p` answers a
    request that carries the window itself. Either may be None, and a
    request for the missing kind gets the token `unsupported` -- a live
    worker has no WAV, a bench worker needs no pcm.
    """
    stdout.write(json.dumps({"ready": True, "load_s": round(load_s, 3)}) + "\n")
    stdout.flush()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            req_id = int(request["id"])
            if "pcm" in request:
                raw = base64.b64decode(str(request["pcm"]), validate=True)
                if len(raw) % SAMPLE_WIDTH:
                    raise ValueError("not PCM16")
                call = (None if score_pcm is None
                        else (lambda raw=raw: score_pcm(raw)))
            else:
                t_start = float(request["t_start"])
                t_end = float(request["t_end"])
                call = (None if decide is None
                        else (lambda a=t_start, b=t_end: decide(a, b)))
        except (AttributeError, KeyError, TypeError, ValueError,
                binascii.Error):
            stdout.write(json.dumps({"id": -1, "error": "bad_request"}) + "\n")
            stdout.flush()
            continue
        if call is None:
            stdout.write(json.dumps({"id": req_id,
                                     "error": "unsupported"}) + "\n")
            stdout.flush()
            continue
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            p = float(call())
        except Exception as exc:          # noqa: BLE001
            stdout.write(json.dumps(
                {"id": req_id, "error": type(exc).__name__}) + "\n")
            stdout.flush()
            continue
        stdout.write(json.dumps({
            "id": req_id, "p": p,
            "ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "cpu_ms": round((time.process_time() - c0) * 1000.0, 1)}) + "\n")
        stdout.flush()
    return 0


def load_scorer(model_path, threads: int = 1):
    """`score_pcm(raw) -> p` for one window of PCM16, plus the load time.

    The only place numpy, onnxruntime and faster_whisper are imported.
    One warm-up decision on silence is included in `load_s`: the first
    ONNX run pays for its own memory arena, and charging that to the
    first utterance of the meeting would put a 200 ms outlier into a
    latency median taken over 300 decisions.
    """
    import numpy as np                                     # noqa: PLC0415
    import onnxruntime as ort                              # noqa: PLC0415
    from faster_whisper.feature_extractor import (         # noqa: PLC0415
        FeatureExtractor)

    t0 = time.monotonic()
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = max(1, int(threads))
    session = ort.InferenceSession(str(model_path), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    features = FeatureExtractor(feature_size=N_MELS, sampling_rate=SAMPLE_RATE,
                                hop_length=HOP_LENGTH, chunk_length=int(WINDOW_S),
                                n_fft=N_FFT)
    window = int(round(WINDOW_S * SAMPLE_RATE))

    def score_pcm(raw):
        audio = np.zeros(window, dtype=np.float32)
        if raw:
            values = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if values.size > window:
                values = values[-window:]
            # Front-padded: the zeros sit BEFORE the speech.
            audio[window - values.size:] = values
        x = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        f = features(x, padding=0)[:, :N_FRAMES][None].astype(np.float32)
        logits = session.run(None, {"input_features": f})[0]
        return float(np.asarray(logits).reshape(-1)[0])

    score_pcm(b"")
    return score_pcm, time.monotonic() - t0


def wav_decide(score_pcm, reader):
    """`decide(t_start, t_end) -> p` over a recording, via `score_pcm`."""
    def decide(t_start, t_end):
        first, count, _pad = frame_range(t_start, t_end, reader.frames)
        return score_pcm(reader.read(first, count))
    return decide


def load_decide(model_path, wav_path, threads: int = 1):
    """The model's `decide(t_start, t_end) -> p`, plus the load time."""
    score_pcm, load_s = load_scorer(model_path, threads)
    return wav_decide(score_pcm, WavReader(wav_path)), load_s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="smart-turn-v3.2 thought-end worker: JSON lines on stdin.")
    p.add_argument("--model", required=True, help="the .onnx file")
    p.add_argument("--wav", default="",
                   help="the recording, 16 kHz mono PCM16; without it only "
                        "pcm requests are served (the live path)")
    p.add_argument("--threads", type=int, default=1)
    a = p.parse_args(argv)
    score_pcm, load_s = load_scorer(a.model, a.threads)
    decide = wav_decide(score_pcm, WavReader(a.wav)) if a.wav else None
    return serve(sys.stdin, sys.stdout, decide, load_s, score_pcm=score_pcm)


if __name__ == "__main__":
    raise SystemExit(main())
