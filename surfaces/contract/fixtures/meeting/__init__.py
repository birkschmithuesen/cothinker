"""Fixture scenario "meeting": a full short session in time-lapse.

Invented data, no real persons. Agenda items advance, utterances carry a
speaker, a decision and an open question are raised, the speaker queue
turns over, the meeting takes a pause and tasks are named at the close.
Two of the pushed view intents (`transcript:recent`, `tasks`) are fields
the core cannot fill yet (issue #129); this fixture populates them anyway
so a surface can design against their shape, per ADR 0019 point 3.

The speaker queue (`StageStateSnapshot.queue`, the speaker-queue block's
`queue:room` payload) turns over in step with `agenda.speaker` /
`agenda.next_speaker` -- it is the same queue, viewed through the
`speaker-queue` block's own wire shape rather than the agenda's.

`StageStateSnapshot.voice` (the voice-attribution block's `voice:room`
payload, issue #164) turns over on its OWN schedule, deliberately out of
step with the queue: `voice.speaking` is who is actually talking right
now, by track/STT attribution, independent of who holds the floor by
tapping to join the queue -- the point the fixture is meant to make
legible.

All wall-clock labels and epoch times are built relative to the moment
the scenario is loaded (`build_frames`'s `now`, default `time.time()`),
not to a fixed past date -- a hardcoded date drifts out of the future
and the item's remaining time overruns (issue #147 point 2).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from surfaces.contract.fixtures import Frame
from surfaces.contract.models import (
    AgendaItemSummary,
    AgendaSnapshot,
    Decision,
    HistoryEntry,
    KnowledgeHit,
    KnowledgeIntent,
    KnowledgeSnapshot,
    ListenClose,
    ListenIntent,
    ListenSnapshot,
    PromptIntent,
    QueueSeat,
    QueueSnapshot,
    Question,
    StageMain,
    StageStateSnapshot,
    StatusIntent,
    Task,
    TasksIntent,
    TimerIntent,
    TranscriptLine,
    TranscriptRecentIntent,
    VoiceIntent,
    VoiceSnapshot,
)

PEOPLE = ["Mira", "Jonas", "Tamar"]

_AGENDA_TITLES = ["Opening round", "Decisions & risks", "Closing round"]

# Participant ids (speaker-queue block terms: `pid`, resolved to a display
# `name` via a `participant:name` edit -- a fixture just fixes both up front).
_PIDS = {"Mira": "p-mira", "Jonas": "p-jonas", "Tamar": "p-tamar"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _agenda_items(current: int) -> list[AgendaItemSummary]:
    """The whole agenda list, `current`'s item marked accordingly (issue
    #147 point 4: a fixture must fill this, not leave it empty)."""
    items = []
    for position, title in enumerate(_AGENDA_TITLES, start=1):
        if position < current:
            status = "done"
        elif position == current:
            status = "current"
        elif position == current + 1:
            status = "next"
        else:
            status = "open"
        items.append(AgendaItemSummary(title=title, budget_s=300.0, status=status))
    return items


def _seat(person: str) -> QueueSeat:
    return QueueSeat(pid=_PIDS[person], name=person)


def _queue(speaking: str, waiting: list[str]) -> QueueSnapshot:
    return QueueSnapshot(speaking=_seat(speaking), waiting=[_seat(p) for p in waiting])


def _voice(speaking: str | None, since: str | None, recent: list[tuple[str, str]]) -> VoiceSnapshot:
    return VoiceSnapshot(
        speaking=speaking, since=since,
        recent=[{"name": name, "last_at": last_at} for name, last_at in recent],
    )


# Invented notes for the "Decisions & risks" topic (issue #148): what the
# project already knows, found by the knowledge-link block from the notes
# themselves (name/tags in the topic title, plus link neighbours) -- no
# model, no network.
_KNOWLEDGE_HITS = [
    KnowledgeHit(kind="contract", title="Vendor timeline", href="contracts/vendor-timeline.md"),
    KnowledgeHit(kind="ops", title="Migration runbook", href="ops/migration-runbook.md"),
]

def build_frames(now: float | None = None) -> list[Frame]:
    """The scenario's frames, with every wall-clock label and epoch time
    anchored to `now` (default: the moment this is called)."""
    now = time.time() if now is None else now

    t0 = _iso(now)
    t1 = _iso(now + 150.0)
    t2 = _iso(now + 300.0)
    t3 = _iso(now + 450.0)
    t5 = _iso(now + 1080.0)

    opening = HistoryEntry(
        id="M1", title="Opening round",
        markdown="## Opening round\n\nEveryone names one expectation for today.",
        set_at=t0,
    )
    decisions_risks = HistoryEntry(
        id="M2", title="Decisions & risks",
        markdown="## Decisions & risks\n\nWhat do we lock in, what is still open?",
        set_at=t2,
    )
    closing = HistoryEntry(
        id="M3", title="Closing round",
        markdown="## Closing round\n\nTasks and who owns them.",
        set_at=t5,
    )

    def snapshot(**overrides) -> StageStateSnapshot:
        base = dict(
            version=7,
            updated=t0,
            mirror="",
            headline="Quarterly planning",
            main=StageMain(id="M1", markdown=opening.markdown, set_at=t0),
            history=[opening],
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
                total=3, item_started_at=now, item_budget_s=300.0,
                meeting_deadline=now + 1200.0,
                people=PEOPLE, speaker="Mira", next_speaker="Jonas",
                items=_agenda_items(1),
            ),
            queue=_queue("Mira", ["Jonas", "Tamar"]),
            voice=_voice("Jonas", t0, []),
            listen=ListenSnapshot(),
            tasks=[],
            transcript_recent=[],
        )
        base.update(overrides)
        return StageStateSnapshot(**base)

    transcript_after_mira = [
        TranscriptLine(speaker="Mira", started_at=t1, text="I'd like us to leave with one clear owner per open risk."),
    ]
    transcript_after_jonas = transcript_after_mira + [
        TranscriptLine(speaker="Jonas", started_at=t2, text="Let's lock the vendor timeline now and park the rest."),
    ]
    transcript_after_tamar = [
        TranscriptLine(speaker="Jonas", started_at=t2, text="Let's lock the vendor timeline now and park the rest."),
        TranscriptLine(speaker="Tamar", started_at=t3, text="I can take the migration runbook, but need a week."),
    ]
    tasks_named = [
        Task(text="Send the updated timeline to the vendor.", person="Jonas", due="2026-12-01"),
        Task(text="Draft the migration runbook.", person="Tamar", due="2026-11-30"),
    ]

    return [
        # The meeting opens: item 1 current, Mira has the floor.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(),
            view_intents=[
                PromptIntent(id="VI1", to_person="Mira", text="What is one expectation for today?"),
                TimerIntent(id="VI2", label="Opening round", remaining_s=300.0, budget_s=300.0, items=_agenda_items(1)),
                VoiceIntent(id="voice:room", speaking="Jonas", since=t0, recent=[]),
            ],
        ),
        # Mira's contribution comes in; the recent-transcript field (planned,
        # issue #129) carries it with her name attached.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(
                mirror="Mira: I'd like us to leave with one clear owner per open risk.",
                transcript_recent=transcript_after_mira,
                voice=_voice("Tamar", t1, [("Jonas", t0)]),
            ),
            view_intents=[
                TranscriptRecentIntent(id="VI3", lines=transcript_after_mira),
                TimerIntent(id="VI2", label="Opening round", remaining_s=180.0, budget_s=300.0, items=_agenda_items(1)),
                VoiceIntent(id="voice:room", speaking="Tamar", since=t1, recent=[{"name": "Jonas", "last_at": t0}]),
            ],
        ),
        # Agenda advances to item 2; a decision and an open question land.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(
                mirror="Jonas: Let's lock the vendor timeline now and park the rest.",
                main=StageMain(id="M2", markdown=decisions_risks.markdown, set_at=t2),
                history=[opening, decisions_risks],
                decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=t2)],
                question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=t2),
                agenda=AgendaSnapshot(
                    current=2, current_text="Decisions & risks",
                    next=3, next_text="Closing round",
                    total=3, item_started_at=now + 300.0, item_budget_s=300.0,
                    meeting_deadline=now + 1200.0,
                    people=PEOPLE, speaker="Jonas", next_speaker="Tamar",
                    items=_agenda_items(2),
                ),
                queue=_queue("Jonas", ["Tamar", "Mira"]),
                voice=_voice("Mira", t2, [("Tamar", t1), ("Jonas", t0)]),
                listen=ListenSnapshot(state="listening", since=now + 300.0, opened_by="voice", closes_in_s=8.0),
                transcript_recent=transcript_after_jonas,
                knowledge=KnowledgeSnapshot(topic_id="M2", query="Decisions & risks", hits=_KNOWLEDGE_HITS),
            ),
            view_intents=[
                TranscriptRecentIntent(id="VI4", lines=transcript_after_jonas),
                VoiceIntent(id="voice:room", speaking="Mira", since=t2,
                           recent=[{"name": "Tamar", "last_at": t1}, {"name": "Jonas", "last_at": t0}]),
                KnowledgeIntent(id="knowledge:M2", topic_id="M2", query="Decisions & risks", hits=_KNOWLEDGE_HITS),
                # Jonas calls Robo to lock the vendor timeline ("Hey Robo,
                # ..."); the call window opens (issue #180, ADR 0023).
                ListenIntent(id="listen:room", state="listening", since=now + 300.0,
                            opened_by="voice", closes_in_s=8.0),
            ],
        ),
        # Speaker queue turns over to Tamar; another contribution comes in.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(
                mirror="Tamar: I can take the migration runbook, but need a week.",
                main=StageMain(id="M2", markdown=decisions_risks.markdown, set_at=t2),
                history=[opening, decisions_risks],
                decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=t2)],
                question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=t2),
                agenda=AgendaSnapshot(
                    current=2, current_text="Decisions & risks",
                    next=3, next_text="Closing round",
                    total=3, item_started_at=now + 300.0, item_budget_s=300.0,
                    meeting_deadline=now + 1200.0,
                    people=PEOPLE, speaker="Tamar", next_speaker="Mira",
                    items=_agenda_items(2),
                ),
                queue=_queue("Tamar", ["Mira", "Jonas"]),
                voice=_voice("Tamar", t3, [("Mira", t2), ("Jonas", t0)]),
                listen=ListenSnapshot(last_close=ListenClose(reason="end", at=now + 450.0)),
                transcript_recent=transcript_after_tamar,
                knowledge=KnowledgeSnapshot(topic_id="M2", query="Decisions & risks", hits=_KNOWLEDGE_HITS),
            ),
            view_intents=[
                TranscriptRecentIntent(id="VI5", lines=transcript_after_tamar),
                VoiceIntent(id="voice:room", speaking="Tamar", since=t3,
                           recent=[{"name": "Mira", "last_at": t2}, {"name": "Jonas", "last_at": t0}]),
                # "Robo, Ende" closes the call window the previous frame
                # opened.
                ListenIntent(id="listen:room", state="idle",
                            last_close=ListenClose(reason="end", at=now + 450.0)),
            ],
        ),
        # A short pause.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(
                mirror="",
                main=StageMain(id="M2", markdown=decisions_risks.markdown, set_at=t2),
                history=[opening, decisions_risks],
                decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=t2)],
                question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=t2),
                agenda=AgendaSnapshot(
                    current=2, current_text="Decisions & risks",
                    next=3, next_text="Closing round",
                    total=3, item_started_at=now + 300.0, item_budget_s=300.0,
                    meeting_deadline=now + 1200.0,
                    people=PEOPLE, speaker="Tamar", next_speaker="Mira",
                    items=_agenda_items(2),
                ),
                queue=_queue("Tamar", ["Mira", "Jonas"]),
                voice=_voice("Tamar", t3, [("Mira", t2), ("Jonas", t0)]),
                transcript_recent=transcript_after_tamar,
                knowledge=KnowledgeSnapshot(topic_id="M2", query="Decisions & risks", hits=_KNOWLEDGE_HITS),
            ),
            view_intents=[StatusIntent(id="VI6", text="Pause for coffee", severity="info")],
        ),
        # The meeting resumes at the closing round; the question is resolved
        # and tasks are named -- the tasks field (planned, issue #129) is
        # populated for the fixture.
        Frame(
            delay_s=1.5,
            snapshot=snapshot(
                mirror="Mira: Good, let's close with who does what.",
                main=StageMain(id="M3", markdown=closing.markdown, set_at=t5),
                history=[opening, decisions_risks, closing],
                decisions=[Decision(id="D1", time="from next week", text="We lock the vendor timeline as proposed.", added_at=t2)],
                question=Question(id="Q1", text="Who owns the migration runbook?", raised_at=t2, state="resolved", resolution="Tamar"),
                agenda=AgendaSnapshot(
                    current=3, current_text="Closing round",
                    next=None, next_text=None,
                    total=3, item_started_at=now + 600.0, item_budget_s=300.0,
                    meeting_deadline=now + 1200.0,
                    people=PEOPLE, speaker="Mira", next_speaker="Jonas",
                    items=_agenda_items(3),
                ),
                transcript_recent=transcript_after_tamar,
                tasks=tasks_named,
                voice=_voice(None, None, [("Tamar", t3), ("Mira", t2), ("Jonas", t0)]),
            ),
            view_intents=[
                TasksIntent(id="VI7", tasks=tasks_named),
                VoiceIntent(id="voice:room", speaking=None, since=None,
                           recent=[{"name": "Tamar", "last_at": t3}, {"name": "Mira", "last_at": t2},
                                   {"name": "Jonas", "last_at": t0}]),
            ],
        ),
    ]
