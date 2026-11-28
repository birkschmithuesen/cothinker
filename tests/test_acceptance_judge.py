"""Level-1 tests for the capped level-4 judge."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.acceptance.judge import Judge, JudgeConfig, combine  # noqa: E402
from demo.acceptance.report import Check  # noqa: E402


def cfg(api="anthropic-messages", max_calls=2, max_tokens=123, key_env="JUDGE_TEST_KEY"):
    return JudgeConfig(model="judge-m", endpoint="http://x/v1", key_env=key_env,
                       api=api, max_calls=max_calls, max_tokens=max_tokens)


class FakePost:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def anth(text):
    return {"content": [{"text": text}]}


def oai(text):
    return {"choices": [{"message": {"content": text}}]}


def test_disabled(monkeypatch):
    c = Judge(None, "lane").grade("a", "q?", "said", "ans")
    assert c.id == "scenario.a.judge" and c.status == "SKIPPED"
    assert "judge disabled" in c.evidence
    assert Judge(None, "lane").summary() is None


def test_equals_lane_model_fails_without_call(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "k")
    p = FakePost(anth("{}"))
    c = Judge(cfg(), "judge-m", post=p).grade("a", "q?", "s", "r")
    assert c.status == "FAIL" and "equals the lane model" in c.evidence
    assert p.calls == []


def test_anthropic_pass_and_request_shape(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "secret-value")
    p = FakePost(anth('noise {"verdict": "pass", "reason": "ok"} tail'))
    c = Judge(cfg(), "lane", post=p).grade("a", "Is it good?", "spoken-text", "answer-text")
    assert c.status == "PASS"
    url, headers, body, _ = p.calls[0]
    assert headers["x-api-key"] == "secret-value"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body["max_tokens"] == 123 and body["model"] == "judge-m"
    prompt = body["messages"][0]["content"]
    assert "spoken-text" in prompt and "answer-text" in prompt and "Is it good?" in prompt
    assert "secret-value" not in c.evidence


def test_openai_fail(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "k2")
    p = FakePost(oai('{"verdict": "fail", "reason": "off topic"}'))
    c = Judge(cfg(api="openai-chat"), "lane", post=p).grade("a", "q", "s", "r")
    assert c.status == "FAIL" and "off topic" in c.evidence
    assert p.calls[0][1]["Authorization"] == "Bearer k2"
    assert p.calls[0][2]["max_tokens"] == 123


def test_cap_stops_calls(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "k")
    p = FakePost(anth('{"verdict": "pass", "reason": "r"}'))
    j = Judge(cfg(max_calls=2), "lane", post=p)
    st = [j.grade(str(i), "q", "s", "r").status for i in range(4)]
    assert st == ["PASS", "PASS", "SKIPPED", "SKIPPED"]
    assert len(p.calls) == 2
    s = j.summary()
    assert s["calls_made"] == 2 and s["max_calls"] == 2 and s["max_tokens"] == 123
    assert s["model"] == "judge-m" and s["api"] == "anthropic-messages"
    assert s["endpoint"] == "http://x/v1"
    assert s["PASS"] == 2 and s["SKIPPED"] == 2
    assert "key" not in " ".join(s)


def test_missing_key_env_skipped(monkeypatch):
    monkeypatch.delenv("JUDGE_TEST_KEY", raising=False)
    p = FakePost(anth("{}"))
    c = Judge(cfg(), "lane", post=p).grade("a", "q", "s", "r")
    assert c.status == "SKIPPED" and "JUDGE_TEST_KEY" in c.evidence
    assert p.calls == []


def test_transport_error_skipped(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "k")
    c = Judge(cfg(), "lane", post=FakePost(OSError("boom"))).grade("a", "q", "s", "r")
    assert c.status == "SKIPPED"


def test_unparseable_skipped(monkeypatch):
    monkeypatch.setenv("JUDGE_TEST_KEY", "k")
    for text in ("no json here", '{"reason": "x"}'):
        c = Judge(cfg(), "lane", post=FakePost(anth(text))).grade("a", "q", "s", "r")
        assert c.status == "SKIPPED"


def mk(status):
    return Check("m", status, "e")


def test_combine():
    j = lambda s: Check("scenario.a.judge", s, "je")  # noqa: E731
    assert combine([mk("FAIL"), mk("PASS")], j("PASS"), "a").status == "FAIL"
    assert combine([mk("PASS")], j("FAIL"), "a").status == "FAIL"
    r = combine([mk("SKIPPED")], j("PASS"), "a")
    assert r.status == "SKIPPED" and "no mechanical check could run" in r.evidence
    r = combine([mk("PASS"), mk("SKIPPED")], j("PASS"), "a")
    assert r.status == "PASS" and r.id == "scenario.a" and "mechanical PASS" in r.evidence
    assert combine([mk("PASS")], None, "a").status == "PASS"
    assert combine([mk("PASS")], j("SKIPPED"), "a").status == "PASS"
    assert "SKIPPED" in combine([mk("PASS")], j("SKIPPED"), "a").evidence
