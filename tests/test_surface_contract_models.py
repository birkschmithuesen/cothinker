"""Tests for the surface contract's wire vocabulary (issue #132 review
finding): `surfaces/contract/models.py` must mirror the live kind names
and fields used on `main` by `shared/surface.py` (`INTENT_KINDS`,
`ACTION_KINDS`), `gateway/stage_state.py::StageState._content()` and the
block emitters in `blocks/speaker_queue/`, `blocks/speaking_share/` and
`blocks/graph/` -- not an invented, renamed vocabulary.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from surfaces.contract.models import (
    ChoiceIntent,
    GraphIntent,
    KnowledgeHit,
    KnowledgeIntent,
    MessageAction,
    QueueIntent,
    QueueSeat,
    QueueSnapshot,
    ShareIntent,
    ShareRow,
    ShareSnapshot,
    SessionGraph,
    SessionGraphEdge,
    SessionGraphNode,
    StageStateSnapshot,
    Task,
    TranscriptLine,
    UserAction,
    ViewIntent,
    VoiceIntent,
    VoiceRecentEntry,
    VoiceSnapshot,
)


def test_choice_intent_kind_is_choice_list_not_choice():
    assert ChoiceIntent.model_fields["kind"].default == "choice_list"
    intent = ChoiceIntent(id="VI2", question="Which topic?", options=["A", "B"])
    assert intent.kind == "choice_list"


def test_choice_intent_rejects_the_old_choice_kind():
    with pytest.raises(Exception):
        ChoiceIntent(kind="choice", id="VI2", question="Which topic?", options=["A", "B"])


def test_message_action_matches_the_message_action_test_shape():
    action = MessageAction(intent_id="VI1", person="P3", text="hello")
    assert action.kind == "message"
    assert action.person == "P3"
    assert action.text == "hello"


def test_user_action_union_accepts_message_kind():
    parsed = TypeAdapter(UserAction).validate_python(
        {"kind": "message", "intent_id": "VI1", "person": "P3", "text": "hello"}
    )
    assert isinstance(parsed, MessageAction)


def test_stage_state_snapshot_queue_share_session_graph_default_to_none():
    assert StageStateSnapshot.model_fields["queue"].default is None
    assert StageStateSnapshot.model_fields["share"].default is None
    assert StageStateSnapshot.model_fields["session_graph"].default is None
    assert StageStateSnapshot.model_fields["knowledge"].default is None


def test_queue_snapshot_matches_speaker_queue_block_payload():
    snapshot = QueueSnapshot(
        mode="tap",
        speaking=QueueSeat(pid="P1", name="Mira"),
        waiting=[QueueSeat(pid="P2", name="Jonas")],
        round=None,
    )
    assert snapshot.model_dump() == {
        "mode": "tap",
        "speaking": {"pid": "P1", "name": "Mira"},
        "waiting": [{"pid": "P2", "name": "Jonas"}],
        "round": None,
    }


def test_share_snapshot_matches_speaking_share_block_payload():
    snapshot = ShareSnapshot(
        shares=[ShareRow(person="P1", role_id=None, words=123, share=0.31)],
        total_words=400,
    )
    assert snapshot.model_dump() == {
        "shares": [{"person": "P1", "role_id": None, "words": 123, "share": 0.31, "given": None}],
        "total_words": 400,
    }


def test_session_graph_matches_graph_block_payload():
    graph = SessionGraph(
        seed="file:protokoll.md",
        nodes=[SessionGraphNode(
            id="file:protokoll.md", label="Sitzung", kind="session", ring=0,
            href="", time="", tag="", parent="", hidden=False, grace=False,
        )],
        edges=[SessionGraphEdge(
            id="e:1", source="file:protokoll.md", target="t1", kind="entry", ring=0,
        )],
        hidden=0,
    )
    assert graph.nodes[0].kind == "session"
    assert graph.edges[0].kind == "entry"


def test_view_intent_union_accepts_queue_and_share_kinds():
    queue_intent = TypeAdapter(ViewIntent).validate_python({
        "kind": "queue", "id": "queue:room", "mode": "tap",
        "speaking": None, "waiting": [], "round": None,
    })
    assert isinstance(queue_intent, QueueIntent)

    share_intent = TypeAdapter(ViewIntent).validate_python({
        "kind": "share", "id": "share:room", "shares": [], "total_words": 0,
    })
    assert isinstance(share_intent, ShareIntent)


def test_stage_state_snapshot_has_tasks_and_transcript_recent_fields():
    """issue #147 point 3: `tasks` and `transcript:recent` existed only as
    events, not in the snapshot -- a surface loaded mid-session saw
    nothing until the next event. Additive fields, default to empty."""
    assert StageStateSnapshot.model_fields["tasks"].default_factory() == []
    assert StageStateSnapshot.model_fields["transcript_recent"].default_factory() == []

    snapshot = StageStateSnapshot.model_construct(
        tasks=[Task(text="Draft the migration runbook.", person="Tamar", due="")],
        transcript_recent=[TranscriptLine(speaker="Mira", started_at="2026-11-23T09:00:00+01:00", text="hi")],
    )
    assert snapshot.tasks[0].person == "Tamar"
    assert snapshot.transcript_recent[0].speaker == "Mira"


def test_stage_state_snapshot_voice_defaults_to_none():
    """issue #164: `voice` is a separate field from `queue` -- who is
    talking right now by voice/track attribution, not who holds the
    floor by tapping to join the speaker queue."""
    assert StageStateSnapshot.model_fields["voice"].default is None


def test_voice_snapshot_matches_the_voice_attribution_block_payload():
    snapshot = VoiceSnapshot(
        speaking="Mira",
        since="2026-11-23T09:12:00+01:00",
        recent=[VoiceRecentEntry(name="Jonas", last_at="2026-11-23T09:10:00+01:00")],
    )
    assert snapshot.model_dump() == {
        "speaking": "Mira",
        "since": "2026-11-23T09:12:00+01:00",
        "recent": [{"name": "Jonas", "last_at": "2026-11-23T09:10:00+01:00"}],
    }


def test_voice_snapshot_defaults_to_nobody_speaking():
    snapshot = VoiceSnapshot()
    assert snapshot.speaking is None
    assert snapshot.since is None
    assert snapshot.recent == []


def test_view_intent_union_accepts_voice_kind():
    voice_intent = TypeAdapter(ViewIntent).validate_python({
        "kind": "voice", "id": "voice:room", "speaking": "Mira",
        "since": "2026-11-23T09:12:00+01:00", "recent": [],
    })
    assert isinstance(voice_intent, VoiceIntent)


def test_view_intent_union_accepts_graph_kind():
    graph_intent = TypeAdapter(ViewIntent).validate_python({
        "kind": "graph", "id": "graph:session",
        "seed": "file:protokoll.md", "nodes": [], "edges": [], "hidden": 0,
    })
    assert isinstance(graph_intent, GraphIntent)


def test_knowledge_hit_matches_knowledge_link_block_payload():
    hit = KnowledgeHit(kind="finance", title="Budget overview", href="ablage/budget.md")
    assert hit.model_dump() == {
        "kind": "finance", "title": "Budget overview", "href": "ablage/budget.md"}


def test_view_intent_union_accepts_knowledge_kind():
    knowledge_intent = TypeAdapter(ViewIntent).validate_python({
        "kind": "knowledge", "id": "knowledge:t1", "topic_id": "t1",
        "query": "Budget and heating plan",
        "hits": [{"kind": "finance", "title": "Budget overview",
                  "href": "ablage/budget.md"}],
    })
    assert isinstance(knowledge_intent, KnowledgeIntent)
