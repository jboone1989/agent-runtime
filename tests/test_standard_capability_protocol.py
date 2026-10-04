from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.memory import MemoryCapabilityClient
from agent_runtime.capabilities.protocol import (
    CAPABILITY_PROTOCOL_VERSION,
    CapabilityCall,
    CapabilityProtocolError,
    CapabilityReply,
)
from agent_runtime.capabilities.standard import MEMORY_CAPABILITY_ID


class MemoryProvider:
    provider_id = "test.memory"

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def manifest(self):
        return {
            "provider": self.provider_id,
            "capabilities": [{
                "capability_id": MEMORY_CAPABILITY_ID,
                "side_effect_level": "LOCAL_STATE",
                "required_permissions": [],
            }],
        }

    def invoke(self, capability_id, payload=None):
        call = CapabilityCall.from_mapping(
            payload or {}, expected_capability_id=MEMORY_CAPABILITY_ID
        )
        if call.operation == "remember":
            record = dict(call.payload["record"])
            memory_id = record["memory_id"]
            self.records[memory_id] = record
            return CapabilityReply.success(
                call,
                provider=self.provider_id,
                payload={"created": True, "memory_id": memory_id},
            ).to_mapping()
        if call.operation == "recall":
            query = str(call.payload["query"]).casefold()
            items = [
                row for row in self.records.values()
                if query in str(row.get("text") or "").casefold()
            ]
            return CapabilityReply.success(
                call,
                provider=self.provider_id,
                payload={"items": items[: int(call.payload["limit"])]},
            ).to_mapping()
        if call.operation == "read":
            row = self.records[str(call.payload["memory_id"])]
            return CapabilityReply.success(
                call,
                provider=self.provider_id,
                payload={"memory": row},
            ).to_mapping()
        if call.operation == "changes":
            return CapabilityReply.success(
                call,
                provider=self.provider_id,
                payload={"items": []},
            ).to_mapping()
        if call.operation == "health":
            return CapabilityReply.success(
                call,
                provider=self.provider_id,
                payload={"status": "ok"},
            ).to_mapping()
        return CapabilityReply.failure(
            call,
            provider=self.provider_id,
            error_code="UNKNOWN_OPERATION",
            error_message=call.operation,
        ).to_mapping()


def test_language_neutral_memory_client_routes_through_application_runtime(tmp_path):
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="agent:test",
        max_side_effect_level="LOCAL_STATE",
    )
    runtime.register_provider(MemoryProvider())
    client = MemoryCapabilityClient(runtime)

    created = client.remember({
        "memory_id": "memory:1",
        "kind": "FACT",
        "text": "A checkpoint restores worker state.",
    })
    assert created["memory_id"] == "memory:1"
    assert client.recall("checkpoint")[0]["memory_id"] == "memory:1"
    assert client.read("memory:1")["memory"]["text"].startswith("A checkpoint")
    assert client.health()["status"] == "ok"

    assert runtime.receipts
    assert runtime.receipts[-1]["capability_id"] == MEMORY_CAPABILITY_ID
    assert (
        runtime.receipts[-1]["request"]["capability_protocol_version"]
        == CAPABILITY_PROTOCOL_VERSION
    )


def test_reply_correlation_rejects_cross_request_mixup():
    call = CapabilityCall.create(
        subject_id="agent:test",
        capability_id=MEMORY_CAPABILITY_ID,
        operation="health",
        request_id="request-a",
    )
    reply = CapabilityReply.success(
        call,
        provider="test.memory",
        payload={"status": "ok"},
    ).to_mapping()
    reply["request_id"] = "request-b"
    with pytest.raises(CapabilityProtocolError, match="request id"):
        CapabilityReply.from_mapping(reply, expected_call=call)


def test_capability_envelope_rejects_hidden_extra_fields():
    call = CapabilityCall.create(
        subject_id="agent:test",
        capability_id=MEMORY_CAPABILITY_ID,
        operation="health",
    ).to_mapping()
    call["provider_specific_shortcut"] = True
    with pytest.raises(CapabilityProtocolError, match="fields mismatch"):
        CapabilityCall.from_mapping(call)


def test_capability_json_schemas_validate_protocol_examples():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parents[1] / "contracts"

    call = CapabilityCall.create(
        subject_id="agent:test",
        capability_id=MEMORY_CAPABILITY_ID,
        operation="recall",
        payload={
            "query": "checkpoint",
            "limit": 8,
            "topic": "",
            "node_types": [],
            "source_ids": [],
        },
        request_id="request-schema",
    )
    reply = CapabilityReply.success(
        call,
        provider="test.memory",
        payload={"items": []},
    )

    jsonschema.Draft202012Validator(
        json.loads((root / "capability_call_v1.schema.json").read_text())
    ).validate(call.to_mapping())
    jsonschema.Draft202012Validator(
        json.loads((root / "memory_call_v1.schema.json").read_text())
    ).validate(call.to_mapping())
    jsonschema.Draft202012Validator(
        json.loads((root / "capability_reply_v1.schema.json").read_text())
    ).validate(reply.to_mapping())
