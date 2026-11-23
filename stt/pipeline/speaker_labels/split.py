#!/usr/bin/env python3
# CoThinker — speaker split within one STT utterance
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Speaker split inside one utterance, behind `--speaker-split` in
gateway/consumer.py (needs `--speakers`), default off.

Live finding 2026-10-20: the STT server's VAD cuts on pauses, not on
speaker changes, so a quick reply ("ja, genau, und ...") lands in the same
final as the sentence before it, and the whole utterance gets ONE name.
Measured on that session with 1.5 s windows / 0.75 s hop against the
enrolled voices: 4 of 43 utterances carried a clear change inside.

Method (decided 2026-10-20):

1. Utterances shorter than MIN_UTTERANCE_S are left alone.
2. The span is cut into WINDOW_S windows every HOP_S; each window is
   embedded through the same `EmbeddingClient` + `cut_clip` the enrollment
   uses, IN PARALLEL on a small thread pool, with ONE deadline for the
   whole utterance. Any error or a missed deadline -> None, and the caller
   keeps today's single-name behaviour.
3. Each window gets the best enrolled name when its cosine score reaches
   SPLIT_THRESHOLD, else "unknown" (see the constant for why it sits below
   the enrollment threshold).
4. Smoothing: a run of one name counts only with >= `min_run` windows.
   For a run at the very START or END of the utterance, the unknown
   windows on either side of it count towards that length (the VAD clips
   the edge turn short -- live 13:30:42, `R?BBBBBB`).
   Weak runs and unknown windows between two strong runs of DIFFERENT
   names are a gap; the change point inside the gap is the cut that
   maximises the summed score difference (left name on the left, right
   name on the right). Gaps between runs of the SAME name are absorbed.
5. Fewer than two strong runs -> None (nothing to split).

Text: with word times from the STT event (`words`, relative to the
utterance start; the Infomaniak backend sends them with `--word-times`,
measured 2026-10-20: verbose_json carries one segment per chunk and word
times only when asked for) the cut goes to the nearest word boundary
within SEGMENT_SNAP_S; otherwise the text is split proportionally by
character count at the nearest word boundary, and the later turn is
marked with a leading "~" -- an estimate, and the reader should see that
it is one.

Never raises into the caller: `split()` returns None on anything unusual.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .enroll import UNKNOWN, cosine_similarity, cut_clip

WINDOW_S = 1.5
HOP_S = 0.75
MIN_UTTERANCE_S = 4.0
MIN_RUN = 2
DEADLINE_S = 3.0
POOL_WORKERS = 8
# Below the enrollment threshold (0.35) on purpose: a 1.5 s window carries
# far less voice than a whole utterance, and its scores sit lower. Measured
# 2026-10-20 (the operator + Roxanne, 4 split utterances, 61 windows): clear
# same-voice windows scored 0.42-0.70, the other voice -0.16-0.12; the
# one edge window of a real turn scored 0.337 against 0.043 for the other
# voice and was lost at 0.35.
SPLIT_THRESHOLD = 0.30
SEGMENT_SNAP_S = 1.0
SPLIT_MARK = "~"


@dataclass
class Turn:
    """One speaker turn inside an utterance: name, audio-clock span, text."""
    name: str
    t_start: float
    t_end: float
    text: str = ""
    estimated: bool = False


@dataclass
class SplitStats:
    """Numbers only -- what the probe and the log report."""
    windows: int = 0
    embed_calls: int = 0
    ms: int = 0
    outcome: str = ""            # split | single | short | timeout | error
    labels: str = ""             # one char per window: initial or '?'
    extra: dict = field(default_factory=dict)


def window_starts(duration: float, window_s: float = WINDOW_S,
                  hop_s: float = HOP_S) -> list:
    """Offsets (seconds from the utterance start) of every full window."""
    if duration < window_s:
        return []
    starts, t = [], 0.0
    while t + window_s <= duration + 1e-6:
        starts.append(round(t, 3))
        t += hop_s
    return starts


def label_windows(scores: Sequence[dict], threshold: float) -> list:
    """Best name per window when its score reaches `threshold`, else UNKNOWN."""
    labels = []
    for s in scores:
        if not s:
            labels.append(UNKNOWN)
            continue
        name = max(s, key=lambda n: s[n])
        labels.append(name if s[name] >= threshold else UNKNOWN)
    return labels


def _runs(labels: Sequence[str]) -> list:
    """[(label, start, end_exclusive)] of identical consecutive labels."""
    out = []
    for i, lab in enumerate(labels):
        if out and out[-1][0] == lab:
            out[-1] = (lab, out[-1][1], i + 1)
        else:
            out.append((lab, i, i + 1))
    return out


def change_points(labels: Sequence[str], scores: Sequence[dict],
                  min_run: int = MIN_RUN) -> list:
    """Segments as [(name, first_window, end_window_exclusive)], or [] when
    the utterance holds fewer than two strong runs of different names."""
    n = len(labels)
    runs = _runs(labels)
    strong = []
    for k, (lab, a, b) in enumerate(runs):
        if lab == UNKNOWN:
            continue
        length = b - a
        # Edge padding: a run at the start or end of the utterance (directly
        # or behind unknown windows only) counts the unknown windows on
        # BOTH of its sides -- the VAD clips an edge turn short.
        at_start = k == 0 or (k == 1 and runs[0][0] == UNKNOWN)
        at_end = (k == len(runs) - 1
                  or (k == len(runs) - 2 and runs[-1][0] == UNKNOWN))
        if at_start or at_end:
            if k > 0 and runs[k - 1][0] == UNKNOWN:
                length += runs[k - 1][2] - runs[k - 1][1]
            if k < len(runs) - 1 and runs[k + 1][0] == UNKNOWN:
                length += runs[k + 1][2] - runs[k + 1][1]
        if length >= min_run:
            strong.append([lab, a, b])
    if not strong:
        return []
    merged = [strong[0]]
    for lab, a, b in strong[1:]:
        if merged[-1][0] == lab:
            merged[-1][2] = b             # absorb the gap between them
        else:
            merged.append([lab, a, b])
    if len(merged) < 2:
        return []
    # Place every change point inside its gap by score difference.
    cuts = []
    for left, right in zip(merged, merged[1:]):
        gap_a, gap_b = left[2], right[1]
        best_c, best_v = None, None
        for c in range(gap_a, gap_b + 1):
            v = 0.0
            for i in range(gap_a, gap_b):
                d = (scores[i].get(left[0], 0.0)
                     - scores[i].get(right[0], 0.0))
                v += d if i < c else -d
            # Ties: the cut nearest the gap's middle.
            mid = (gap_a + gap_b) / 2
            if (best_v is None or v > best_v + 1e-9
                    or (abs(v - best_v) <= 1e-9
                        and abs(c - mid) < abs(best_c - mid))):
                best_c, best_v = c, v
        cuts.append(best_c)
    segs = []
    start = 0
    for (lab, _a, _b), c in zip(merged, cuts + [n]):
        segs.append((lab, start, c))
        start = c
    return [s for s in segs if s[2] > s[1]]


def cut_time(window_index: int, hop_s: float = HOP_S,
             window_s: float = WINDOW_S) -> float:
    """Seconds from the utterance start of a change between window
    `window_index - 1` and `window_index`: the midpoint of their centres."""
    return window_index * hop_s + (window_s - hop_s) / 2


def _word_cut(text: str, pos: int) -> int:
    """The whitespace position nearest `pos` (never 0 or len(text))."""
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return -1
    return min(spaces, key=lambda i: abs(i - pos))


def split_text(text: str, cut_offsets: Sequence[float], duration: float,
               words: Optional[Sequence[dict]] = None) -> list:
    """`text` cut at `cut_offsets` (seconds from the utterance start).

    Returns [(piece, estimated)] with len(cut_offsets) + 1 entries, or []
    when the text cannot be cut into that many non-empty pieces.

    `words` ([{start, end, ...}] in seconds from the utterance start, the
    STT server's `words` with --word-times): when there is exactly one
    entry per whitespace token of `text`, each cut goes to the word
    boundary nearest it (within SEGMENT_SNAP_S) and nothing is marked as
    estimated -- the original text, punctuation included, is kept. In
    every other case the split is proportional by character count at the
    nearest word boundary, and the later pieces are marked estimated.
    """
    text = text.strip()
    if not text or duration <= 0:
        return []
    tokens = text.split()
    if words and len(words) == len(tokens) and len(tokens) >= 2:
        try:
            # boundary j sits between token j-1 and token j
            bounds = [(float(words[j - 1]["end"]) + float(words[j]["start"])) / 2
                      for j in range(1, len(tokens))]
        except (KeyError, TypeError, ValueError):
            bounds = []
        idx = []
        for off in cut_offsets if bounds else []:
            j = min(range(len(bounds)), key=lambda k: abs(bounds[k] - off))
            if abs(bounds[j] - off) > SEGMENT_SNAP_S:
                idx = []
                break
            idx.append(j + 1)
        if idx and idx == sorted(set(idx)):
            pieces, prev = [], 0
            for j in idx + [len(tokens)]:
                pieces.append((" ".join(tokens[prev:j]), False))
                prev = j
            if all(p for p, _ in pieces):
                return pieces
    pieces, rest_start = [], 0
    for off in cut_offsets:
        pos = int(round(len(text) * min(max(off / duration, 0.0), 1.0)))
        cut = _word_cut(text, pos)
        if cut <= rest_start:
            return []
        pieces.append(text[rest_start:cut].strip())
        rest_start = cut
    pieces.append(text[rest_start:].strip())
    if not all(pieces):
        return []
    return [(p, i > 0) for i, p in enumerate(pieces)]


class SpeakerSplitter:
    """Embeds sub-windows of one utterance and returns its speaker turns.

    `embed_fn(t_start, t_end) -> list[float]` is injectable for tests; the
    default cuts from `wav_path` and calls `client.embed()`.
    """

    def __init__(self, store, *, client=None, wav_path=None,
                 embed_fn: Optional[Callable] = None,
                 threshold: float = SPLIT_THRESHOLD,
                 min_utterance_s: float = MIN_UTTERANCE_S,
                 min_run: int = MIN_RUN, deadline_s: float = DEADLINE_S,
                 workers: int = POOL_WORKERS,
                 log: Callable[..., None] = print) -> None:
        self.store = store
        self.client = client
        self.wav_path = wav_path
        self.embed_fn = embed_fn or self._embed_from_wav
        self.threshold = threshold
        self.min_utterance_s = min_utterance_s
        self.min_run = min_run
        self.deadline_s = deadline_s
        self.log = log
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="spk-split")
        # One orchestration thread per utterance in flight, separate from
        # the window pool so an orchestrator never waits on itself.
        self._outer = ThreadPoolExecutor(max_workers=2,
                                         thread_name_prefix="spk-split-main")
        # Cost counters (numbers only): utterances looked at, embed calls.
        self.utterances = 0
        self.embed_calls = 0
        self.splits = 0
        self.fallbacks = 0

    def close(self) -> None:
        self._outer.shutdown(wait=False, cancel_futures=True)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def split_async(self, text, t_start, t_end, words=None):
        """`split()` on the orchestration thread -> Future. Lets the
        caller run the whole-utterance `resolve()` meanwhile."""
        return self._outer.submit(self.split, text, t_start, t_end, words)

    def collect(self, future, log=print):
        """The turns of a `split_async()` future, or None. Never raises.

        Waits at most the per-utterance deadline plus a small grace (the
        window calls inside already obey the deadline); logs one
        numbers-only line for a split, a timeout or an error.
        """
        try:
            turns, st = future.result(timeout=self.deadline_s + 0.5)
        except Exception as e:                # noqa: BLE001
            self.fallbacks += 1
            log(f"speaker split: timeout ({type(e).__name__})")
            return None
        if st.outcome in ("split", "timeout", "error"):
            log(f"speaker split: {st.outcome} {st.labels} "
                f"calls={st.embed_calls} {st.ms}ms")
        return turns

    def _embed_from_wav(self, t0: float, t1: float):
        if self.wav_path is None or self.client is None:
            return None
        clip = cut_clip(self.wav_path, t0, t1)
        if clip is None:
            return None
        return self.client.embed(clip)

    def _references(self) -> dict:
        persons = getattr(self.store, "_persons", {}) or {}
        return dict(persons)

    def window_scores(self, t_start: float, t_end: float,
                      stats: SplitStats) -> Optional[list]:
        refs = self._references()
        starts = window_starts(t_end - t_start)
        stats.windows = len(starts)
        if not starts:
            return None
        deadline = time.monotonic() + self.deadline_s
        futs = [self._pool.submit(self.embed_fn, t_start + s,
                                  t_start + s + WINDOW_S) for s in starts]
        stats.embed_calls = len(futs)
        self.embed_calls += len(futs)
        done, not_done = wait(futs, timeout=max(0.0, deadline - time.monotonic()))
        if not_done:
            for f in not_done:
                f.cancel()
            stats.outcome = "timeout"
            return None
        scores = []
        for f in futs:
            try:
                emb = f.result()
            except Exception:                 # noqa: BLE001
                stats.outcome = "error"
                return None
            if not emb:
                stats.outcome = "error"
                return None
            scores.append({name: cosine_similarity(emb, ref)
                           for name, ref in refs.items()})
        return scores

    def split(self, text: str, t_start, t_end,
              words: Optional[Sequence[dict]] = None):
        """[Turn, ...] with >= 2 entries, or None (keep one name).

        Returns (turns_or_None, SplitStats). Never raises.
        """
        stats = SplitStats()
        t0 = time.monotonic()
        try:
            turns = self._split(text, t_start, t_end, words, stats)
        except Exception as e:                # noqa: BLE001
            stats.outcome = "error"
            stats.extra["exc"] = type(e).__name__
            turns = None
        stats.ms = int((time.monotonic() - t0) * 1000)
        if turns is None and stats.outcome in ("timeout", "error"):
            self.fallbacks += 1
        elif turns:
            self.splits += 1
        return turns, stats

    def _split(self, text, t_start, t_end, words, stats):
        if t_start is None or t_end is None:
            stats.outcome = "short"
            return None
        duration = float(t_end) - float(t_start)
        if duration < self.min_utterance_s:
            stats.outcome = "short"
            return None
        if len(self._references()) < 2:
            stats.outcome = "single"
            return None
        self.utterances += 1
        scores = self.window_scores(float(t_start), float(t_end), stats)
        if scores is None:
            return None
        labels = label_windows(scores, self.threshold)
        stats.labels = "".join("?" if lab == UNKNOWN else lab[:1]
                               for lab in labels)
        segs = change_points(labels, scores, self.min_run)
        if len(segs) < 2:
            stats.outcome = "single"
            return None
        offsets = [cut_time(a) for _lab, a, _b in segs[1:]]
        pieces = split_text(text, offsets, duration, words)
        if len(pieces) != len(segs):
            stats.outcome = "single"
            return None
        bounds = [0.0] + offsets + [duration]
        turns = []
        for i, ((lab, _a, _b), (piece, est)) in enumerate(zip(segs, pieces)):
            turns.append(Turn(name=lab, t_start=round(t_start + bounds[i], 3),
                              t_end=round(t_start + bounds[i + 1], 3),
                              text=(f"{SPLIT_MARK} {piece}" if est else piece),
                              estimated=est))
        stats.outcome = "split"
        return turns

