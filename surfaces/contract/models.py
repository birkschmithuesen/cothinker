"""Pydantic models for the surface contract (ADR 0019).

Two payload families, mirroring the real wire shapes:

- The **stage-state snapshot** (`StageStateSnapshot`): the content fields of
  `buehne.json`, produced today by `gateway.stage_state.StageState.content()`
  and `gateway.stage_state.agenda_payload()`. Field names match the JSON
  keys the stage already sends, not a renamed abstraction.
- **View intents** and **user actions** (ADR 0006): what blocks and the
  phase engine emit toward a surface, and what a surface sends back.

A field the core cannot fill yet stays in the schema (ADR 0019 point 3),
carries an example, and is marked with the `x-status: planned` extra plus
the issue that will fill it (see `ISSUE_SURFACE_API_GAPS`).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

ISSUE_SURFACE_API_GAPS = "https://github.com/birkschmithuesen/cothinker/issues/129"


def _planned(issue: str = ISSUE_SURFACE_API_GAPS) -> dict:
    """The `json_schema_extra` for a field or model the core cannot fill yet."""
    return {"x-status": "planned", "x-issue": issue}


# --------------------------------------------------------------------------
# Stage-state snapshot (gateway/stage_state.py: StageState.content())
# --------------------------------------------------------------------------


class StageMain(BaseModel):
    """The surface currently shown, `StageState.content()`'s `main` field."""

    id: str = Field(description="Identity of the current surface.", examples=["M7"])
    markdown: str = Field(
        description="The markdown body shown as the main surface.",
        examples=["## Opening round\n\nEveryone names one expectation."],
    )
    set_at: str = Field(
        description="Wall-clock label of when this surface was set; the"
        " reader computes the age itself, it is never baked into the"
        " payload.",
        examples=["2026-11-23T09:00:00+01:00"],
    )
    collapsed_lines: int = Field(
        default=0,
        description="Number of markdown lines folded away by the collapse view.",
        examples=[0],
    )
    collapsed_markdown: str = Field(
        default="",
        description="The folded-away markdown, shown on request.",
        examples=[""],
    )


class HistoryEntry(BaseModel):
    """One position in `history`: every surface shown so far, in full."""

    id: str = Field(description="Identity of this history position.", examples=["M5"])
    title: str = Field(
        description="Short label the history is navigated by.",
        examples=["Opening round"],
    )
    markdown: str = Field(
        description="The markdown body of this position.",
        examples=["## Opening round\n\nEveryone names one expectation."],
    )
    set_at: str = Field(
        description="Wall-clock label of when this position was set.",
        examples=["2026-11-23T09:00:00+01:00"],
    )
    collapsed_lines: int = Field(default=0, description="Folded-away line count.", examples=[0])
    collapsed_markdown: str = Field(default="", description="Folded-away markdown.", examples=[""])
    kind: Literal["markdown", "graph"] = Field(
        default="markdown",
        description="`markdown` for a normal position, `graph` for a session-graph position.",
        examples=["markdown"],
    )
    graph: dict | None = Field(
        default=None,
        description="Nodes and edges when `kind` is `graph`; an older graph"
        " position keeps only its `seed`, the newest keeps the full drawing.",
        examples=[None],
    )


class Decision(BaseModel):
    id: str = Field(description="Identity of this decision.", examples=["D3"])
    time: str = Field(description="When the decision takes effect, as named by the round.", examples=["from next week"])
    text: str = Field(description="The decision text.", examples=["We ship the public repo split first."])
    added_at: str = Field(
        description="Wall-clock label of when this decision was added; used"
        " for the few seconds of a 'new' mark, computed by the reader.",
        examples=["2026-11-23T09:12:00+01:00"],
    )


class Notice(BaseModel):
    id: str = Field(description="Identity of this notice.", examples=["N2"])
    kind: str = Field(description="The notice kind, chosen by the raising block.", examples=["risk"])
    text: str = Field(description="The notice text.", examples=["The vendor contract still needs signature."])
    raised_at: str = Field(description="Wall-clock label of when the notice was raised.", examples=["2026-11-23T09:05:00+01:00"])
    state: Literal["open", "resolved", "expired"] = Field(default="open", examples=["open"])
    resolution: str = Field(default="", description="How the notice was resolved, if it was.", examples=[""])
    follow_up: bool = Field(default=False, description="Whether this notice needs a follow-up after it ages out.", examples=[False])


class Question(BaseModel):
    id: str = Field(description="Identity of this open question.", examples=["Q1"])
    text: str = Field(description="The question text.", examples=["Who owns the migration runbook?"])
    raised_at: str = Field(description="Wall-clock label of when the question was raised.", examples=["2026-11-23T09:07:00+01:00"])
    state: Literal["open", "resolved", "expired"] = Field(default="open", examples=["open"])
    resolution: str = Field(default="", description="The answer, once resolved.", examples=[""])
    follow_up: bool = Field(default=False, description="Whether this question needs a follow-up after it ages out.", examples=[False])


class ParkedEntry(BaseModel):
    """A notice or question that found no free slot; it carries no identity."""

    text: str = Field(description="What was parked.", examples=["Should we invite the vendor to the next round?"])
    parked_at: str = Field(description="Wall-clock label of when it was parked.", examples=["2026-11-23T09:20:00+01:00"])


class PendingAnnouncement(BaseModel):
    """Something of `kind` is on its way to the stage."""

    id: str = Field(description="Identity pairing this announcement's start with its end.", examples=["P1"])
    kind: str = Field(description="What is coming, chosen by the calling code.", examples=["decision"])
    label: str = Field(description="Short label shown while the announcement is pending.", examples=["noting a decision..."])
    since: str = Field(description="Wall-clock label of when the announcement started.", examples=["2026-11-23T09:11:55+01:00"])


class StageImage(BaseModel):
    id: str = Field(description="Identity of the requested image.", examples=["IMG1"])
    caption: str = Field(description="Caption shown with the image.", examples=["Session graph, round 2"])
    set_at: str = Field(description="Wall-clock label of when the image was set.", examples=["2026-11-23T09:30:00+01:00"])


class AgendaItemSummary(BaseModel):
    """One item of the full agenda list -- not wired yet, see issue #129."""

    title: str = Field(description="The agenda item's title.", examples=["Retro: what worked"])
    budget_s: float | None = Field(default=None, description="Planned duration in seconds.", examples=[600.0])
    status: Literal["done", "current", "next", "open"] = Field(
        description="Where this item stands relative to the current one.",
        examples=["current"],
    )


class AgendaSnapshot(BaseModel):
    """`gateway.stage_state.agenda_payload()`'s result; `None` for a session without `agenda.md`."""

    current: int = Field(description="1-based position of the current item; 0 when there are no items.", examples=[2])
    current_text: str = Field(description="Title of the current item.", examples=["Retro: what worked"])
    next: int | None = Field(default=None, description="1-based position of the next item, if any.", examples=[3])
    next_text: str | None = Field(default=None, description="Title of the next item, if any.", examples=["Retro: what to change"])
    total: int = Field(description="Total number of agenda items.", examples=[5])
    item_started_at: float = Field(
        description="Epoch seconds the current item started; the reader"
        " computes the live countdown against `meeting_deadline` itself.",
        examples=[1732352400.0],
    )
    item_budget_s: float | None = Field(default=None, description="Budget of the current item in seconds.", examples=[600.0])
    meeting_deadline: float | None = Field(default=None, description="Epoch seconds the whole meeting is due to end.", examples=[1732356000.0])
    people: list[str] = Field(default_factory=list, description="The speaker queue, in order.", examples=[["Mira", "Jonas", "Tamar"]])
    speaker: str | None = Field(default=None, description="Who is currently speaking, if the queue is non-empty.", examples=["Mira"])
    next_speaker: str | None = Field(default=None, description="Who speaks next, if the queue has more than one person.", examples=["Jonas"])
    items: list[AgendaItemSummary] | None = Field(
        default=None,
        description="The whole agenda as a list, not just current/next; the"
        " core does not wire this up yet (issue #129). A fixture populates"
        " it so a surface can design against it.",
        examples=[None],
        json_schema_extra=_planned(),
    )


class StageStateSnapshot(BaseModel):
    """The content fields of `buehne.json`; the consumer adds `status`."""

    version: int = Field(description="Schema version of the stage file.", examples=[7])
    updated: str = Field(description="Wall-clock label of the last update.", examples=["2026-11-23T09:30:05+01:00"])
    mirror: str = Field(description="The live transcript mirror text.", examples=["...and then we agreed on the timeline."])
    call_answer: str = Field(default="", description="The last call-and-answer line, if any.", examples=[""])
    headline: str = Field(default="", description="The session headline.", examples=["Quarterly planning"])
    main: StageMain
    history: list[HistoryEntry] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
    question: Question | None = Field(default=None, description="The single open-question slot; `None` when empty.")
    links: list[str] = Field(default_factory=list, description="Links shown alongside the stage.", examples=[["https://example.invalid/agenda"]])
    parked: int = Field(description="Count of notices/questions parked for lack of a free slot.", examples=[1])
    parked_entries: list[ParkedEntry] = Field(default_factory=list)
    pending: list[PendingAnnouncement] = Field(default_factory=list)
    age_visible_after_s: int = Field(description="Seconds before an entry's age becomes visible.", examples=[20])
    follow_up_after_s: int = Field(description="Seconds before an open notice/question needs a follow-up mark.", examples=[300])
    image: StageImage | None = Field(default=None, description="An image on request, by id; `None` otherwise.")
    agenda: AgendaSnapshot | None = Field(default=None, description="`None` for a session without `agenda.md`.")


# --------------------------------------------------------------------------
# View intents (ADR 0006: blocks and the phase engine emit these)
# --------------------------------------------------------------------------


class PromptIntent(BaseModel):
    kind: Literal["prompt"] = "prompt"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI1"])
    to_person: str | None = Field(default=None, description="Who the prompt is directed at; `None` is everyone.", examples=["Mira"])
    text: str = Field(description="The prompt text.", examples=["What is one expectation for today?"])


class ChoiceIntent(BaseModel):
    kind: Literal["choice"] = "choice"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI2"])
    to_person: str | None = Field(default=None, description="Who the choice is directed at; `None` is everyone.", examples=["Jonas"])
    question: str = Field(description="The question the choices answer.", examples=["Which topic next?"])
    options: list[str] = Field(description="The offered choices, in display order.", examples=[["Budget", "Timeline", "Risks"]])
    allow_multiple: bool = Field(default=False, description="Whether more than one option may be chosen.", examples=[False])


class TimerIntent(BaseModel):
    kind: Literal["timer"] = "timer"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI3"])
    label: str = Field(description="Label shown next to the timer.", examples=["Retro: what worked"])
    remaining_s: float = Field(description="Seconds remaining on this timer.", examples=[480.0])
    budget_s: float | None = Field(default=None, description="The timer's total budget in seconds.", examples=[600.0])
    items: list[AgendaItemSummary] | None = Field(
        default=None,
        description="The whole agenda as a list of items, not just the"
        " current one; the core does not wire this up yet (issue #129).",
        examples=[None],
        json_schema_extra=_planned(),
    )


class StatusIntent(BaseModel):
    kind: Literal["status"] = "status"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI4"])
    text: str = Field(description="The status line text.", examples=["Recording"])
    severity: Literal["info", "warning", "error"] = Field(default="info", examples=["info"])


class ArtefactIntent(BaseModel):
    kind: Literal["artefact"] = "artefact"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI5"])
    label: str = Field(description="Label for the artefact.", examples=["Session graph, round 2"])
    url: str = Field(description="Where the artefact can be fetched.", examples=["https://example.invalid/artefacts/graph-2.png"])
    media_type: str = Field(description="MIME type of the artefact.", examples=["image/png"])


class TranscriptLine(BaseModel):
    speaker: str = Field(default="", description="Who said it; empty when attribution is unsure.", examples=["Tamar"])
    started_at: str = Field(description="Wall-clock label of when the line started.", examples=["2026-11-23T09:14:02+01:00"])
    text: str = Field(description="The transcribed text.", examples=["Let's park the vendor question for now."])


class TranscriptRecentIntent(BaseModel):
    """The last three contributions. Not wired yet, see issue #129."""

    model_config = ConfigDict(json_schema_extra=_planned())

    kind: Literal["transcript:recent"] = "transcript:recent"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI6"])
    lines: list[TranscriptLine] = Field(description="The last three contributions, oldest first.", examples=[[]])


class Task(BaseModel):
    text: str = Field(description="The task text.", examples=["Send the updated timeline to the vendor."])
    person: str = Field(default="", description="Who owns it; empty when the meeting did not name anyone.", examples=["Jonas"])
    due: str = Field(default="", description="Due date, as named by the meeting; empty when not named.", examples=["2026-12-01"])


class TasksIntent(BaseModel):
    """Tasks named by the meeting, from `decisions-tasks-questions`. Not wired yet, see issue #129."""

    model_config = ConfigDict(json_schema_extra=_planned())

    kind: Literal["tasks"] = "tasks"
    id: str = Field(description="Identity a user action refers back to.", examples=["VI7"])
    tasks: list[Task] = Field(description="Tasks named by the meeting so far.", examples=[[]])


ViewIntent = Annotated[
    Union[
        PromptIntent,
        ChoiceIntent,
        TimerIntent,
        StatusIntent,
        ArtefactIntent,
        TranscriptRecentIntent,
        TasksIntent,
    ],
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------
# User actions (ADR 0006: a surface sends these back)
# --------------------------------------------------------------------------


class ChooseAction(BaseModel):
    kind: Literal["choose"] = "choose"
    intent_id: str = Field(description="The choice intent this responds to.", examples=["VI2"])
    option: str = Field(description="The chosen option.", examples=["Timeline"])


class AcceptAction(BaseModel):
    kind: Literal["accept"] = "accept"
    intent_id: str = Field(description="The intent being accepted.", examples=["VI1"])


class RejectAction(BaseModel):
    kind: Literal["reject"] = "reject"
    intent_id: str = Field(description="The intent being rejected.", examples=["VI1"])
    reason: str | None = Field(default=None, description="Why it was rejected, if given.", examples=[None])


class EditAction(BaseModel):
    kind: Literal["edit"] = "edit"
    intent_id: str = Field(description="The intent whose text is being edited.", examples=["VI1"])
    text: str = Field(description="The edited text.", examples=["What is one worry for today?"])


class TapAction(BaseModel):
    kind: Literal["tap"] = "tap"
    intent_id: str | None = Field(default=None, description="The intent tapped, if any; a navigation tap has none.", examples=["VI4"])
    target: str = Field(description="What was tapped.", examples=["status-badge"])


class VoiceAction(BaseModel):
    kind: Literal["voice"] = "voice"
    intent_id: str | None = Field(default=None, description="The intent the spoken command responds to, if any.", examples=["VI2"])
    text: str = Field(description="What was recognised.", examples=["timeline"])
    confidence: float | None = Field(default=None, description="Recognition confidence, 0..1, if known.", examples=[0.92])


UserAction = Annotated[
    Union[ChooseAction, AcceptAction, RejectAction, EditAction, TapAction, VoiceAction],
    Field(discriminator="kind"),
]
