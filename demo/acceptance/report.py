"""One report format for every level-4 tool (ADR 0022 rule 4, ADR 0025).

There is deliberately no acceptance field here: an acceptance lives in a
separate human-written file that only gate.py reads (ADR 0025 rule 7)."""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = ("PASS", "FAIL", "SKIPPED")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)


def redact(text: str, token: str) -> str:
    return text.replace(token, "<redacted>") if token else text


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    evidence: str = ""

    @property
    def name(self) -> str:
        return self.id

    @property
    def detail(self) -> str:
        return self.evidence

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "evidence": self.evidence}


@dataclass
class Report:
    run_id: str = field(default_factory=new_run_id)
    base_url: str = ""
    token: str = ""
    fingerprint: dict | None = None
    preflight_run_id: str | None = None
    judge: dict | None = None
    tool: str = ""
    started_at: float = field(default_factory=time.time)
    checks: list[Check] = field(default_factory=list)

    def add(self, id: str, status: str, evidence: str = "") -> Check:
        if status not in STATUSES:
            raise ValueError(status)
        c = Check(id, status, redact(evidence, self.token))
        self.checks.append(c)
        return c

    def extend(self, checks) -> None:
        for c in checks:
            self.add(c.id, c.status, c.evidence)

    @property
    def passed(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "base_url": self.base_url, "started_at": self.started_at,
                "finished_at": time.time(), "fingerprint": self.fingerprint,
                "preflight_run_id": self.preflight_run_id, "tool": self.tool, "judge": self.judge,
                "pass": self.passed, "checks": [c.to_dict() for c in self.checks]}

    def to_markdown(self) -> str:
        fp = (self.fingerprint or {}).get("digest", "none")
        lines = ["# Acceptance report", "", f"Run: {self.run_id}", f"Fingerprint: {fp}",
                 f"Judge: {self.judge or 'disabled'}", "", "| Status | Check | Evidence |", "|---|---|---|"]
        lines += [f"| {c.status} | {c.id} | {c.evidence} |" for c in self.checks]
        lines += ["", "Overall: " + ("PASS" if self.passed else "FAIL")]
        return "\n".join(lines)

    def write(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        (out_dir / "report.md").write_text(self.to_markdown() + "\n", encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bind_preflight(rep: Report, preflight_report) -> None:
    """Record which preflight (and so which deployment fingerprint) this run
    belongs to, or say that it is not bound."""
    if preflight_report is None:
        rep.add("fingerprint.bound", "SKIPPED", "no --preflight-report given")
        return
    pre = load(preflight_report)
    fp = pre.get("fingerprint")
    digest = fp.get("digest") if isinstance(fp, dict) else None
    if not isinstance(digest, str) or not digest or not isinstance(pre.get("run_id"), str):
        rep.add("fingerprint.bound", "FAIL",
                "preflight report has no fingerprint (run preflight with --consumer-unit and --out)")
        return
    rep.preflight_run_id = pre["run_id"]
    rep.fingerprint = fp
    rep.add("fingerprint.bound", "PASS", f"preflight {pre['run_id']} digest {digest[:12]}")
