"""Generate `openapi.json` and `asyncapi.yaml` for the operator console
from the same Pydantic models and FastAPI app (ADR 0019 point 1, applied
to the operator audience, issue #159): request/answer in OpenAPI, the SSE
push channel in AsyncAPI.

    python3 -m surfaces.operator.generate_spec [--check]

`--check` compares the generated documents against the committed files
and exits 1 on drift, without writing anything -- the CI drift test
(`tests/test_operator_spec.py`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from surfaces.operator.app import create_app
from surfaces.operator.fixtures import FixtureOperatorBackend
from surfaces.operator.models import OperatorStatus

HERE = Path(__file__).resolve().parent
OPENAPI_PATH = HERE / "openapi.json"
ASYNCAPI_PATH = HERE / "asyncapi.yaml"

ASYNCAPI_VERSION = "3.0.0"
CONTRACT_VERSION = "0.1.0"

# Only used to build an app for schema introspection (`app.openapi()`
# never calls an endpoint, so no real token ever has to compare against
# this); never written to a file or printed.
_SCHEMA_GEN_TOKEN = "schema-generation-only"


def generate_openapi() -> dict:
    """The OpenAPI document for the status/projects endpoints and the
    start/stop/project commands."""
    app = create_app(FixtureOperatorBackend(), _SCHEMA_GEN_TOKEN)
    return app.openapi()


def generate_asyncapi() -> dict:
    """The AsyncAPI 3.0 document for the `/operator/events` SSE push
    channel: the full `OperatorStatus` snapshot, pushed on change."""
    from pydantic import TypeAdapter

    status_schema = TypeAdapter(OperatorStatus).json_schema(ref_template="#/components/schemas/{model}")
    defs = status_schema.pop("$defs", {})
    schemas = dict(defs)
    schemas["OperatorStatus"] = status_schema

    return {
        "asyncapi": ASYNCAPI_VERSION,
        "info": {
            "title": "CoThinker operator console -- push channel",
            "version": CONTRACT_VERSION,
            "description": "The full operator status snapshot, pushed over"
            " server-sent events on change, at most once a second"
            " (ADR 0019 style, issue #159).",
        },
        "channels": {
            "operatorStatus": {
                "address": "/operator/events",
                "messages": {
                    "operatorStatus": {"$ref": "#/components/messages/operatorStatus"},
                },
            },
        },
        "operations": {
            "sendOperatorStatus": {
                "action": "send",
                "channel": {"$ref": "#/channels/operatorStatus"},
                "summary": "The server sends the full operator status"
                " snapshot on every change, at most once a second.",
                "messages": [{"$ref": "#/channels/operatorStatus/messages/operatorStatus"}],
            },
        },
        "components": {
            "messages": {
                "operatorStatus": {
                    "name": "operatorStatus",
                    "title": "Operator status",
                    "payload": {"$ref": "#/components/schemas/OperatorStatus"},
                },
            },
            "schemas": schemas,
        },
    }


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_specs() -> None:
    _write(OPENAPI_PATH, json.dumps(generate_openapi(), indent=2, sort_keys=True) + "\n")
    _write(ASYNCAPI_PATH, yaml.safe_dump(generate_asyncapi(), sort_keys=False))


def check_specs() -> list[str]:
    """Field names of every document that drifted; empty when none did."""
    drifted = []
    expected_openapi = json.dumps(generate_openapi(), indent=2, sort_keys=True) + "\n"
    if not OPENAPI_PATH.exists() or OPENAPI_PATH.read_text(encoding="utf-8") != expected_openapi:
        drifted.append("openapi.json")
    expected_asyncapi = yaml.safe_dump(generate_asyncapi(), sort_keys=False)
    if not ASYNCAPI_PATH.exists() or ASYNCAPI_PATH.read_text(encoding="utf-8") != expected_asyncapi:
        drifted.append("asyncapi.yaml")
    return drifted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        drifted = check_specs()
        if drifted:
            print(f"drift in: {', '.join(drifted)} -- run "
                  "`python3 -m surfaces.operator.generate_spec` to regenerate",
                  file=sys.stderr)
            return 1
        return 0

    write_specs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
