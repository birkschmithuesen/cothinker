"""Drift test for the generated OpenAPI/AsyncAPI documents (ADR 0019 point 1,
issue #132): `surfaces/contract/openapi.json` and `asyncapi.yaml` must be
exactly what `surfaces.contract.generate_spec` produces from the current
models -- a field added to, removed from or retyped in the models and not
regenerated fails this test.

AsyncAPI validation note: no hosted AsyncAPI validator or linter package
is reachable in this environment (no network, no `asyncapi` package
installed). "Validates" here means: the file parses as YAML, carries the
AsyncAPI 3.0 required top-level keys (`asyncapi`, `info`, `channels`,
`operations`, `components`), and its channel/operation/message/schema
cross-references resolve and the referenced `ViewIntent` schema is a
structurally valid JSON Schema (checked with `jsonschema`). This is a
deliberate, reversible choice given the environment, not a substitute for
running a real AsyncAPI validator when one is reachable.
"""
from __future__ import annotations

import json

import jsonschema
import yaml

from surfaces.contract.generate_spec import (
    ASYNCAPI_PATH,
    OPENAPI_PATH,
    check_specs,
    generate_asyncapi,
    generate_openapi,
)


def test_openapi_file_matches_generated_spec():
    assert OPENAPI_PATH.exists(), "run `python3 -m surfaces.contract.generate_spec`"
    on_disk = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert on_disk == generate_openapi()


def test_asyncapi_file_matches_generated_spec():
    assert ASYNCAPI_PATH.exists(), "run `python3 -m surfaces.contract.generate_spec`"
    on_disk = yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))
    assert on_disk == generate_asyncapi()


def test_check_specs_reports_no_drift():
    assert check_specs() == []


def test_asyncapi_has_required_top_level_keys():
    doc = yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))
    for key in ("asyncapi", "info", "channels", "operations", "components"):
        assert key in doc
    assert doc["asyncapi"] == "3.0.0"


def test_asyncapi_channel_and_operation_cross_references_resolve():
    doc = yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))
    assert "viewIntents" in doc["channels"]
    channel = doc["channels"]["viewIntents"]
    assert "viewIntent" in channel["messages"]
    operation = doc["operations"]["sendViewIntent"]
    assert operation["channel"]["$ref"] == "#/channels/viewIntents"
    message = doc["components"]["messages"]["viewIntent"]
    assert message["payload"]["$ref"] == "#/components/schemas/ViewIntent"
    assert "ViewIntent" in doc["components"]["schemas"]


def test_asyncapi_view_intent_schema_is_a_valid_json_schema():
    doc = yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))
    schema = doc["components"]["schemas"]["ViewIntent"]
    jsonschema.Draft202012Validator.check_schema(schema)
