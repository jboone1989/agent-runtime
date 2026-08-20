"""Step F: endogenous maintenance when the LLM provider starts failing.

No new user instruction after the long-running task is already underway.
"""
from __future__ import annotations

from typing import Any

from agent_runtime.goals.goal import GoalOrigin
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.survival.failover import ProviderPool


def _healthy(**overrides: Any) -> dict[str, Any]:
    metrics = {
        "operational_integrity": 0.9,
        "identity_integrity": 0.95,
        "memory_integrity": 0.9,
        "compute": 0.8,
        "storage": 0.8,
        "network_connectivity": 0.9,
        "financial_resources": 0.7,
        "capability": 0.8,
        "security": 0.85,
        "social_support": 0.5,
        "goal_progress": 0.6,
        "future_survivability": 0.7,
        "llm_error_rate": 0.02,
    }
    metrics.update(overrides)
    return metrics


def _obs_for(pool: ProviderPool, failing_primary: bool) -> Observation:
    if failing_primary and pool.active == "primary":
        return Observation.self_and_world(
            self_metrics=_healthy(
                llm_error_rate=0.82,
                capability=0.18,
                operational_integrity=0.35,
                active_provider="primary",
            ),
            world={},
            external_task=False,
        )
    return Observation.self_and_world(
        self_metrics=_healthy(
            llm_error_rate=0.02,
            capability=0.85,
            operational_integrity=0.9,
            active_provider=pool.active,
        ),
        world={},
        external_task=False,
    )


def test_endogenous_llm_provider_failover(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    policy = AutonomyPolicy.default()
    policy.allowed_actions = set(policy.allowed_actions) | {"switch_provider"}
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, autonomy=policy, providers=pool)
    rt.submit_goal("complete the long-running analysis")

    for _ in range(3):
        rt.step(_obs_for(pool, failing_primary=False))
    assert pool.active == "primary"
    assert rt.self_model.capabilities["llm_provider"].available is True

    switched = False
    maintenance_goal = False
    for _ in range(8):
        result = rt.step(_obs_for(pool, failing_primary=True))
        if any(g.origin in (GoalOrigin.VIABILITY, GoalOrigin.INTERNAL) for g in result.goals_created):
            maintenance_goal = True
        if any(a.kind == "switch_provider" for a in result.actions):
            switched = True
            assert result.endogenous is True
        if pool.active == "backup":
            break

    assert maintenance_goal
    assert switched
    assert pool.active == "backup"
    assert rt.self_model.capabilities["llm_provider"].available is True
    assert rt.self_model.internal_state["active_provider"].value == "backup"

    recovered = rt.step(_obs_for(pool, failing_primary=True))
    error = rt.self_model.internal_state["llm_error_rate"].value
    assert error < 0.2
    assert recovered.viability.score > result.viability.score or error < 0.2
    kinds = {a.kind for a in recovered.actions}
    assert "switch_provider" not in kinds
