"""Operator console conformance check (ADR 0019 point 4, applied to the
operator audience, issue #159):

    python -m surfaces.operator.check --surface <url or path to index.html>

Starts the operator contract server in fixture mode on a free port,
opens the surface headless (Playwright Chromium) with
`?api=<that server>/operator/&token=<token>`, records every network
request and waits for at least two `operator-status` pushes (the
fixture's initial snapshot, then one scripted change). Exit code 0 only
if every check passes.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from surfaces.operator.app import create_app
from surfaces.operator.fixtures import FixtureOperatorBackend, OperatorFrame, build_frames

VIEWPORT = {"width": 1280, "height": 800}

# What surfaces/operator/app.py serves today, plus FastAPI's own
# generated-docs support paths -- everything else a page requests is
# outside the operator contract.
OPERATOR_PATHS = {
    "/operator/status", "/operator/projects", "/operator/events",
    "/operator/start", "/operator/stop", "/operator/project",
    "/docs", "/openapi.json",
}

EVENT_COUNT_TARGET = 2
EVENT_TIMEOUT_S = 10.0
DEFAULT_TOKEN = "operator-check-token"


@dataclass
class CheckResult:
    name: str
    passed: bool
    reason: str

    def to_dict(self) -> dict:
        return {"name": self.name, "pass": self.passed, "reason": self.reason}


@dataclass
class Report:
    surface: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict:
        return {
            "surface": self.surface,
            "pass": self.passed,
            "checks": [check.to_dict() for check in self.checks],
        }


class _ServerThread:
    """Runs a `uvicorn` app on a free localhost port in a background thread."""

    def __init__(self, app):
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.01)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


class _StaticServerThread:
    """Serves a directory of static files on a free localhost port."""

    def __init__(self, directory: Path):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _quick_frames() -> list[OperatorFrame]:
    """The default fixture's first two frames, with the second's delay
    shortened: enough real time for a conformance check to observe one
    scripted change without waiting out the full ~2-minute timeline."""
    frames = build_frames()
    return [frames[0], OperatorFrame(delay_s=0.3, status=frames[1].status)]


def _surface_origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _is_allowed_request(url: str, contract_origin: str, surface_origin: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin == surface_origin:
        return True
    if origin == contract_origin and parts.path in OPERATOR_PATHS:
        return True
    return False


def run_check(surface: str, token: str = DEFAULT_TOKEN, out: str | Path | None = None) -> Report:
    from playwright.sync_api import sync_playwright

    out_dir = Path(out) if out is not None else Path("conformance-out")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = Report(surface=surface)

    backend = FixtureOperatorBackend(frames=_quick_frames())
    contract_server = _ServerThread(create_app(backend, token, cors_origins=["*"]))
    contract_base = contract_server.start()
    contract_origin = _surface_origin(contract_base)

    static_server: _StaticServerThread | None = None
    is_path = not surface.startswith(("http://", "https://"))
    if is_path:
        surface_path = Path(surface).resolve()
        static_server = _StaticServerThread(surface_path.parent)
        static_base = static_server.start()
        surface_url = f"{static_base}/{surface_path.name}"
    else:
        surface_url = surface
    surface_origin = _surface_origin(surface_url)

    requests_seen: list[dict] = []
    console_messages: list[dict] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            page.on("request", lambda req: requests_seen.append({"url": req.url, "headers": req.headers}))
            page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))

            open_url = f"{surface_url}?api={contract_base}/operator/&token={token}"
            page.goto(open_url, wait_until="load")

            deadline = time.monotonic() + EVENT_TIMEOUT_S
            while page.locator("#updates > div").count() < EVENT_COUNT_TARGET and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            update_count = page.locator("#updates > div").count()

            context.close()
            browser.close()
    finally:
        contract_server.stop()
        if static_server is not None:
            static_server.stop()

    status_requests = [r for r in requests_seen
                        if urlsplit(r["url"]).path == "/operator/status" and _surface_origin(r["url"]) == contract_origin]
    events_requests = [r for r in requests_seen
                        if urlsplit(r["url"]).path == "/operator/events" and _surface_origin(r["url"]) == contract_origin]

    report.checks.append(CheckResult(
        "fetches /operator/status", bool(status_requests),
        "GET /operator/status was requested" if status_requests else "no request to /operator/status was observed",
    ))
    report.checks.append(CheckResult(
        "subscribes to /operator/events", bool(events_requests),
        "GET /operator/events was requested" if events_requests else "no request to /operator/events was observed",
    ))

    operator_requests = status_requests + events_requests
    header_ok = bool(operator_requests) and all(
        r["headers"].get("authorization") == f"Bearer {token}" for r in operator_requests
    )
    token_in_url = any(f"token={token}" in urlsplit(r["url"]).query for r in operator_requests)
    report.checks.append(CheckResult(
        "sends the operator token as a Bearer header", header_ok and not token_in_url,
        "every /operator/ request carried the token as an authorization header, never a query parameter" if header_ok and not token_in_url
        else "a request to /operator/ was missing the Authorization header or carried the token in its URL",
    ))

    outside = [r["url"] for r in requests_seen if not _is_allowed_request(r["url"], contract_origin, surface_origin)]
    report.checks.append(CheckResult(
        "requests nothing outside the contract",
        not outside,
        "every request stayed within the operator paths and the surface's own static files" if not outside
        else "unexpected request(s) outside the operator paths and the surface's own static files: " + ", ".join(outside),
    ))

    report.checks.append(CheckResult(
        "renders at least two operator-status updates",
        update_count >= EVENT_COUNT_TARGET,
        f"{update_count} update(s) rendered" if update_count >= EVENT_COUNT_TARGET
        else f"only {update_count} of {EVENT_COUNT_TARGET} expected updates rendered within {EVENT_TIMEOUT_S:.0f}s",
    ))

    errors = [m["text"] for m in console_messages if m["type"] == "error"]
    report.checks.append(CheckResult(
        "no console errors",
        not errors,
        "no console errors were logged" if not errors else "console error(s): " + ", ".join(errors),
    ))

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n")

    return report


def _print_report(report: Report) -> None:
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"{status}: {check.name} -- {check.reason}")
    print(f"\n{'PASS' if report.passed else 'FAIL'}: overall")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces.operator.check")
    parser.add_argument("--surface", required=True, help="URL or path to an index.html")
    parser.add_argument("--out", default="conformance-out", help="directory for the report")
    args = parser.parse_args(argv)

    report = run_check(args.surface, out=args.out)
    _print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
