"""Surface conformance check (ADR 0019 point 4, issue #142):

    python -m surfaces.contract.check --surface <url or path to index.html> [--scenario meeting]

One command, no knowledge of this repository's code required: it starts
the contract server in fixture mode on a free port, opens the surface
with `?api=<that server>/`, records every network request, plays the
whole scenario and prints PASS/FAIL per check with a one-line reason.
Exit code 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import copy
import functools
import http.server
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from pydantic import BaseModel

from surfaces.contract.app import create_app
from surfaces.contract.fixtures import Frame, load_scenario
from surfaces.contract.player import ScenarioPlayer

VIEWPORT = {"width": 1920, "height": 1080}

# What `surfaces/contract/app.py` serves today, plus FastAPI's own
# generated-docs support paths -- everything else a page requests is
# outside the contract.
CONTRACT_PATHS = {"/snapshot", "/actions", "/events", "/docs", "/openapi.json"}

EVENT_TIMEOUT_S = 10.0
DOM_SETTLE_S = 0.3


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
    scenario: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict:
        return {
            "surface": self.surface,
            "scenario": self.scenario,
            "pass": self.passed,
            "checks": [check.to_dict() for check in self.checks],
        }


# --------------------------------------------------------------------------
# Serving: the contract server (fixture mode) and, for a path surface, the
# surface's own static files.
# --------------------------------------------------------------------------


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


def _build_app_from_frames(frames: list[Frame]):
    """The contract server used by the check: its own port differs from the
    surface's static-file port it opens the surface against (`run_check`),
    so it needs cross-origin access by construction, not as an opt-in a
    caller must remember to pass."""
    player = ScenarioPlayer(frames, frames[0].snapshot)
    return create_app(player, cors_origins=["*"])


# --------------------------------------------------------------------------
# "planned" fields: stripped to their zero value for the second replay.
# --------------------------------------------------------------------------


def _is_planned(extra) -> bool:
    return isinstance(extra, dict) and extra.get("x-status") == "planned"


def _zero_value(value):
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return value


# Envelope identity fields every `ViewIntent` carries (models.py): zeroing
# these would turn a whole-model-planned intent into an invalid envelope
# (no `kind` to dispatch on, no `id` a user action could refer back to)
# rather than just emptying its unfilled data, so they are kept as-is.
_IDENTITY_FIELDS = {"kind", "id"}


def _strip_planned(model: BaseModel) -> BaseModel:
    """A copy of `model` with every field marked `x-status: planned` (on the
    field itself, or on its whole model) reset to its zero value."""
    model_cls = type(model)
    model_extra = model_cls.model_config.get("json_schema_extra") if isinstance(model_cls.model_config, dict) else None
    whole_model_planned = _is_planned(model_extra)

    data = {}
    for field_name, field_info in model_cls.model_fields.items():
        value = getattr(model, field_name)
        if field_name in _IDENTITY_FIELDS:
            data[field_name] = value
        elif whole_model_planned or _is_planned(field_info.json_schema_extra):
            data[field_name] = _zero_value(value)
        elif isinstance(value, BaseModel):
            data[field_name] = _strip_planned(value)
        elif isinstance(value, list):
            data[field_name] = [_strip_planned(item) if isinstance(item, BaseModel) else item for item in value]
        else:
            data[field_name] = value
    return model_cls.model_construct(**data)


def _strip_planned_frames(frames: list[Frame]) -> list[Frame]:
    return [
        Frame(
            delay_s=frame.delay_s,
            snapshot=_strip_planned(frame.snapshot),
            view_intents=[_strip_planned(intent) for intent in frame.view_intents],
        )
        for frame in frames
    ]


# --------------------------------------------------------------------------
# The check itself.
#
# Several intents of one fixture frame are pushed with no delay between
# them (`ScenarioPlayer.run()` awaits only between frames, not within
# one), so DOM snapshots taken by polling from Python cannot be lined up
# with a single intent reliably. Instead, an init script wraps the page's
# own `EventSource` *before* the surface's script runs: our listener is
# registered on the real connection first, so it fires -- synchronously,
# in the browser -- before the surface's own listener for the same event,
# giving a true "before" snapshot per intent; a `setTimeout` taken from
# inside that same handler gives the "after" snapshot once the surface
# has had a chance to render. `page.expose_function` reports both back.
# --------------------------------------------------------------------------

_WRAP_EVENT_SOURCE_SCRIPT = """
(() => {
  const RealEventSource = window.EventSource;
  function WrappedEventSource(url, options) {
    const es = new RealEventSource(url, options);
    es.addEventListener('view-intent', (event) => {
      let kind = 'unknown';
      try { kind = JSON.parse(event.data).kind; } catch (err) { /* leave 'unknown' */ }
      const before = document.body.innerHTML;
      setTimeout(() => {
        const after = document.body.innerHTML;
        window.__conformanceReport(kind, before, after);
      }, %(settle_ms)d);
    });
    return es;
  }
  WrappedEventSource.prototype = RealEventSource.prototype;
  window.EventSource = WrappedEventSource;
})();
""" % {"settle_ms": int(DOM_SETTLE_S * 1000)}


def _with_api_param(surface_url: str, contract_base: str) -> str:
    """`surface_url` with `api=<contract_base>/` appended to its query
    string. A token-gated surface (ADR 0020) already carries its own
    `?t=...`, so this joins with `&` rather than assuming `surface_url`
    has no query of its own."""
    sep = "&" if "?" in surface_url else "?"
    return f"{surface_url}{sep}api={contract_base}/"


def _surface_origin(surface_url: str) -> str:
    parts = urlsplit(surface_url)
    return f"{parts.scheme}://{parts.netloc}"


def _is_allowed_request(url: str, contract_origin: str, surface_origin: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin == surface_origin:
        return True
    if origin == contract_origin and parts.path in CONTRACT_PATHS:
        return True
    return False


def _replay_and_collect(
    *,
    surface_url: str,
    contract_base: str,
    frames: list[Frame],
    out_dir: Path,
    screenshot_prefix: str,
):
    """Opens `surface_url` with `api=<contract_base>/` appended to its query
    string (a token-gated surface already carries its own `?t=...`, so this
    joins with `&` rather than assuming `surface_url` has no query of its
    own), plays `frames` on the already-running contract server behind it,
    and returns `(requests, console_messages, per_intent_results, final_html)`.
    """
    from playwright.sync_api import sync_playwright

    requests: list[str] = []
    console_messages: list[dict] = []
    reports: list[dict] = []

    expected_count = sum(len(frame.view_intents) for frame in frames)
    total_delay_s = sum(frame.delay_s for frame in frames)
    overall_budget_s = total_delay_s + EVENT_TIMEOUT_S

    with sync_playwright() as playwright:
        # A real deployment's surface loads from a public origin; the
        # fixture server this check spins up is loopback-only. Chrome's
        # Private Network Access policy blocks a public page from
        # fetching a loopback address by default (confirmed live: every
        # request to it failed client-side with "blocked by CORS policy
        # ... `loopback` address space", even with the PNA feature flags
        # disabled -- this Chrome version enforces it unconditionally
        # for a public-to-loopback fetch, not behind a feature flag).
        # This check controls both ends of that fetch (the fixture
        # server is its own, spun up for exactly this run) and the
        # browser instance is thrown away right after, so disabling web
        # security for it entirely is safe.
        browser = playwright.chromium.launch(
            headless=True, args=["--disable-web-security"])
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        # Font binaries are not shipped in this public repo (surfaces/design/
        # build.py documents the gap) and are out of scope for this check.
        page.route("**/fonts/*.woff2", lambda route: route.fulfill(status=200, content_type="font/woff2", body=b""))
        page.on("request", lambda req: requests.append(req.url))
        page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
        page.expose_function(
            "__conformanceReport",
            lambda kind, before, after: reports.append({"kind": kind, "before": before, "after": after}),
        )
        page.add_init_script(_WRAP_EVENT_SOURCE_SCRIPT)

        open_url = _with_api_param(surface_url, contract_base)
        page.goto(open_url, wait_until="load")

        deadline = time.monotonic() + overall_budget_s
        while len(reports) < expected_count and time.monotonic() < deadline:
            page.wait_for_timeout(50)

        per_intent: list[dict] = []
        for index, report in enumerate(reports):
            dom_changed = report["before"] != report["after"]
            dropped_logged = any(
                f"dropped {report['kind']}" in message["text"] for message in console_messages
            )
            shot_path = out_dir / f"{screenshot_prefix}{index:02d}-{report['kind'].replace(':', '_')}.png"
            page.screenshot(path=str(shot_path))
            per_intent.append({
                "kind": report["kind"],
                "ok": dom_changed or dropped_logged,
                "reason": "DOM changed after the event" if dom_changed else (
                    f"console logged 'dropped {report['kind']}'" if dropped_logged
                    else f"no DOM change and no 'dropped {report['kind']}' console log"
                ),
            })
        for missing_index in range(len(reports), expected_count):
            per_intent.append({
                "kind": "?",
                "ok": False,
                "reason": f"only {len(reports)} of {expected_count} intents arrived within {overall_budget_s:.0f}s",
            })

        final_html = page.evaluate("document.body.innerHTML")
        context.close()
        browser.close()

    return requests, console_messages, per_intent, final_html


def run_check(surface: str, scenario: str = "meeting", out: str | Path | None = None) -> Report:
    out_dir = Path(out) if out is not None else Path("conformance-out")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = Report(surface=surface, scenario=scenario)

    frames = load_scenario(scenario)
    stripped_frames = _strip_planned_frames(frames)

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

    contract_server = _ServerThread(_build_app_from_frames(copy.deepcopy(frames)))
    contract_base = contract_server.start()
    contract_origin = _surface_origin(contract_base)

    try:
        requests, console_messages, per_intent, _ = _replay_and_collect(
            surface_url=surface_url,
            contract_base=contract_base,
            frames=frames,
            out_dir=out_dir,
            screenshot_prefix="",
        )
    finally:
        contract_server.stop()

    snapshot_requested = any(
        urlsplit(url).path == "/snapshot" and _surface_origin(url) == contract_origin
        for url in requests
    )
    events_requested = any(
        urlsplit(url).path == "/events" and _surface_origin(url) == contract_origin
        for url in requests
    )
    report.checks.append(CheckResult(
        "fetches /snapshot", snapshot_requested,
        "GET /snapshot was requested" if snapshot_requested else "no request to /snapshot was observed",
    ))
    report.checks.append(CheckResult(
        "subscribes to /events", events_requested,
        "GET /events was requested" if events_requested else "no request to /events was observed",
    ))

    outside = [url for url in requests if not _is_allowed_request(url, contract_origin, surface_origin)]
    report.checks.append(CheckResult(
        "requests nothing outside the contract",
        not outside,
        "every request stayed within the contract paths and the surface's own static files" if not outside
        else "unexpected request(s) outside the contract paths and the surface's own static files: " + ", ".join(outside),
    ))

    for entry in per_intent:
        report.checks.append(CheckResult(
            f"intent '{entry['kind']}' renders or is dropped",
            entry["ok"],
            entry["reason"],
        ))

    errors = [m["text"] for m in console_messages if m["type"] == "error"]
    report.checks.append(CheckResult(
        "no console errors",
        not errors,
        "no console errors were logged" if not errors else "console error(s): " + ", ".join(errors),
    ))

    # Second replay: every planned field stripped to its zero value must
    # not break the page.
    stripped_contract_server = _ServerThread(_build_app_from_frames(copy.deepcopy(stripped_frames)))
    stripped_contract_base = stripped_contract_server.start()
    try:
        _, stripped_console_messages, _, stripped_final_html = _replay_and_collect(
            surface_url=surface_url,
            contract_base=stripped_contract_base,
            frames=stripped_frames,
            out_dir=out_dir,
            screenshot_prefix="planned-stripped-",
        )
    finally:
        stripped_contract_server.stop()

    if static_server is not None:
        static_server.stop()

    stripped_errors = [m["text"] for m in stripped_console_messages if m["type"] == "error"]
    report.checks.append(CheckResult(
        "planned fields sent empty do not break the page",
        not stripped_errors,
        "no console errors with planned fields stripped" if not stripped_errors
        else "console error(s) with planned fields stripped: " + ", ".join(stripped_errors),
    ))
    blank = not stripped_final_html.strip()
    report.checks.append(CheckResult(
        "planned fields sent empty do not blank the screen",
        not blank,
        "the page still rendered content with planned fields stripped" if not blank
        else "the page was blank with planned fields stripped",
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
    parser = argparse.ArgumentParser(prog="python -m surfaces.contract.check")
    parser.add_argument("--surface", required=True, help="URL or path to an index.html")
    parser.add_argument("--scenario", default="meeting", help="fixture scenario to replay")
    parser.add_argument("--out", default="conformance-out", help="directory for the report and screenshots")
    args = parser.parse_args(argv)

    report = run_check(args.surface, scenario=args.scenario, out=args.out)
    _print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
