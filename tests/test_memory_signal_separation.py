"""Memory capacity cannot masquerade as autobiographical integrity."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.perception.encoder import encode_builtin
from agent_runtime.perception.percept import Percept
from agent_runtime.regulation.observation import build_delta


NOW = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)


def test_capacity_and_consistency_write_distinct_self_metrics() -> None:
    resource = Percept.from_raw(
        observation_id="percept-resource",
        modality="resource",
        kind="RESOURCE_LEVEL",
        timestamp=NOW,
        attributes={"channel": "memory", "remaining": 0.8},
        confidence=1.0,
        salience=0.2,
        entity_id="memory",
        metadata={"sensor_id": "resource_sensor"},
    )
    memory = Percept.from_raw(
        observation_id="percept-memory",
        modality="memory",
        kind="MEMORY_STATUS",
        timestamp=NOW,
        attributes={
            "retrieval_failure": 0.0,
            "corruption": 0.0,
            "write_failure": 0.0,
            "consistency": 1.0,
            "read_latency": 0.05,
        },
        confidence=1.0,
        salience=0.2,
        entity_id="memory_store",
        metadata={"sensor_id": "memory_sensor"},
    )
    signals = [
        *encode_builtin(resource, [resource.percept_id]),
        *encode_builtin(memory, [memory.percept_id]),
    ]
    delta = build_delta(signals=signals, escalations=[], local_actions=[])

    assert next(item for item in signals if item.entity_id == "memory").kind == "MEMORY_CAPACITY"
    assert delta.self_updates["memory_capacity"] == 0.8
    assert delta.self_updates["memory_integrity"] == 1.0


def test_legacy_memory_usage_is_safe_capacity_alias() -> None:
    resource = Percept.from_raw(
        observation_id="legacy-resource",
        modality="resource",
        kind="RESOURCE_LEVEL",
        timestamp=NOW,
        attributes={"channel": "memory", "remaining": 0.8},
        confidence=1.0,
        salience=0.2,
        entity_id="memory",
        metadata={"sensor_id": "resource_sensor"},
    )
    signal = encode_builtin(resource, [resource.percept_id])[0]
    signal.kind = "MEMORY_USAGE"
    delta = build_delta(signals=[signal], escalations=[], local_actions=[])

    assert delta.self_updates == {"memory_capacity": 0.8}
