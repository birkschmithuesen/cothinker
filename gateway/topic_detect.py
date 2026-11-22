#!/usr/bin/env python3
# CoThinker — topic-boundary detectors, swappable
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Does this block open a new topic? Three candidates behind one contract.

  worker   the recorded `{"action":"topic"}` of the live agent -- the
           baseline, replayed by bench/replay.py, no code here.
  drift    embedding drift over a sliding window (DriftDetector).
  laya     a typed decision model on CPU (LayaDetector, which speaks to
           gateway/topic_laya_worker.py in a subprocess).
  jev      a typed decision model behind OpenRouter's decisions API
           (JevDetector, over blocks/decisions_tasks_questions/openrouter_jev.py). A US provider:
           BENCH-ONLY, see that module's docstring.

WHY THIS LIVES UNDER gateway/ AND NOT UNDER bench/. A measurement is only
worth anything if the thing measured is the thing that would run. These
classes are therefore the ones a later live switch imports, unchanged,
and they must not depend on the bench: nothing here imports `bench/`, and
nothing here opens a file or a vault path. Caching, process guards and
report writing belong to bench/detect.py.

THE DECISION UNIT IS ONE BLOCK, and a detector sees EARLIER blocks only.
`score(history, current)` takes the history as an argument instead of an
index into a full list, so a detector CANNOT look at the future even by
accident -- offline that would be a silent cheat that live operation
could never reproduce.

TWO POSITIONS PER DECISION, because they answer two different questions
and a single one of them would flatter or punish every candidate:

  end   ("evidence") the newest speech the decision could see. The
        recorded baseline's `TopicDecision.t_ev` is exactly this, so this
        is the position the comparison headlines.
  start ("claim")    where the detector says the topic begins.

NO TEXT LEAVES THIS MODULE TOWARDS A PRINTING CALLER. `BlockView.text`
exists for the model call. `Score` carries numbers and, on failure, a
TOKEN -- a status code or an exception type name, never a message: a
model library's exception text can quote its input, and the input is
speech of third parties.
"""
from __future__ import annotations

import json
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

# The sliding window of the drift detector. SET, NOT MEASURED, and
# deliberately not tuned on three sessions -- see the plan's D7/D8.
WINDOW_S = 180.0
MAX_WINDOW_BLOCKS = 6


@dataclass
class BlockView:
    """One decision unit: a block as a detector gets to see it."""
    index: int
    start: float
    end: float
    at: float
    text: str
    chars: int


@dataclass
class Score:
    """One decision. Numbers, and on failure a token -- never a message.

    `cost_usd` is set only by a detector that pays per call in USD (jev):
    None means "this detector has no such cost", which is not the same
    as a call that cost 0.0 -- so drift and laya, which never set it,
    keep reporting exactly what they reported before it existed.
    """
    index: int
    p: float = 0.0
    ms: float = 0.0
    tokens: int = 0
    error: str = ""
    cost_usd: Optional[float] = None


class Detector:
    """The contract the three candidates keep. Not usable by itself."""

    name = ""
    model = ""

    def score(self, history, current):
        raise NotImplementedError("a Detector subclass has to answer score()")

    def close(self) -> None:
        """Release whatever the detector holds. Safe to call twice."""


def block_views(cuts, utterances) -> list:
    """The block cuts of a replay, as decision units.

    Duck-typed on purpose: `cuts` need `first_idx`, `last_idx` and `at`,
    `utterances` need `index`, `at` and `text`. That is what
    bench/replay.py's `BlockCut` and bench/session.py's `Utterance`
    carry, and this module does not import either of them.
    """
    nach_index = {u.index: u for u in utterances}
    out = []
    for cut in cuts:
        teile = [nach_index[k] for k in range(cut.first_idx, cut.last_idx + 1)
                 if k in nach_index]
        if not teile:
            continue
        # The same join gateway/segmenter.py's `Block.text` does, so
        # `chars` here is the `chars` the trace recorded.
        text = " ".join(u.text.strip() for u in teile if u.text.strip()).strip()
        out.append(BlockView(index=len(out), start=teile[0].at,
                             end=teile[-1].at, at=cut.at, text=text,
                             chars=len(text)))
    return out


def cosine(a, b) -> float:
    """Cosine similarity, -1..1. A zero vector gives 0.0."""
    if len(a) != len(b):
        raise ValueError(f"vectors of different length: {len(a)} and {len(b)}")
    punkt = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return punkt / (na * nb)


def mean_vector(vectors) -> list:
    """The component-wise mean. An empty list gives an empty vector."""
    if not vectors:
        return []
    n = len(vectors[0])
    for v in vectors:
        if len(v) != n:
            raise ValueError(f"vectors of different length: {n} and {len(v)}")
    return [sum(v[i] for v in vectors) / float(len(vectors)) for i in range(n)]


def drift_window(history, current, *, window_s: float = WINDOW_S,
                 max_blocks: int = MAX_WINDOW_BLOCKS) -> list:
    """The earlier blocks a drift decision is allowed to look back at.

    Time-bounded first, then count-bounded to the NEWEST `max_blocks`: a
    round that races through six blocks in a minute has moved on more
    than one that took ten, and the window has to follow the
    conversation, not a fixed block count.
    """
    grenze = current.start - window_s
    drin = [v for v in history if v.end >= grenze]
    if max_blocks <= 0:
        return []
    return drin[-max_blocks:]


def boundaries(scores, views, threshold) -> list:
    """`(index, t_ev, t_claim, t_dec)` per block that reaches `threshold`.

    `t_dec` is the cut time plus the MEASURED decision latency: what the
    stage could have shown, when. A detector that answers in 0.3 s and
    one that answers in 12 s find the same boundary at two different
    moments, and the delay column is where that difference shows up.

    A decision carrying an error token is skipped -- a failed call is a
    gap in the measurement, not a "no boundary here" answer.
    """
    nach_index = {v.index: v for v in views}
    out = []
    for s in scores:
        blick = nach_index.get(s.index)
        if s.error or blick is None or s.p < threshold:
            continue
        out.append((s.index, blick.end, blick.start,
                    blick.at + s.ms / 1000.0))
    return out


# --- candidate (b): embedding drift over a sliding window ------------------


class DriftDetector(Detector):
    """`1 - cos(block, mean(window))`, one embedding per block.

    `embed(text) -> (vector, tokens, ms)` is injected: the cache, the
    key and the HTTP client live in bench/detect.py, and this class
    stays a pure function of what it is given -- that is also the only
    way the test can compute the expected score by hand.

    WHY THE MEAN AND NOT THE PREVIOUS BLOCK. A single previous block is
    one utterance's worth of wording; a round that says "ja, genau" in
    between would look like a topic change every second block. The mean
    of the window is what the conversation has been about lately, which
    is the thing a new topic departs from.
    """

    name = "drift"

    def __init__(self, embed, *, model: str = "",
                 window_s: float = WINDOW_S,
                 max_blocks: int = MAX_WINDOW_BLOCKS) -> None:
        self.embed = embed
        self.model = model
        self.window_s = window_s
        self.max_blocks = max_blocks

    def score(self, history, current) -> Score:
        # Embedded FIRST, and unconditionally: live, the embedding is
        # computed when the block arrives, and the decision that needs
        # it is the next one. A broad except on purpose -- one failed
        # call out of 228 must not cost the whole measurement, and the
        # token makes the gap countable (bench/detect.py exits non-zero
        # on any of them).
        try:
            vektor, tokens, ms = self.embed(current.text)
        except Exception as fehler:          # noqa: BLE001
            return Score(index=current.index, error=type(fehler).__name__)
        fenster = drift_window(history, current, window_s=self.window_s,
                               max_blocks=self.max_blocks)
        if not fenster:
            return Score(index=current.index, p=0.0, ms=ms, tokens=tokens)
        t0 = time.monotonic()
        try:
            vorher = [self.embed(v.text)[0] for v in fenster]
            abstand = 1.0 - cosine(vektor, mean_vector(vorher))
        except Exception as fehler:          # noqa: BLE001
            return Score(index=current.index, ms=ms, tokens=tokens,
                         error=type(fehler).__name__)
        rechenzeit = (time.monotonic() - t0) * 1000.0
        return Score(index=current.index,
                     p=min(1.0, max(0.0, abstand)),
                     ms=ms + rechenzeit, tokens=tokens)


# --- candidate (c): a typed decision model in a subprocess -----------------

LAYA_VORHER_CHARS = 1200
LAYA_NEU_CHARS = 600
LAYA_TIMEOUT_S = 30.0


def laya_input(history, current, *, vorher_chars: int = LAYA_VORHER_CHARS,
               neu_chars: int = LAYA_NEU_CHARS) -> tuple:
    """`(vorher, neu)` exactly as the laya worker receives them.

    ONE function for the live detector and for bench/train_data.py: a
    model fine-tuned on rows cut differently from what the detector sends
    would be measured on an input it never saw in training.
    """
    vorher = " ".join(v.text for v in history).strip()[-vorher_chars:]
    return vorher, current.text[:neu_chars]


class LayaError(Exception):
    """The worker process failed. Messages are TOKENS, never text."""


class LayaDetector(Detector):
    """Asks gateway/topic_laya_worker.py, one decision at a time.

    The worker runs under a different interpreter (3.11 + torch), so
    this is a process boundary and not an import. Everything that can go
    wrong across that boundary is turned into an error TOKEN on a
    `Score`: a failed decision is a countable gap in the measurement,
    never an exception that takes down a 228-block run.

    A TIMEOUT KILLS THE CHILD instead of waiting for the late answer.
    The protocol is one request per line and matched by id; a late
    answer would arrive while the next request is in flight, and every
    following decision would silently belong to the previous block. A
    desynchronised pipe cannot be measured against, so the detector
    declares itself dead and every later decision reports the same
    token.
    """

    name = "laya"

    def __init__(self, python: str, worker: str, model_dir: str, *,
                 threads: int = 4, timeout_s: float = LAYA_TIMEOUT_S,
                 vorher_chars: int = LAYA_VORHER_CHARS,
                 neu_chars: int = LAYA_NEU_CHARS, model: str = "") -> None:
        self._python = python
        self._worker = worker
        self._model_dir = model_dir
        self._threads = threads
        self.timeout_s = timeout_s
        self.vorher_chars = vorher_chars
        self.neu_chars = neu_chars
        self.model = model or model_dir
        self.load_s = 0.0
        self._proc = None
        self._sel = None
        self._buf = b""
        self._counter = 0
        self._dead = ""

    # ------------------------------------------------------------ process
    def start(self) -> None:
        """Start the worker and read its ready line. Raises LayaError."""
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [self._python, self._worker, "--model", self._model_dir,
             "--threads", str(self._threads)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # DEVNULL, not PIPE: transformers writes pages of warnings,
            # and a pipe nobody reads fills its buffer and stops the
            # child in the middle of an answer.
            stderr=subprocess.DEVNULL, bufsize=0)
        self._sel = selectors.DefaultSelector()
        self._sel.register(self._proc.stdout, selectors.EVENT_READ)
        # The model load measured 13.7-16.5 s (F4), which is longer than
        # any per-request timeout should be -- so the ready line gets its
        # own, generous one.
        zeile = self._readline(max(120.0, self.timeout_s))
        try:
            bereit = json.loads(zeile)
        except ValueError:
            self._die("bad_ready")
        if not isinstance(bereit, dict) or not bereit.get("ready"):
            self._die("not_ready")
        wert = bereit.get("load_s")
        self.load_s = float(wert) if isinstance(wert, (int, float)) else 0.0

    def _die(self, token: str) -> None:
        self._dead = token
        self.close()
        raise LayaError(token)

    def _readline(self, timeout_s: float) -> str:
        """One line from the child, or a LayaError. No thread, no signal."""
        frist = time.monotonic() + timeout_s
        while b"\n" not in self._buf:
            rest = frist - time.monotonic()
            if rest <= 0.0:
                self._die("timeout")
            if not self._sel.select(rest):
                continue
            stueck = self._proc.stdout.read(65536)
            if not stueck:
                self._die("closed")
            self._buf += stueck
        zeile, _, self._buf = self._buf.partition(b"\n")
        return zeile.decode("utf-8", "replace")

    def _write(self, obj: dict) -> None:
        roh = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(roh)
            self._proc.stdin.flush()
        except OSError:
            self._die("write_failed")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if self._sel is not None:
            self._sel.close()
            self._sel = None
        self._buf = b""
        if proc is None:
            return
        for strom in (proc.stdin, proc.stdout):
            try:
                if strom is not None:
                    strom.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10.0)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # ------------------------------------------------------------- decide
    def score(self, history, current) -> Score:
        if self._dead:
            return Score(index=current.index, error=self._dead)
        vorher, neu = laya_input(history, current,
                                 vorher_chars=self.vorher_chars,
                                 neu_chars=self.neu_chars)
        if not vorher:
            # Nothing to be new against. Asking anyway would measure the
            # model's prior on an empty context, and that number would
            # then be scored as if it were a decision.
            return Score(index=current.index, p=0.0, ms=0.0)
        t0 = time.monotonic()
        try:
            if self._proc is None:
                self.start()
            self._counter += 1
            kennung = self._counter
            self._write({"id": kennung, "vorher": vorher, "neu": neu})
            antwort = json.loads(self._readline(self.timeout_s))
            if not isinstance(antwort, dict):
                self._die("bad_reply")
            if int(antwort.get("id", -1)) != kennung:
                self._die("id_mismatch")
            if antwort.get("error"):
                return Score(index=current.index,
                             ms=(time.monotonic() - t0) * 1000.0,
                             error=str(antwort["error"])[:32])
            p = float(antwort["p"])
        except LayaError as fehler:
            return Score(index=current.index, error=str(fehler)[:32])
        except (KeyError, OSError, TypeError, ValueError) as fehler:
            self._dead = type(fehler).__name__
            self.close()
            return Score(index=current.index, error=self._dead)
        gemessen = antwort.get("ms")
        ms = (float(gemessen) if isinstance(gemessen, (int, float))
              else (time.monotonic() - t0) * 1000.0)
        return Score(index=current.index, p=min(1.0, max(0.0, p)), ms=ms)


# --- candidate (d): a typed decision model over HTTP (OpenRouter, Jev) ------

# Same order of magnitude as laya's, and the same reasoning: the newest
# context and the head of the new block are what the question is about.
# PINNED, NOT TUNED (D8) -- three sessions cannot carry a fitted length.
JEV_VORHER_CHARS = 1200
JEV_NEU_CHARS = 600
# `vorher` is the text of at most this many earlier blocks. Laya takes the
# whole history and keeps the last 1200 characters; with blocks of a few
# hundred characters that is the last two or three blocks anyway, and
# saying "two" outright keeps a run of very short blocks from dragging
# older ones into the context.
JEV_VORHER_BLOCKS = 2

JEV_NOUL_ID = "neues_thema"
# The SAME wording the laya worker asks (gateway/topic_laya_worker.py),
# so the two typed models answer literally the same question.
JEV_NOUL_INSTRUCTIONS = "Eroeffnet `neu` ein neues Thema gegenueber `vorher`?"
JEV_CHOICE_ID = "gleiches_thema_vertiefung_neu_rueckkehr"
JEV_CHOICE_INSTRUCTIONS = "Wie verhaelt sich `neu` zu `vorher`?"
JEV_CHOICE_CRITERIA = {
    "gleiches_thema": "`neu` spricht weiter ueber dasselbe Thema wie "
                      "`vorher`, ohne es wesentlich zu vertiefen.",
    "vertiefung": "`neu` bleibt beim Thema von `vorher`, geht aber auf "
                  "einen Unterpunkt oder ein Detail genauer ein.",
    "neues_thema": "`neu` eroeffnet ein Thema, das in `vorher` nicht "
                   "vorkommt.",
    "rueckkehr": "`neu` kehrt zu einem Thema zurueck, das frueher schon "
                 "besprochen wurde, aber nicht das Thema von `vorher` ist.",
}


def jev_questions(with_choice: bool = False) -> dict:
    """The questions object of one request. Pinned, not built per call."""
    fragen = {JEV_NOUL_ID: {"type": "noul",
                            "instructions": JEV_NOUL_INSTRUCTIONS}}
    if with_choice:
        fragen[JEV_CHOICE_ID] = {"type": "choice",
                                 "instructions": JEV_CHOICE_INSTRUCTIONS,
                                 "criteria": dict(JEV_CHOICE_CRITERIA)}
    return fragen


def _error_token(fehler) -> str:
    """A client's own token if it carries a clean one, else the type name."""
    token = getattr(fehler, "token", "")
    if isinstance(token, str) and 0 < len(token) <= 32 and all(
            (c.isascii() and c.isalnum()) or c in "_-" for c in token):
        return token
    return type(fehler).__name__


class JevDetector(Detector):
    """Asks Jev (TypeSafe, `typesafe/jev-1.13`) one decision at a time.

    `client.decide(state, questions) -> Decision` is injected -- the
    OpenRouter client of blocks/decisions_tasks_questions/openrouter_jev.py, or bench/detect.py's
    cache in front of it. This module does not import either. Synchronous
    on purpose, like laya: one decision at a time is what live operation
    would do, so the latency measured is the latency that would matter.

    ONE CALL, SEVERAL ANSWERS. With `with_choice=True` the choice
    question rides in the SAME request as the noul question ("speculative
    fan-out"): the second answer costs a few output tokens instead of a
    second round trip. `p` always comes from the noul answer; the choice
    answer is kept in `self.choices[block index]` for the bench to write
    next to the decision -- a measurement on the side, not the score.

    COST lives on the `Score` (`cost_usd`, USD from `usage.cost`), not
    in a running sum on the detector: bench/detect.detector_decisions()
    already turns Scores into rows one by one, a per-decision value
    survives the cache (a hit reports what the original call cost), and
    a sum can always be taken later while a sum cannot be split again.

    NO DEAD STATE, unlike laya. A timeout over HTTP leaves no pipe that a
    late answer could desynchronise; the next decision is a fresh
    request. A failure is a token on this Score and nothing more.
    """

    name = "jev"

    def __init__(self, client, *, with_choice: bool = False,
                 vorher_blocks: int = JEV_VORHER_BLOCKS,
                 vorher_chars: int = JEV_VORHER_CHARS,
                 neu_chars: int = JEV_NEU_CHARS, model: str = "") -> None:
        self.client = client
        self.with_choice = with_choice
        self.vorher_blocks = vorher_blocks
        self.vorher_chars = vorher_chars
        self.neu_chars = neu_chars
        self.model = model or getattr(client, "model", "")
        self.questions = jev_questions(with_choice)
        self.choices: dict = {}

    def score(self, history, current) -> Score:
        fenster = (history[-self.vorher_blocks:]
                   if self.vorher_blocks > 0 else [])
        vorher = " ".join(v.text for v in fenster).strip()[-self.vorher_chars:]
        if not vorher:
            # Nothing to be new against -- the same rule as laya.
            return Score(index=current.index, p=0.0, ms=0.0)
        state = {"vorher": vorher, "neu": current.text[:self.neu_chars]}
        # A broad except on purpose, as in DriftDetector: one failed call
        # must not cost the other decisions, and the token counts the gap.
        try:
            antwort = self.client.decide(state, self.questions)
        except Exception as fehler:          # noqa: BLE001
            return Score(index=current.index, error=_error_token(fehler))
        antworten = getattr(antwort, "answers", None) or {}
        # JEV-SITE: the noul answer's `noul` field IS the boundary score p.
        # Not `p`, not `confidence`: the decisions API names it `noul`.
        roh = (antworten.get(JEV_NOUL_ID) or {}).get("noul")
        if isinstance(roh, bool) or not isinstance(roh, (int, float)):
            # JEV-SITE: an answer without a numeric noul is a GAP, not a
            # "no boundary" -- a 0.0 here would be scored as a decision.
            return Score(index=current.index, ms=float(antwort.ms),
                         tokens=int(antwort.tokens),
                         cost_usd=float(antwort.cost_usd), error="no_noul")
        if self.with_choice:
            self._keep_choice(current.index, antworten.get(JEV_CHOICE_ID))
        return Score(index=current.index,
                     # JEV-SITE: clamped into 0..1 -- a probability outside
                     # it is not one, and the threshold search assumes 0..1.
                     p=min(1.0, max(0.0, float(roh))),
                     ms=float(antwort.ms), tokens=int(antwort.tokens),
                     cost_usd=float(antwort.cost_usd))

    def _keep_choice(self, index: int, wahl) -> None:
        if not isinstance(wahl, dict):
            return
        eintrag = {}
        # JEV-SITE: only a choice among the criteria WE sent is recorded;
        # anything else the model names is dropped, not stored.
        if wahl.get("choice") in JEV_CHOICE_CRITERIA:
            eintrag["choice"] = wahl["choice"]
        konf = wahl.get("confidence")
        if isinstance(konf, (int, float)) and not isinstance(konf, bool):
            eintrag["confidence"] = float(konf)
        wahrsch = wahl.get("probabilities")
        if isinstance(wahrsch, dict):
            eintrag["probabilities"] = {
                k: float(v) for k, v in wahrsch.items()
                if k in JEV_CHOICE_CRITERIA
                and isinstance(v, (int, float)) and not isinstance(v, bool)}
        self.choices[index] = eintrag

    def close(self) -> None:
        """Nothing to release: urllib opens and closes per request."""
