"""Contract v1 extension governance. Root schemas stay frozen."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_runtime.contracts.extensions import (
    ExtensionConflictError,
    ExtensionDescriptor,
    ExtensionRegistry,
    ExtensionValidationError,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "v1"
MANIFEST = ROOT / "manifest.json"
SCHEMAS = ROOT / "schemas"


def test_root_shape_digest_unchanged() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for rel, digest in manifest["files"].items():
        path = ROOT / rel
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        assert hashlib.sha256(data).hexdigest() == digest


def test_bare_public_namespace_rejected() -> None:
    registry = ExtensionRegistry()
    with pytest.raises(ExtensionConflictError, match="namespace"):
        registry.register(
            ExtensionDescriptor(
                namespace="memory",
                version="1",
                owner="runtime",
                critical=False,
                schema_id="memory.json",
            )
        )


def test_namespace_collision_rejected() -> None:
    registry = ExtensionRegistry()
    desc = ExtensionDescriptor(
        namespace="extensions.virtualbrain.chemicals",
        version="1",
        owner="virtualbrain",
        critical=False,
        schema_id="vb-chem",
    )
    registry.register(desc)
    with pytest.raises(ExtensionConflictError, match="collision"):
        registry.register(desc)


def test_unknown_noncritical_extension_ignored() -> None:
    registry = ExtensionRegistry()
    out = registry.filter_extensions(
        {
            "virtualbrain": {"chemicals": {"CORT": 0.1}},
            "acme.probe": {"critical": False, "version": "1", "payload": {"x": 1}},
        }
    )
    assert "acme.probe" not in out
    assert out["virtualbrain"]["chemicals"]["CORT"] == 0.1


def test_unknown_critical_extension_rejected() -> None:
    registry = ExtensionRegistry()
    with pytest.raises(ExtensionValidationError, match="critical"):
        registry.filter_extensions(
            {"acme.probe": {"critical": True, "version": "1", "payload": {}}}
        )


def test_registered_extension_schema_failure_rejected() -> None:
    registry = ExtensionRegistry()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["level"],
        "properties": {"level": {"type": "number"}},
    }
    registry.register(
        ExtensionDescriptor(
            namespace="extensions.example.meter",
            version="1",
            owner="example",
            critical=True,
            schema_id="example.meter.v1",
        ),
        schema=schema,
    )
    with pytest.raises(ExtensionValidationError):
        registry.filter_extensions({"example.meter": {"wrong": True}})


def test_registered_extension_valid_payload_kept() -> None:
    registry = ExtensionRegistry()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["level"],
        "properties": {"level": {"type": "number"}},
    }
    registry.register(
        ExtensionDescriptor(
            namespace="extensions.example.meter",
            version="1",
            owner="example",
            critical=False,
            schema_id="example.meter.v1",
        ),
        schema=schema,
    )
    out = registry.filter_extensions({"example.meter": {"level": 0.2}})
    assert out == {"example.meter": {"level": 0.2}}


def test_extension_payload_must_not_carry_secrets() -> None:
    registry = ExtensionRegistry()
    with pytest.raises(ExtensionValidationError, match="secret"):
        registry.filter_extensions({"virtualbrain": {"api_key": "sk-secret"}})
