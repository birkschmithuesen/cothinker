#!/usr/bin/env python3
# CoThinker — the Laya decision worker (runs under the Laya interpreter)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Answer one typed question per stdin line: does `neu` open a new topic?

RUNS UNDER A DIFFERENT PYTHON than the rest of this repo. The laya wheel
needs 3.11 and torch; the repo's interpreter is 3.9 and stdlib-only. This
file is therefore never imported by the gateway and never by a test run
that needs the model: the parent (blocks.topics.detect.LayaDetector)
starts it as a SUBPROCESS and speaks JSON lines to it.

`torch` and `laya` are imported inside `load_predict()`, not at module
level, so `python3 tests/test_topic_detect.py` can import this file under
3.9 and test the protocol loop against a fake `predict`. A test that
imported torch would pull two gigabytes into every test run.

THE PROTOCOL, one JSON object per line, in this order:

  out  {"ready": true, "load_s": 13.9}      once, before anything else
  in   {"id": 7, "vorher": "...", "neu": "..."}
  out  {"id": 7, "p": 0.528, "ms": 233.0}
  out  {"id": 7, "error": "<token>"}        a TOKEN, never a message
  out  {"id": -1, "error": "bad_request"}   an unusable line; loop goes on

Line-based and synchronous on purpose: one decision at a time is what
live operation would do, so the measured latency is the latency that
would matter. stderr belongs to the library (transformers warns a lot)
and the parent sends it to DEVNULL.

NO TEXT IN ANY ANSWER. The answer carries an id, a probability and a
duration. On failure it carries the exception's TYPE NAME: the input is
speech of third parties, and a model library's exception message quotes
its input often enough to make a message a leak.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# PINNED, NOT TUNED. Three sessions are far too few to fit a question
# type, a context length and a wording on -- that would be fitting noise
# and calling it a result (see the plan's D8). Measured on a synthetic
# German probe (architect, 2026-09-25): the "noul" type separated
# (p 0.528 for a new topic against 0.139 for the same one), "choice" did
# not (0.419 against 0.395).
QUESTION_ID = "neues_thema"
QUESTION_TYPE = "noul"
QUESTION_INSTRUCTIONS = "Eroeffnet `neu` ein neues Thema gegenueber `vorher`?"


def question() -> dict:
    """The one question this worker ever asks."""
    return {QUESTION_ID: {"type": QUESTION_TYPE,
                          "instructions": QUESTION_INSTRUCTIONS}}


def answer_of(reply: object) -> float:
    """The probability out of one laya reply, or a ValueError.

    Checked field by field rather than indexed blindly: a model release
    that renames a key has to fail loudly here, not silently deliver a
    constant that looks like a measurement.
    """
    if not isinstance(reply, dict):
        raise ValueError("reply is not an object")
    antworten = reply.get("answers")
    if not isinstance(antworten, dict):
        raise ValueError("reply carries no answers")
    eintrag = antworten.get(QUESTION_ID)
    if not isinstance(eintrag, dict) or QUESTION_TYPE not in eintrag:
        raise ValueError("answers carry no " + QUESTION_TYPE)
    return float(eintrag[QUESTION_TYPE])


def serve(stdin, stdout, predict, load_s: float = 0.0) -> int:
    """The protocol loop. `predict(state, questions) -> reply`."""
    stdout.write(json.dumps({"ready": True, "load_s": round(load_s, 3)}) + "\n")
    stdout.flush()
    for zeile in stdin:
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            anfrage = json.loads(zeile)
            kennung = int(anfrage["id"])
            vorher = str(anfrage["vorher"])
            neu = str(anfrage["neu"])
        except (AttributeError, KeyError, TypeError, ValueError):
            stdout.write(json.dumps({"id": -1, "error": "bad_request"}) + "\n")
            stdout.flush()
            continue
        t0 = time.monotonic()
        try:
            p = answer_of(predict({"vorher": vorher, "neu": neu}, question()))
        except Exception as fehler:          # noqa: BLE001
            stdout.write(json.dumps(
                {"id": kennung, "error": type(fehler).__name__}) + "\n")
            stdout.flush()
            continue
        stdout.write(json.dumps(
            {"id": kennung, "p": p,
             "ms": round((time.monotonic() - t0) * 1000.0, 1)}) + "\n")
        stdout.flush()
    return 0


def load_predict(model_dir: str, threads: int = 4):
    """laya's `predict`, plus how long the load took. The only torch site."""
    import torch                                      # noqa: E402,PLC0415
    from laya.agent import Agent                      # noqa: E402,PLC0415

    # Four threads on a four-core box: measured 187-1700 ms per decision
    # (F4). More threads than cores would only add contention, and the
    # number is an argument so a different host can say otherwise.
    torch.set_num_threads(max(1, int(threads)))
    t0 = time.monotonic()
    agent = Agent(model_dir, device="cpu")
    return agent.predict, time.monotonic() - t0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Laya topic-boundary worker: JSON lines over stdin.")
    p.add_argument("--model", required=True, help="the model directory")
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args(argv)
    vorhersage, load_s = load_predict(a.model, a.threads)
    return serve(sys.stdin, sys.stdout, vorhersage, load_s)


if __name__ == "__main__":
    raise SystemExit(main())
