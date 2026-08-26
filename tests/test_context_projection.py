"""Bounded context projection prevents authority and secret leakage."""
from __future__ import annotations

import json

from agent_runtime.backends import AgentRunMode
from agent_runtime.context import ContextPolicy, ContextProjector, ContextSnapshot


def test_projection_redacts_secrets_recursively() -> None:
    snapshot = ContextSnapshot(
        {
            "identity": {"agent_id": "agent-1", "api_key": "secret-value"},
            "provider": {
                "nested": {"access_token": "secret-token", "health": 0.9}
            },
        }
    )
    projected = ContextProjector().project(snapshot, mode=AgentRunMode.ADVISORY)
    blob = json.dumps(projected.to_dict())
    assert "secret-value" not in blob
    assert "secret-token" not in blob
    assert "api_key" not in blob
    assert projected.sections["provider"]["nested"]["health"] == 0.9


def test_projection_selects_sections_and_records_omissions() -> None:
    snapshot = ContextSnapshot(
        {"identity": {"agent_id": "a"}, "goals": ["g"], "private": {"x": 1}}
    )
    projected = ContextProjector().project(
        snapshot,
        mode=AgentRunMode.EXECUTION,
        policy=ContextPolicy(allowed_sections=frozenset({"identity", "goals"})),
    )
    assert set(projected.sections) == {"identity", "goals"}
    assert projected.omitted_sections == ("private",)


def test_projection_bounds_lists_strings_and_total_sections() -> None:
    snapshot = ContextSnapshot(
        {
            "identity": {"agent_id": "agent-1"},
            "history": [f"event-{index}" for index in range(20)],
            "large": "x" * 5000,
        }
    )
    projected = ContextProjector().project(
        snapshot,
        mode=AgentRunMode.ADVISORY,
        policy=ContextPolicy(
            section_priority=("identity", "history", "large"),
            max_items_per_list=3,
            max_string_chars=32,
            max_serialized_chars=512,
        ),
    )
    assert projected.sections["identity"]["agent_id"] == "agent-1"
    assert projected.sections["history"] == ["event-17", "event-18", "event-19"]
    if "large" in projected.sections:
        assert projected.sections["large"].endswith("...[truncated]")
    else:
        assert "large" in projected.omitted_sections

