"""Contract v1 is strict, hashed, and validated with JSON Schema."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_runtime.integrations.canonical import CONTRACT_VERSION, motivation_output
from agent_runtime.motivation.engine import MotivationState

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "v1"
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "fixtures"
MANIFEST = ROOT / "manifest.json"

SCHEMA_FILES = (
    "body_state_view.schema.json",
    "motivation_input.schema.json",
    "motivation_output.schema.json",
    "outcome_view.schema.json",
    "self_state_view.schema.json",
    "world_state_view.schema.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_contract_v1_layout_and_manifest() -> None:
    assert SCHEMAS.is_dir()
    assert FIXTURES.is_dir()
    assert MANIFEST.is_file()
    manifest = _load(MANIFEST)
    assert manifest["contract_version"] == "1.0"
    files = manifest["files"]
    for name in SCHEMA_FILES:
        rel = f"schemas/{name}"
        assert rel in files
        assert files[rel] == _sha256(SCHEMAS / name)
    for fixture in FIXTURES.glob("*.json"):
        rel = f"fixtures/{fixture.name}"
        assert rel in files
        assert files[rel] == _sha256(fixture)


def test_contract_v1_schemas_are_strict() -> None:
    for name in SCHEMA_FILES:
        schema = _load(SCHEMAS / name)
        assert schema.get("additionalProperties") is False
        assert schema.get("required")
        assert schema["properties"]["contract_version"]["const"] == "1.0"


def test_self_state_view_schema_uses_agent_id_not_identity() -> None:
    schema = _load(SCHEMAS / "self_state_view.schema.json")
    required = set(schema["required"])
    assert required == {
        "contract_version",
        "agent_id",
        "metrics",
        "capabilities",
        "uncertainty",
        "extensions",
    }
    assert "identity" not in schema["properties"]
    assert "capability_estimates" not in schema["properties"]
    assert "extensions" in schema["properties"]


def test_body_component_relation_is_enum() -> None:
    schema = _load(SCHEMAS / "body_state_view.schema.json")
    item = schema["properties"]["components"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"component_id", "kind", "health", "relation"}
    assert item["properties"]["relation"]["enum"] == [
        "CORE_SELF",
        "BODY",
        "OWNED",
        "DEPENDENCY",
        "ALLY",
        "EXTERNAL",
        "UNKNOWN",
    ]


def test_golden_fixtures_validate_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    registry = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        schema = _load(path)
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.name, resource)
        if "$id" in schema:
            registry = registry.with_resource(schema["$id"], resource)

    mapping = {
        "body_state_view.json": "body_state_view.schema.json",
        "motivation_input.json": "motivation_input.schema.json",
        "motivation_output.json": "motivation_output.schema.json",
        "outcome_view.json": "outcome_view.schema.json",
        "self_state_view.json": "self_state_view.schema.json",
        "world_state_view.json": "world_state_view.schema.json",
    }
    for fixture_name, schema_name in mapping.items():
        payload = _load(FIXTURES / fixture_name)
        schema = _load(SCHEMAS / schema_name)
        jsonschema.Draft202012Validator(schema, registry=registry).validate(payload)


def test_canonical_builders_match_strict_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    registry = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        schema = _load(path)
        registry = registry.with_resource(path.name, Resource.from_contents(schema))

    out = motivation_output(MotivationState.neutral())
    assert out["contract_version"] == CONTRACT_VERSION
    assert "tendencies" not in out
    jsonschema.Draft202012Validator(
        _load(SCHEMAS / "motivation_output.schema.json"), registry=registry
    ).validate(out)


def test_canonical_has_no_tool_mapping_policy() -> None:
    import agent_runtime.integrations.canonical as canonical

    assert not hasattr(canonical, "CONTRACT_ACTION_TO_KIND")
    assert not hasattr(canonical, "KIND_TO_CONTRACT_DIRECTION")
    assert not hasattr(canonical, "action_tendencies_from_contract")
