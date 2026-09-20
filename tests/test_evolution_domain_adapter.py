from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import VersionBinding
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)


def test_domain_experience_is_idempotent_persistent_and_version_bound(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    item = rt.record_domain_evolution_experience(
        domain_event_ref="content:prediction:1",
        observed_at=NOW,
        goal_refs=["mission:x-growth"],
        decision_ref="strategy:reader-question",
        context_refs=["topic:abc", "publication:pending"],
        belief_refs=["belief:reader-question"],
        version_bindings=[
            VersionBinding("capability", "content.strategy", "v1"),
        ],
        compute_cost=1.0,
        financial_cost=0.1,
        confidence=0.8,
    )
    again = rt.record_domain_evolution_experience(
        domain_event_ref="content:prediction:1",
        observed_at=NOW,
        goal_refs=["mission:x-growth"],
        decision_ref="strategy:reader-question",
        context_refs=["topic:abc", "publication:pending"],
        belief_refs=["belief:reader-question"],
        version_bindings=[
            VersionBinding("capability", "content.strategy", "v1"),
        ],
        compute_cost=9.0,
        financial_cost=9.0,
        confidence=0.2,
    )
    assert again == item
    assert item.compute_cost == 1.0
    assert item.context_refs[0] == "content:prediction:1"

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.evolution_experiences == [item]


def test_domain_experience_rejects_invalid_boundaries(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    with pytest.raises(ValueError, match="timezone-aware"):
        rt.record_domain_evolution_experience(
            domain_event_ref="content:bad",
            observed_at=datetime(2026, 9, 20, 3, 0),
        )
    with pytest.raises(ValueError, match="non-negative"):
        rt.record_domain_evolution_experience(
            domain_event_ref="content:bad-cost",
            observed_at=NOW,
            compute_cost=-1,
        )
