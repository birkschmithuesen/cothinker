"""Generate `openapi.json` and `asyncapi.yaml` from the same Pydantic
models and FastAPI app (ADR 0019 point 1): request/answer in OpenAPI,
the SSE push channel in AsyncAPI, nothing written twice.

    python3 -m surfaces.contract.generate_spec [--check]

`--check` compares the generated documents against the committed files and
exits 1 on drift, without writing anything -- the CI drift test.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from surfaces.contract.app import create_app
from surfaces.contract.models import ViewIntent

HERE = Path(__file__).resolve().parent
OPENAPI_PATH = HERE / "openapi.json"
ASYNCAPI_PATH = HERE / "asyncapi.yaml"

ASYNCAPI_VERSION = "3.0.0"
CONTRACT_VERSION = "0.1.0"


def generate_openapi() -> dict:
    """The OpenAPI document for the snapshot and user-action endpoints."""
    app = create_app(player=None)
    return app.openapi()


def generate_asyncapi() -> dict:
    """The AsyncAPI 3.0 document for the `/events` SSE push channel.

    OpenAPI has no first-class notion of a push channel (ADR 0019
    consequences); AsyncAPI 3.0's `channels`/`operations`/`messages` model
    it directly. The `ViewIntent` schema is reused unchanged from the
    OpenAPI components -- one schema, not written twice.
    """
    from pydantic import TypeAdapter

    view_intent_schema = TypeAdapter(ViewIntent).json_schema(
        ref_template="#/components/schemas/{model}"
    )
    defs = view_intent_schema.pop("$defs", {})
    schemas = dict(defs)
    schemas["ViewIntent"] = view_intent_schema

    return {
        "asyncapi": ASYNCAPI_VERSION,
        "info": {
            "title": "CoThinker surface contract -- push channel",
            "version": CONTRACT_VERSION,
            "description": "View intents pushed by the surface contract's "
            "fixture-mode server over server-sent events (ADR 0019, ADR 0006).",
        },
        "channels": {
            "viewIntents": {
                "address": "/events",
                "messages": {
                    "viewIntent": {"$ref": "#/components/messages/viewIntent"},
                },
            },
        },
        "operations": {
            "sendViewIntent": {
                "action": "send",
                "channel": {"$ref": "#/channels/viewIntents"},
                "summary": "The server sends one view intent per SSE event.",
                "messages": [{"$ref": "#/channels/viewIntents/messages/viewIntent"}],
            },
        },
        "components": {
            "messages": {
                "viewIntent": {
                    "name": "viewIntent",
                    "title": "View intent",
                    "payload": {"$ref": "#/components/schemas/ViewIntent"},
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
                  "`python3 -m surfaces.contract.generate_spec` to regenerate",
                  file=sys.stderr)
            return 1
        return 0

    write_specs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
