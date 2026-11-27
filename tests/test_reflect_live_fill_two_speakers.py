# CoThinker — live-mode fill check: queue, share and voice from an
# attributed two-speaker transcript (issue #178)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The deployment acceptance run that opened issue #178 found `queue`,
`share` and `voice.speaking` staying `null` in a live `buehne.json`: the
Reflect template named `timer`, `speaker-queue` and `speaking-share` but
the old hand-kept block list in `gateway/block_host_wiring.py` never carried them (fixed by the
two prior commits on this branch), and `voice-attribution` was never named
by the template at all (also fixed).

This test drives the real block host (same pieces `test_reflect_run.py`
already assembles -- `run_session()`, reused here rather than a second
harness) with a SMALL, two-speaker fixture carrying the exact sentences
issue #178 quotes (a decision: "Wir machen das so: Werkstatt am Freitag";
a task: "Sam, bis Donnerstag Material bestellen") and asserts `queue`,
`share` and `voice.speaking` actually come out filled from it.

`decisions` and `tasks` are NOT asserted here -- see this branch's own
summary for why (a `--lanes` / `--policy` wiring gap and a `tasks` field
that, per `surfaces/contract/models.py`, was never wired into
`buehne.json` at all, both genuine design questions for the maintainer,
not a narrow wiring bug this card's test can responsibly paper over).

    python3 -m pytest -q tests/test_reflect_live_fill_two_speakers.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reflect_run import FULL_REGISTRY, TEMPLATE_PATH, run_session  # noqa: E402
from surfaces.contract.models import StageStateSnapshot  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "reflect_two_speakers.jsonl"


def _load_fixture() -> list[dict]:
    lines = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_fixture_carries_the_decision_and_task_sentences_from_two_speakers():
    """Red-before-everything sanity check on the fixture itself, so a
    broken fixture file fails here first rather than inside the real test
    below with a confusing message."""
    fixture = _load_fixture()
    assert {line["speaker"] for line in fixture} == {"Alex", "Sam"}
    texts = [line["text"] for line in fixture]
    assert "Wir machen das so: Werkstatt am Freitag." in texts
    assert "Sam, bis Donnerstag Material bestellen." in texts


def test_queue_share_and_voice_speaking_fill_from_the_attributed_transcript(tmp_path):
    fixture = _load_fixture()

    result = run_session(tmp_path / "session", TEMPLATE_PATH, FULL_REGISTRY, fixture)

    assert {"speaker-queue", "speaking-share", "voice-attribution"} <= result.produced

    snapshot = StageStateSnapshot.model_validate(result.snapshot)

    assert snapshot.queue is not None
    assert snapshot.queue.speaking is not None or snapshot.queue.waiting

    assert snapshot.share is not None
    words = {row.person: row.words for row in snapshot.share.shares}
    assert words.get("Alex", 0) > 0
    assert words.get("Sam", 0) > 0

    assert snapshot.voice is not None
    assert snapshot.voice.speaking in ("Alex", "Sam")
