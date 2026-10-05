"""Runtime-owned memory capability; provider construction belongs to the host."""
from __future__ import annotations

from collections.abc import Mapping

from ..capabilities.protocol import CapabilityCall, CapabilityProtocolError, CapabilityReply
from ..capabilities.standard import MEMORY_CAPABILITY_ID


class MemoryCapabilityProvider:
    provider_id = "runtime.memory.canonical"

    def __init__(self, service, *, subject_id, remember=None):
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ValueError("memory capability requires an authenticated subject binding")
        self.service, self.subject_id, self.remember = service, subject_id, remember

    def manifest(self):
        return {"provider": self.provider_id, "capabilities": [{
            "capability_id": MEMORY_CAPABILITY_ID,
            "description": "Scoped canonical memory, exact evidence reads and committed provider views",
            "side_effect_level": "LOCAL_STATE", "required_permissions": []}]}

    def invoke(self, capability_id, payload=None):
        if capability_id != MEMORY_CAPABILITY_ID:
            raise CapabilityProtocolError("provider is bound only to agent.memory")
        call = CapabilityCall.from_mapping(payload or {}, expected_capability_id=MEMORY_CAPABILITY_ID)
        if call.subject_id != self.subject_id:
            return CapabilityReply.failure(call, provider=self.provider_id, error_code="SUBJECT_MISMATCH",
                                           error_message="memory capability belongs to another subject").to_mapping()
        p, operation = call.payload, call.operation
        if operation == "recall":
            items = self.service.search(_string(p, "query"), topic=_string(p, "topic", default=""),
                limit=_integer(p, "limit", default=8, minimum=1, maximum=100),
                node_types=_strings(p, "node_types"), source_ids=_strings(p, "source_ids"))
            result = {"items": items, "progress": self.service.progress()}
        elif operation in {"read", "snapshot", "expand", "relations"}:
            memory_id = _string(p, "memory_id")
            if operation == "snapshot":
                result = self.service.snapshot(memory_id)
            else:
                value = getattr(self.service, operation)(memory_id, offset=_integer(p, "offset", default=0))
                result = {"items": value} if isinstance(value, list) else value
        elif operation == "changes":
            result = {"items": self.service.changes(_integer(p, "since", default=0),
                limit=_integer(p, "limit", default=8, minimum=1, maximum=100))}
        elif operation == "health":
            if p:
                raise CapabilityProtocolError("health payload must be empty")
            result = {"status": "ok", "progress": self.service.progress()}
        elif operation == "remember":
            if self.remember is None:
                return CapabilityReply.failure(call, provider=self.provider_id, error_code="WRITE_POLICY_REQUIRED",
                    error_message="bind authenticated canonical write/adoption policy before remembering").to_mapping()
            record = p.get("record")
            if not isinstance(record, Mapping):
                raise CapabilityProtocolError("record must be a mapping")
            if any(k in record and record[k] != v for k, v in self.service.progress()["scope"].items()):
                raise CapabilityProtocolError("record scope differs from capability binding")
            result = self.remember(dict(record), call)
        else:
            return CapabilityReply.failure(call, provider=self.provider_id, error_code="UNKNOWN_OPERATION",
                                           error_message=operation).to_mapping()
        return CapabilityReply.success(call, provider=self.provider_id, payload=result).to_mapping()


def _string(payload, key, *, default=None):
    value = payload.get(key, default)
    if not isinstance(value, str) or (default is None and not value.strip()):
        raise CapabilityProtocolError(key + " must be a string")
    return value


def _strings(payload, key):
    value = payload.get(key, [])
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not x.strip() for x in value):
        raise CapabilityProtocolError(key + " must be a bounded string array")
    return value


def _integer(payload, key, *, default, minimum=0, maximum=None):
    value = payload.get(key, default)
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise CapabilityProtocolError(key + " is out of range")
    return value
