#!/usr/bin/env python3
# CoThinker — topic-change hysteresis for the lane architecture
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Confirms a topic change against noise, per
`docs/kurator-kernmotivation.md`'s "not-moving needs a reason now, not
moving" and the 2026-10-20 lane concept's §"Themen-Hysterese".

Jev (`gateway/decide.py`) answers `neues_thema` and `rueckkehr_thema` as
per-block probabilities, fresh every block -- noisy on their own. This
module turns that stream into a CONFIRMED topic-change event, which is
what fires the coach lane (`gateway/lanes.py`) and what the stage shows
as "neues Thema". It deliberately does NOT live in `gateway/decider.py`:
the hysteresis rule is a lane-architecture decision, the decider's job
stays "ask the questions, hand back the numbers".

THE RULE (measured across 4 sessions, 2026-10-20 -- FINAL, per the
operator's point 10: best F1@60=0.60 against the alternatives tried;
p>=0.80/no-hold reproduces the same noise with fewer hits, p>=0.85 is
quiet but misses ~2/3 of real topics, p>=0.90+ finds almost nothing):
    A topic change is confirmed when `neues_thema` >= THRESHOLD (0.70)
    in a SINGLE block, AND the running topic is already >= MIN_TOPIC_AGE_S
    (90s) old. No consecutive-block requirement, no separate "strong"
    one-shot threshold -- both were measured away in favour of this
    simpler single-block-plus-hold rule.

    `rueckkehr_thema` reactivates the earlier topic instead of opening a
    new one -- but it is checked only AFTER the main rule, never before
    it: a high `rueckkehr_thema` must never veto a genuine new-topic
    confirmation on the same block. No new coach contribution follows a
    return.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

THRESHOLD = 0.70          # 2026-10-20, FINAL (operator's point 10)
MIN_TOPIC_AGE_S = 90.0


@dataclass(frozen=True)
class TopicDecision:
    """What this block's answers mean for the CURRENT topic.

    `changed` is true for both a freshly confirmed new topic and a
    confirmed return to an earlier one -- both end the old topic's
    coach eligibility. `coach_eligible` is true only for a genuinely new
    topic: a return must not trigger a new coach contribution (spec).
    """
    changed: bool = False
    returned: bool = False
    coach_eligible: bool = False
    topic_age_s: float = 0.0


class TopicHysteresis:
    """One instance per meeting. Feed it every block's `by_id()` answers."""

    def __init__(self, *, threshold: float = THRESHOLD,
                 min_topic_age_s: float = MIN_TOPIC_AGE_S,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.threshold = threshold
        self.min_topic_age_s = min_topic_age_s
        self.now = now
        self.topic_started_at = now()

    def on_result(self, p_neues_thema: Optional[float],
                 p_rueckkehr_thema: Optional[float]) -> TopicDecision:
        """One block's two noul probabilities -> this block's decision.

        The main rule (neues_thema) is checked first and unconditionally
        -- a high rueckkehr_thema on the very same block must never
        prevent a genuine new topic from being confirmed (operator's
        point 1: "rueckkehr_thema is not a veto on the main rule").
        """
        t = self.now()
        age = t - self.topic_started_at

        if (p_neues_thema is not None and p_neues_thema >= self.threshold
                and age >= self.min_topic_age_s):
            self.topic_started_at = t
            return TopicDecision(changed=True, returned=False,
                                 coach_eligible=True, topic_age_s=0.0)

        if p_rueckkehr_thema is not None and p_rueckkehr_thema >= self.threshold:
            self.topic_started_at = t
            return TopicDecision(changed=True, returned=True,
                                 coach_eligible=False, topic_age_s=0.0)

        return TopicDecision(topic_age_s=age)
