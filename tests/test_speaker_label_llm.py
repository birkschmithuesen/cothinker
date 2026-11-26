# CoThinker — tests of the async LLM candidate stage for diarization labels
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_speaker_label_llm.py

Only invented placeholder names and speech. The infomaniak_chat.Client is
mocked: no network. `infomaniak_chat` is imported as a bare module with
gateway/ on sys.path, exactly like the module under test does, so the
ChatError class is the SAME object on both sides.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

from stt.pipeline.speaker_labels import label_llm as ll  # noqa: E402  (the ONE import of the module under test)
import infomaniak_chat  # noqa: E402  (bare, same as the module under test)

PEOPLE = ["Ada Vance", "Ben Ortiz", "Cora"]
WINDOW = [
    {"index": 0, "label": "S1", "text": "Ben, du hast die Hand gehoben."},
    {"index": 1, "label": "S2", "text": "Danke, ja, ich wollte etwas sagen."},
]


def reply(obj, latency=0.25):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return infomaniak_chat.ChatReply(text=text, latency_s=latency)


def good(**over):
    base = {"label": "S2", "name": "Ben Ortiz", "evidence_quote_index": 0,
            "confidence": "high"}
    base.update(over)
    return base


def fake_client(*results):
    """A Client double: each call returns/raises the next result."""
    client = mock.Mock(spec=infomaniak_chat.Client)
    client.chat.side_effect = list(results)
    return client


def judge_for(*results, **kw):
    return ll.CandidateJudge(fake_client(*results), sleep=lambda s: None, **kw)


# ------------------------------------------------------------------ prompt
def test_prompt_is_json_with_utterances_and_participants():
    p = json.loads(ll.build_candidate_prompt(WINDOW, PEOPLE))
    assert p["teilnehmende"] == PEOPLE
    assert p["aeusserungen"] == WINDOW


def test_prompt_keeps_umlauts_unescaped():
    w = [{"index": 0, "label": "S1", "text": "Grüße aus Köln"}]
    assert "Grüße aus Köln" in ll.build_candidate_prompt(w, PEOPLE)


def test_prompt_drops_unknown_window_keys():
    w = [{"index": 3, "label": "S1", "text": "hi", "secret": "x"}]
    assert "secret" not in ll.build_candidate_prompt(w, PEOPLE)


def test_prompt_handles_none_participants():
    assert json.loads(ll.build_candidate_prompt(WINDOW, None))[
        "teilnehmende"] == []


def test_schema_requires_all_four_fields_and_is_closed():
    s = ll.CANDIDATE_SCHEMA
    assert set(s["required"]) == {"label", "name", "evidence_quote_index",
                                  "confidence"}
    assert s["additionalProperties"] is False
    assert s["properties"]["confidence"]["enum"] == ["high", "medium", "low"]


# ------------------------------------------------------------------- judge
def test_judge_ok_verdict():
    j = judge_for(reply(good()))
    v = j.judge(WINDOW, PEOPLE)
    assert v.status == "ok"
    assert (v.label, v.name, v.evidence_quote_index, v.confidence) == (
        "S2", "Ben Ortiz", 0, "high")
    assert v.latency_s == 0.25


def test_judge_passes_model_schema_and_prompt_to_client():
    client = fake_client(reply(good()))
    ll.CandidateJudge(client, model="m-1", max_tokens=77).judge(WINDOW, PEOPLE)
    args, kwargs = client.chat.call_args
    assert args[0] == "m-1"
    assert args[1] == ll.SYSTEM_PROMPT
    assert json.loads(args[2])["teilnehmende"] == PEOPLE
    assert args[3] is ll.CANDIDATE_SCHEMA
    assert kwargs == {"schema_name": ll.SCHEMA_NAME, "max_tokens": 77}


def test_judge_defaults():
    j = ll.CandidateJudge(object())
    assert j.model == ll.DEFAULT_MODEL
    assert j.max_tokens == ll.DEFAULT_MAX_TOKENS
    assert j.backoff == ()


def test_judge_empty_window_is_format_without_calling_client():
    client = fake_client()
    v = ll.CandidateJudge(client).judge([], PEOPLE)
    assert (v.status, v.token) == ("format", "empty_window")
    client.chat.assert_not_called()


def test_judge_handles_chat_error_from_mocked_client():
    v = judge_for(infomaniak_chat.ChatError("boom", status=400,
                                            kind="http")).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("error", "http_400")
    assert v.latency_s is not None


def test_chat_error_class_is_the_same_object_on_both_sides():
    assert ll.ChatError is infomaniak_chat.ChatError


def test_judge_does_not_retry_by_default_even_on_retryable_status():
    client = fake_client(infomaniak_chat.ChatError("x", status=503,
                                                   kind="http"))
    v = ll.CandidateJudge(client).judge(WINDOW, PEOPLE)
    assert v.token == "http_503"
    assert client.chat.call_count == 1


def test_judge_retries_retryable_status_with_backoff():
    sleeps = []
    client = fake_client(
        infomaniak_chat.ChatError("x", status=503, kind="http"),
        infomaniak_chat.ChatError("y", status=429, kind="http"),
        reply(good()))
    j = ll.CandidateJudge(client, backoff=(0.1, 0.2), sleep=sleeps.append)
    v = j.judge(WINDOW, PEOPLE)
    assert v.status == "ok"
    assert sleeps == [0.1, 0.2]
    assert client.chat.call_count == 3


def test_judge_retries_network_errors():
    client = fake_client(
        infomaniak_chat.ChatError("n", kind="network"), reply(good()))
    j = ll.CandidateJudge(client, backoff=(0.0,), sleep=lambda s: None)
    assert j.judge(WINDOW, PEOPLE).status == "ok"


def test_judge_gives_up_after_backoff_exhausted():
    client = fake_client(
        infomaniak_chat.ChatError("a", status=500, kind="http"),
        infomaniak_chat.ChatError("b", status=500, kind="http"))
    j = ll.CandidateJudge(client, backoff=(0.0,), sleep=lambda s: None)
    v = j.judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("error", "http_500")
    assert client.chat.call_count == 2


def test_judge_does_not_retry_non_retryable_status():
    client = fake_client(infomaniak_chat.ChatError("a", status=401,
                                                   kind="http"), reply(good()))
    j = ll.CandidateJudge(client, backoff=(0.0, 0.0), sleep=lambda s: None)
    assert j.judge(WINDOW, PEOPLE).token == "http_401"
    assert client.chat.call_count == 1


def test_judge_unexpected_exception_becomes_error_verdict():
    v = judge_for(RuntimeError("kaputt")).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("error", "RuntimeError")


def test_judge_non_json_reply_is_format_not_json():
    v = judge_for(reply("das ist keine Antwort")).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "not_json")
    assert v.latency_s == 0.25


def test_judge_accepts_prose_in_front_of_json():
    v = judge_for(reply("Hier: " + json.dumps(good()))).judge(WINDOW, PEOPLE)
    assert v.status == "ok"


def test_judge_unknown_label_is_format():
    v = judge_for(reply(good(label="S9"))).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "unknown_label")


def test_judge_bad_index_is_format():
    v = judge_for(reply(good(evidence_quote_index=7))).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "bad_index")


def test_judge_invented_name_is_format():
    v = judge_for(reply(good(name="Dan Invented"))).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "unknown_name")


def test_judge_name_must_match_participant_verbatim():
    v = judge_for(reply(good(name="Ben"))).judge(WINDOW, PEOPLE)
    assert v.token == "unknown_name"


def test_judge_bad_confidence_is_bad_shape():
    v = judge_for(reply(good(confidence="certain"))).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "bad_shape")


def test_judge_bool_index_is_bad_shape():
    v = judge_for(reply(good(evidence_quote_index=True))).judge(WINDOW, PEOPLE)
    assert v.token == "bad_shape"


def test_judge_missing_field_is_bad_shape():
    obj = good()
    del obj["name"]
    assert judge_for(reply(obj)).judge(WINDOW, PEOPLE).token == "bad_shape"


def test_judge_json_array_reply_is_not_json():
    v = judge_for(reply("[1, 2]")).judge(WINDOW, PEOPLE)
    assert (v.status, v.token) == ("format", "not_json")


def test_judge_uses_window_indices_not_positions():
    w = [{"index": 5, "label": "S1", "text": "a"},
         {"index": 6, "label": "S2", "text": "b"}]
    v = judge_for(reply(good(evidence_quote_index=5))).judge(w, PEOPLE)
    assert v.status == "ok"
    assert judge_for(reply(good(evidence_quote_index=0))).judge(
        w, PEOPLE).token == "bad_index"


def test_judge_with_none_participants_rejects_any_name():
    v = judge_for(reply(good())).judge(WINDOW, None)
    assert v.token == "unknown_name"


def test_verdict_defaults():
    v = ll.Verdict(status="format")
    assert (v.label, v.name, v.evidence_quote_index, v.confidence,
            v.latency_s, v.token) == ("", "", -1, "", None, "")


# ------------------------------------------------------------------ worker
def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def workers():
    made = []
    yield made
    for w in made:
        w.stop()


def make_worker(workers, on_verdict, judge, **kw):
    w = ll.CandidateWorker(on_verdict, judge=judge, **kw)
    workers.append(w)
    return w


def test_worker_without_judge_refuses_requests(workers):
    w = make_worker(workers, lambda v: None, None)
    assert w.request(WINDOW, PEOPLE) is False


def test_worker_delivers_verdict_on_its_own_thread(workers):
    got, threads = [], []

    def on_verdict(v):
        got.append(v)
        threads.append(threading.current_thread().name)

    w = make_worker(workers, on_verdict, judge_for(reply(good())))
    assert w.request(WINDOW, PEOPLE) is True
    assert _wait(lambda: got)
    assert got[0].status == "ok" and got[0].name == "Ben Ortiz"
    assert threads == ["speaker-label-llm"]
    assert w.calls == 1


def test_worker_request_does_not_block_on_slow_judge(workers):
    gate = threading.Event()

    class Slow:
        def judge(self, window, participants):
            gate.wait(5)
            return ll.Verdict(status="ok")

    w = make_worker(workers, lambda v: None, Slow())
    try:
        t0 = time.monotonic()
        w.request(WINDOW, PEOPLE)
        assert time.monotonic() - t0 < 0.5
    finally:
        gate.set()


def test_worker_full_queue_returns_false_without_raising(workers):
    gate = threading.Event()
    started = threading.Event()

    class Slow:
        def judge(self, window, participants):
            started.set()
            gate.wait(5)
            return ll.Verdict(status="ok")

    w = make_worker(workers, lambda v: None, Slow(), queue_max=1)
    try:
        assert w.request(WINDOW, PEOPLE) is True
        assert started.wait(2)               # worker busy, queue empty
        assert w.request(WINDOW, PEOPLE) is True   # fills the single slot
        assert w.request(WINDOW, PEOPLE) is False
    finally:
        gate.set()


def test_worker_default_queue_max():
    assert ll.QUEUE_MAX == 2


def test_worker_copies_window_and_participants(workers):
    seen = []

    class Spy:
        def judge(self, window, participants):
            seen.append((window, participants))
            return ll.Verdict(status="ok")

    w = make_worker(workers, lambda v: None, Spy())
    win, ppl = list(WINDOW), list(PEOPLE)
    w.request(win, ppl)
    assert _wait(lambda: seen)
    assert seen[0] == (WINDOW, PEOPLE)
    assert seen[0][0] is not win and seen[0][1] is not ppl


def test_worker_survives_failing_callback_and_logs(workers):
    logs, got = [], []

    def on_verdict(v):
        got.append(v)
        if len(got) == 1:
            raise ValueError("callback broke")

    w = make_worker(workers, on_verdict, judge_for(reply(good()), reply(good())),
                    log=lambda *a: logs.append(a))
    w.request(WINDOW, PEOPLE)
    assert _wait(lambda: logs)
    assert logs[0] == ("speaker label llm worker error:", "ValueError")
    w.request(WINDOW, PEOPLE)
    assert _wait(lambda: len(got) == 2)


def test_worker_survives_failing_log(workers):
    got = []

    def bad_log(*a):
        raise RuntimeError("log broke")

    def on_verdict(v):
        got.append(v)
        if len(got) == 1:
            raise ValueError("x")

    w = make_worker(workers, on_verdict, judge_for(reply(good()), reply(good())),
                    log=bad_log)
    w.request(WINDOW, PEOPLE)
    assert _wait(lambda: got)
    w.request(WINDOW, PEOPLE)
    assert _wait(lambda: len(got) == 2)


def test_worker_chat_error_arrives_as_error_verdict(workers):
    got = []
    j = judge_for(infomaniak_chat.ChatError("n", kind="network"))
    w = make_worker(workers, got.append, j)
    w.request(WINDOW, PEOPLE)
    assert _wait(lambda: got)
    assert (got[0].status, got[0].token) == ("error", "network_None")


def test_worker_stop_ends_processing(workers):
    got = []
    w = make_worker(workers, got.append, judge_for(reply(good())))
    w.stop()
    w._thread.join(2)
    assert not w._thread.is_alive()
    w.request(WINDOW, PEOPLE)
    time.sleep(0.3)
    assert got == []


def test_worker_thread_is_a_daemon(workers):
    w = make_worker(workers, lambda v: None, None)
    assert w._thread.daemon is True
