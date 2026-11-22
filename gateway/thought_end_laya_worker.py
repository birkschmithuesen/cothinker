#!/usr/bin/env python3
# CoThinker — the Laya thought-end worker (runs under the Laya interpreter)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Answer one typed question per stdin line: is this contribution complete?

RUNS UNDER A DIFFERENT PYTHON than the rest of this repo. The laya wheel
needs 3.11 and torch; the repo's interpreter is 3.9 and standard library
only. This file is therefore never imported by the gateway and never by a
test that needs the model: the parent
(gateway/thought_end.WorkerClient) starts it as a SUBPROCESS and speaks
JSON lines to it. `torch` and `laya` are imported inside
`load_predict()`, so the protocol loop stays testable under 3.9 -- a test
that imported torch would pull two gigabytes into every test run.

THE PROTOCOL (see gateway/thought_end.py for the shared description):

  out  {"ready": true, "load_s": 16.0}
  in   {"id": 7, "kontext": "...", "aeusserung": "..."}
  out  {"id": 7, "p": 0.176, "ms": 143.0, "cpu_ms": 579.0}
  out  {"id": 7, "error": "<a token>"}
  out  {"id": -1, "error": "bad_request"}

A SEPARATE FILE FROM THE TOPIC WORKER ON PURPOSE. Sub-project C ships
`blocks/topics/laya_worker.py`, the same protocol with a different pinned
question. Merging the two is a later card; doing it while both cards are
open would mean two branches editing one file for no gain.

THE QUESTION IS PINNED, NOT TUNED. Type, wording and both criteria are
fixed before the measurement (plan D5). Three sessions are far too few to
fit a question on -- that would be fitting noise and calling it a result.
Measured on a synthetic German probe (plan F5): p was 0.176 and 0.167 for
two finished contributions against 0.064 and 0.056 for two that broke
off. The direction separates, the absolute scale is low, and the
threshold therefore comes from the leave-one-session-out rule in the
bench, not from this file.

NO TEXT IN ANY ANSWER: an id, a probability and two durations. On failure
the exception's TYPE NAME, never its message -- a model library quotes
its input often enough to make a message a leak, and the input here is
speech of third parties.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

QUESTION_ID = "abgeschlossen"
QUESTION_TYPE = "noul"
QUESTION_INSTRUCTIONS = (
    "Ist mit `aeusserung` ein Redebeitrag inhaltlich abgeschlossen, sodass "
    "nichts Angefangenes offen bleibt?")
QUESTION_CRITERIA = {
    "false": "Der Beitrag bricht ab oder geht erkennbar weiter.",
    "true": "Der Beitrag ist abgeschlossen.",
}


def question() -> dict:
    """The one question this worker ever asks. A fresh dict every call."""
    return {QUESTION_ID: {"type": QUESTION_TYPE,
                          "instructions": QUESTION_INSTRUCTIONS,
                          "criteria": dict(QUESTION_CRITERIA)}}


def answer_of(reply: object) -> float:
    """The probability out of one laya reply, or a ValueError.

    Checked field by field rather than indexed blindly: a model release
    that renames a key has to fail loudly here, not silently deliver a
    constant that a whole results table would then be built on.
    """
    if not isinstance(reply, dict):
        raise ValueError("reply is not an object")
    answers = reply.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("reply carries no answers")
    entry = answers.get(QUESTION_ID)
    if not isinstance(entry, dict) or QUESTION_TYPE not in entry:
        raise ValueError("answers carry no " + QUESTION_TYPE)
    return float(entry[QUESTION_TYPE])


def serve(stdin, stdout, predict, load_s: float = 0.0) -> int:
    """The protocol loop. `predict(state, questions) -> reply`."""
    stdout.write(json.dumps({"ready": True, "load_s": round(load_s, 3)}) + "\n")
    stdout.flush()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            req_id = int(request["id"])
            context = str(request["kontext"])
            utterance = str(request["aeusserung"])
        except (AttributeError, KeyError, TypeError, ValueError):
            stdout.write(json.dumps({"id": -1, "error": "bad_request"}) + "\n")
            stdout.flush()
            continue
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            p = answer_of(predict({"kontext": context,
                                   "aeusserung": utterance}, question()))
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


def load_predict(model_dir: str, threads: int = 4):
    """laya's `system_one`, plus how long the load took. The only torch site."""
    import torch                                   # noqa: PLC0415
    import laya                                    # noqa: PLC0415

    # Four threads on a four-core box: measured 143 ms median per decision
    # (plan F5). More threads than cores would only add contention, and
    # the number is an argument so a different host can say otherwise.
    torch.set_num_threads(max(1, int(threads)))
    t0 = time.monotonic()
    agent = laya.load(model_dir, device="cpu")
    return agent.system_one, time.monotonic() - t0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Laya thought-end worker: JSON lines on stdin.")
    p.add_argument("--model", required=True, help="the model directory")
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args(argv)
    predict_fn, load_s = load_predict(a.model, a.threads)
    return serve(sys.stdin, sys.stdout, predict_fn, load_s)


if __name__ == "__main__":
    raise SystemExit(main())
