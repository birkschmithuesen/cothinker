"""Drift test for the operator console's generated OpenAPI/AsyncAPI
documents (issue #159, mirroring tests/test_surface_contract_spec.py):
`surfaces/operator/openapi.json` and `asyncapi.yaml` must be exactly what
`surfaces.operator.generate_spec` produces from the current models.
"""
from __future__ import annotations

import json

import jsonschema
import yaml

from surfaces.operator.generate_spec import (
    ASYNCAPI_PATH,
    OPENAPI_PATH,
    check_specs,
    generate_asyncapi,
    generate_openapi,
)


def test_openapi_file_matches_generated_spec():
    assert OPENAPI_PATH.exists(), "run `python3 -m surfaces.operator.generate_spec`"
    on_disk = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert on_disk == generate_openapi()


def test_asyncapi_file_matches_generated_spec():
    assert ASYNCAPI_PATH.exists(), "run `python3 -m surfaces.operator.generate_spec`"
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
    assert "operatorStatus" in doc["channels"]
    channel = doc["channels"]["operatorStatus"]
    assert "operatorStatus" in channel["messages"]
    operation = doc["operations"]["sendOperatorStatus"]
    assert operation["channel"]["$ref"] == "#/channels/operatorStatus"
    message = doc["components"]["messages"]["operatorStatus"]
    assert message["payload"]["$ref"] == "#/components/schemas/OperatorStatus"
    assert "OperatorStatus" in doc["components"]["schemas"]


def test_asyncapi_operator_status_schema_is_a_valid_json_schema():
    doc = yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))
    schema = doc["components"]["schemas"]["OperatorStatus"]
    jsonschema.Draft202012Validator.check_schema(schema)


def test_openapi_paths_cover_the_full_operator_contract():
    doc = generate_openapi()
    paths = set(doc["paths"])
    for path in ("/operator/status", "/operator/projects", "/operator/events",
                 "/operator/start", "/operator/stop", "/operator/project"):
        assert path in paths, f"missing from generated openapi.json: {path}"
