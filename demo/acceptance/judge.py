"""Capped judge for the level-4 acceptance run.

The judge can only turn a mechanical PASS into a FAIL; it never produces a PASS
on its own. It is pinned to a configured model, refuses to run when that model
equals the lane model, and stops at a hard call cap.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

from demo.acceptance.report import Check

TIMEOUT_S = 60

PROMPT = (
    "You are a strict reviewer of a meeting assistant.\n"
    "Spoken text of the scenario:\n{spoken}\n\n"
    "The assistant's answer:\n{answer}\n\n"
    "Question to decide:\n{question}\n\n"
    'Reply with one JSON object only: {{"verdict": "pass"|"fail", "reason": "..."}}'
)


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    endpoint: str
    key_env: str
    api: str  # "anthropic-messages" or "openai-chat"
    max_calls: int
    max_tokens: int


def http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def first_json_object(text: str) -> dict | None:
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text, i)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


class Judge:
    def __init__(self, cfg: JudgeConfig | None, lane_model: str | None,
                 post: Callable[..., dict] = http_post):
        self.cfg = cfg
        self.lane_model = lane_model
        self.post = post
        self.calls_made = 0
        self.counts = {"PASS": 0, "FAIL": 0, "SKIPPED": 0}

    def _done(self, scenario_id: str, status: str, evidence: str) -> Check:
        self.counts[status] += 1
        return Check(f"scenario.{scenario_id}.judge", status, evidence)

    def grade(self, scenario_id: str, question: str, spoken: str, answer: str) -> Check:
        cfg = self.cfg
        if cfg is None:
            return self._done(scenario_id, "SKIPPED", "judge disabled (no --judge-model)")
        if self.lane_model is not None and cfg.model == self.lane_model:
            return self._done(scenario_id, "FAIL",
                              f"judge model {cfg.model!r} equals the lane model; judge must differ")
        if self.calls_made >= cfg.max_calls:
            return self._done(scenario_id, "SKIPPED", f"judge call cap reached ({cfg.max_calls})")
        key = os.environ.get(cfg.key_env)
        if not key:
            return self._done(scenario_id, "SKIPPED", f"judge key env {cfg.key_env} is not set")
        prompt = PROMPT.format(spoken=spoken, answer=answer, question=question)
        body = {"model": cfg.model, "max_tokens": cfg.max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        if cfg.api == "anthropic-messages":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        elif cfg.api == "openai-chat":
            headers = {"Authorization": f"Bearer {key}"}
        else:
            return self._done(scenario_id, "SKIPPED", f"unknown judge api {cfg.api!r}")
        self.calls_made += 1
        try:
            reply = self.post(cfg.endpoint, headers, body, TIMEOUT_S)
            if cfg.api == "anthropic-messages":
                text = reply["content"][0]["text"]
            else:
                text = reply["choices"][0]["message"]["content"]
        except Exception as exc:  # transport or shape error: never a PASS
            return self._done(scenario_id, "SKIPPED", f"judge call failed: {type(exc).__name__}")
        obj = first_json_object(text if isinstance(text, str) else "")
        verdict = str(obj.get("verdict", "")).lower() if obj else ""
        if verdict not in ("pass", "fail"):
            return self._done(scenario_id, "SKIPPED", "judge reply had no usable verdict")
        reason = str(obj.get("reason", ""))[:300]
        return self._done(scenario_id, verdict.upper(), f"judge {verdict}: {reason}")

    def summary(self) -> dict | None:
        if self.cfg is None:
            return None
        c = self.cfg
        return {"model": c.model, "api": c.api, "endpoint": c.endpoint,
                "max_calls": c.max_calls, "max_tokens": c.max_tokens,
                "calls_made": self.calls_made, **self.counts}


def combine(mechanical: list[Check], judge: Check | None, scenario_id: str) -> Check:
    cid = f"scenario.{scenario_id}"
    fails = [c for c in mechanical if c.status == "FAIL"]
    if fails:
        return Check(cid, "FAIL", "mechanical FAIL: " + "; ".join(c.id for c in fails))
    if judge is not None and judge.status == "FAIL":
        return Check(cid, "FAIL", f"mechanical PASS, judge FAIL: {judge.evidence}")
    if not any(c.status == "PASS" for c in mechanical):
        return Check(cid, "SKIPPED", "no mechanical check could run")
    ev = "mechanical PASS"
    if judge is not None:
        ev += f", judge {judge.status}"
    return Check(cid, "PASS", ev)
