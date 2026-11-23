"""Fixture scenario "meeting": a full short session in time-lapse.

Invented data, no real persons. Agenda items advance, utterances carry a
speaker, a decision and an open question are raised, the speaker queue
turns over, the meeting takes a pause and tasks are named at the close.
Two of the pushed view intents (`transcript:recent`, `tasks`) are fields
the core cannot fill yet (issue #129); this fixture populates them anyway
so a surface can design against their shape, per ADR 0019 point 3.
"""
from __future__ import annotations

from surfaces.contract.fixtures import Frame
from surfaces.contract.models import (
    AgendaSnapshot,
    Decision,
    HistoryEntry,
    PromptIntent,
    Question,
    StageMain,
    StageStateSnapshot,
    StatusIntent,
    Task,
    TasksIntent,
    TimerIntent,
    TranscriptLine,
    TranscriptRecentIntent,
)

PEOPLE = ["Mira", "Jonas", "Tamar"]

_T0 = "2026-11-23T09:00:00+01:00"
_T1 = "2026-11-23T09:02:30+01:00"
_T2 = "2026-11-23T09:05:00+01:00"
_T3 = "2026-11-23T09:07:30+01:00"
_T4 = "2026-11-23T09:10:00+01:00"
_T5 = "2026-11-23T09:18:00+01:00"
_T6 = "2026-11-23T09:20:00+01:00"

_EPOCH_START = 1732352400.0  # 2026-11-23T09:00:00+01:00

_OPENING = HistoryEntry(
    id="M1", title="Opening round",
    markdown="## Opening round\n\nEveryone names one expectation for today.",
    set_at=_T0,
)
_DECISIONS_RISKS = HistoryEntry(
    id="M2", title="Decisions & risks",
    markdown="## Decisions & risks\n\nWhat do we lock in, what is still open?",
    set_at=_T2,
)
_CLOSING = HistoryEntry(
    id="M3", title="Closing round",
    markdown="## Closing round\n\nTasks and who owns them.",
    set_at=_T5,
)


def _snapshot(**overrides) -> StageStateSnapshot:
    base = dict(
        version=7,
        updated=_T0,
        mirror="",
        headline="Quarterly planning",
        main=StageMain(id="M1", markdown=_OPENING.markdown, set_at=_T0),
        history=[_OPENING],
        decisions=[],
        notices=[],
        question=None,
        links=[],
        parked=0,
        parked_entries=[],
        pending=[],
        age_visible_after_s=20,
        follow_up_after_s=300,
        image=None,
        agenda=AgendaSnapshot(
            current=1, current_text="Opening round",
            next=2, next_text="Decisions & risks",
            total=3, item_started_at=_EPOCH_START, item_budget_s=300.0,
            meeting_deadline=_EPOCH_START + 1200.0,
            people=PEOPLE, speaker="Mira", next_speaker="Jonas",
        ),
    )
    base.update(overrides)
    return StageStateSnapshot(**base)


FRAMES: list[Frame] = [
    # The meeting opens: item 1 current, Mira has the floor.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(),
        view_intents=[
            PromptIntent(id="VI1", to_person="Mira", text="What is one expectation for today?"),
            TimerIntent(id="VI2", label="Opening round", remaining_s=300.0, budget_s=300.0),
        ],
    ),
    # Mira's contribution comes in; the recent-transcript field (planned,
    # issue #129) carries it with her name attached.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(
            mirror="Mira: I'd like us to leave with one clear owner per open risk.",
        ),
        view_intents=[
            TranscriptRecentIntent(
                id="VI3",
                lines=[
                    TranscriptLine(
                        speaker="Mira", started_at=_T1,
                        text="I'd like us to leave with one clear owner per open risk.",
                    ),
                ],
            ),
            TimerIntent(id="VI2", label="Opening round", remaining_s=180.0, budget_s=300.0),
        ],
    ),
    # Agenda advances to item 2; a decision and an open question land.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(
            mirror="Jonas: Let's lock the vendor timeline now and park the rest.",
            main=StageMain(id="M2", markdown=_DECISIONS_RISKS.markdown, set_at=_T2),
            history=[_OPENING, _DECISIONS_RISKS],
            decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=_T2)],
            question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=_T2),
            agenda=AgendaSnapshot(
                current=2, current_text="Decisions & risks",
                next=3, next_text="Closing round",
                total=3, item_started_at=_EPOCH_START + 300.0, item_budget_s=300.0,
                meeting_deadline=_EPOCH_START + 1200.0,
                people=PEOPLE, speaker="Jonas", next_speaker="Tamar",
            ),
        ),
        view_intents=[
            TranscriptRecentIntent(
                id="VI4",
                lines=[
                    TranscriptLine(speaker="Mira", started_at=_T1, text="I'd like us to leave with one clear owner per open risk."),
                    TranscriptLine(speaker="Jonas", started_at=_T2, text="Let's lock the vendor timeline now and park the rest."),
                ],
            ),
        ],
    ),
    # Speaker queue turns over to Tamar; another contribution comes in.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(
            mirror="Tamar: I can take the migration runbook, but need a week.",
            main=StageMain(id="M2", markdown=_DECISIONS_RISKS.markdown, set_at=_T2),
            history=[_OPENING, _DECISIONS_RISKS],
            decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=_T2)],
            question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=_T2),
            agenda=AgendaSnapshot(
                current=2, current_text="Decisions & risks",
                next=3, next_text="Closing round",
                total=3, item_started_at=_EPOCH_START + 300.0, item_budget_s=300.0,
                meeting_deadline=_EPOCH_START + 1200.0,
                people=PEOPLE, speaker="Tamar", next_speaker="Mira",
            ),
        ),
        view_intents=[
            TranscriptRecentIntent(
                id="VI5",
                lines=[
                    TranscriptLine(speaker="Jonas", started_at=_T2, text="Let's lock the vendor timeline now and park the rest."),
                    TranscriptLine(speaker="Tamar", started_at=_T3, text="I can take the migration runbook, but need a week."),
                ],
            ),
        ],
    ),
    # A short pause.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(
            mirror="",
            main=StageMain(id="M2", markdown=_DECISIONS_RISKS.markdown, set_at=_T2),
            history=[_OPENING, _DECISIONS_RISKS],
            decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=_T2)],
            question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=_T2),
            agenda=AgendaSnapshot(
                current=2, current_text="Decisions & risks",
                next=3, next_text="Closing round",
                total=3, item_started_at=_EPOCH_START + 300.0, item_budget_s=300.0,
                meeting_deadline=_EPOCH_START + 1200.0,
                people=PEOPLE, speaker="Tamar", next_speaker="Mira",
            ),
        ),
        view_intents=[StatusIntent(id="VI6", text="Pause for coffee", severity="info")],
    ),
    # The meeting resumes at the closing round; the question is resolved
    # and tasks are named -- the tasks field (planned, issue #129) is
    # populated for the fixture.
    Frame(
        delay_s=1.5,
        snapshot=_snapshot(
            mirror="Mira: Good, let's close with who does what.",
            main=StageMain(id="M3", markdown=_CLOSING.markdown, set_at=_T5),
            history=[_OPENING, _DECISIONS_RISKS, _CLOSING],
            decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=_T2)],
            question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=_T2, state="resolved", resolution="Tamar"),
            agenda=AgendaSnapshot(
                current=3, current_text="Closing round",
                next=None, next_text=None,
                total=3, item_started_at=_EPOCH_START + 600.0, item_budget_s=300.0,
                meeting_deadline=_EPOCH_START + 1200.0,
                people=PEOPLE, speaker="Mira", next_speaker="Jonas",
            ),
        ),
        view_intents=[
            TasksIntent(
                id="VI7",
                tasks=[
                    Task(text="Send the updated timeline to the vendor.", person="Jonas", due="2026-12-01"),
                    Task(text="Draft the migration runbook.", person="Tamar", due="2026-11-30"),
                ],
            ),
        ],
    ),
]
