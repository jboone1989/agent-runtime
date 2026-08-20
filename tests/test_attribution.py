from __future__ import annotations

from agent_runtime.cognition.attribution import attribute


def test_self_only_change_is_self_attribution() -> None:
    result = attribute(["compute"], [])
    assert result.cause_type == "SELF"
    assert result.confidence > 0.5


def test_world_only_change_is_world_attribution() -> None:
    result = attribute([], ["api_latency_ms"])
    assert result.cause_type == "WORLD"


def test_co_occurrence_is_unknown() -> None:
    result = attribute(["compute"], ["api_latency_ms"])
    assert result.cause_type == "UNKNOWN"
