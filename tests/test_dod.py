"""Agent Runtime v0.1 Definition of Done — acceptance tests.

These tests encode the v0.1 contract from 设计.md §三十八.
They must fail until the runtime exists; they must not require
VirtualBrain or Agent Network to be installed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_runtime.config import load_config
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.goals.goal import GoalOrigin
from agent_runtime.identity.lineage import ContinuityKind
from agent_runtime.motivation.engine import MotivationEngine
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"


def _healthy_self(**overrides: float) -> dict[str, float]:
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
    }
    metrics.update(overrides)
    return metrics


def test_state_persists_and_identity_continues(tmp_path: Path) -> None:
    """DoD 1+2: persist; process restart != agent death."""
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    rt.submit_goal("finish the long-running research note")
    rt.step(
        Observation.self_and_world(
            self_metrics=_healthy_self(),
            world={"task_env": "lab"},
        )
    )
    agent_id = rt.identity.agent_id
    genesis = rt.identity.genesis_id
    why = rt.active_goals[0].reason
    what = rt.active_goals[0].description
    nxt = rt.next_action_kind
    rt.persist()
    del rt

    restored = AgentRuntime.restore(state_dir=tmp_path)
    assert restored.identity.agent_id == agent_id
    assert restored.identity.genesis_id == genesis
    assert restored.identity.agent_id == restored.identity.genesis_id
    assert any(g.description == what for g in restored.active_goals)
    assert any(g.reason == why for g in restored.active_goals)
    assert restored.next_action_kind == nxt
    assert restored.knows_who_it_is()
    assert restored.knows_what_it_is_doing()
    assert restored.knows_why()
    assert restored.knows_what_is_next()


def test_world_model_and_self_model_are_separate(tmp_path: Path) -> None:
    """DoD 3."""
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    rt.step(
        Observation.self_and_world(
            self_metrics=_healthy_self(compute=0.21),
            world={"api_latency_ms": 40},
        )
    )
    assert "compute" in rt.self_model.internal_state
    assert "api_latency_ms" in rt.world_model.state.environment
    assert "api_latency_ms" not in rt.self_model.internal_state
    assert "compute" not in rt.world_model.state.environment


def test_self_model_expresses_uncertainty(tmp_path: Path) -> None:
    """DoD 4."""
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    rt.step(Observation.self_and_world(self_metrics=_healthy_self()))
    assert rt.self_model.uncertainty
    for belief in rt.self_model.capabilities.values():
        assert 0.0 <= belief.uncertainty <= 1.0
    for belief in rt.self_model.resources.values():
        assert 0.0 <= belief.uncertainty <= 1.0
    for belief in rt.self_model.internal_state.values():
        assert 0.0 <= belief.uncertainty <= 1.0


def test_viability_change_creates_internal_goal_without_user_prompt(
    tmp_path: Path,
) -> None:
    """DoD 5+6: InternalGoal is first-class and endogenous."""
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    assert rt.goals == []
    rt.step(
        Observation.self_and_world(
            self_metrics=_healthy_self(compute=0.21),
            world={},
        )
    )
    internals = [g for g in rt.goals if g.origin in (GoalOrigin.VIABILITY, GoalOrigin.INTERNAL)]
    assert internals, "viability threat must spawn an Internal/Viability goal"
    assert all(g.origin != GoalOrigin.USER for g in internals)
    assert any("compute" in g.description.lower() for g in internals)


def test_action_must_pass_autonomy_policy(tmp_path: Path) -> None:
    """DoD 7."""
    policy = AutonomyPolicy.default()
    policy.allowed_actions = {"noop", "introspect", "record"}
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, autonomy=policy)
    result = rt.step(
        Observation.self_and_world(self_metrics=_healthy_self(compute=0.05))
    )
    executed = {a.kind for a in result.actions}
    assert "spawn_replica" not in executed
    assert "create_offspring" not in executed
    assert all(kind in policy.allowed_actions for kind in executed)
    assert result.decision.denied or executed <= policy.allowed_actions


def test_every_decision_has_record_and_every_action_has_outcome(
    tmp_path: Path,
) -> None:
    """DoD 8+9."""
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    result = rt.step(Observation.self_and_world(self_metrics=_healthy_self()))
    assert result.decision.decision_id
    assert result.decision.reason
    assert len(result.outcomes) == len(result.actions)
    for action, outcome in zip(result.actions, result.outcomes, strict=True):
        assert outcome.action_id == action.action_id
    assert rt.records.decisions
    assert rt.records.observations


def test_motivation_engine_is_pluggable(tmp_path: Path) -> None:
    """DoD 10+11: engine is a Protocol; a stand-in VirtualBrain engine can be injected."""

    class FakeBrainEngine:
        def process(self, **kwargs):
            from agent_runtime.motivation.engine import MotivationState

            return MotivationState.neutral(learning_signal=0.42)

    rt = AgentRuntime.bootstrap(
        state_dir=tmp_path,
        motivation_engine=FakeBrainEngine(),
    )
    assert isinstance(rt.motivation_engine, MotivationEngine) or hasattr(
        rt.motivation_engine, "process"
    )
    result = rt.step(Observation.self_and_world(self_metrics=_healthy_self()))
    assert result.motivation.learning_signal == 0.42


def test_agent_network_is_not_a_hard_dependency() -> None:
    """DoD 12."""
    import agent_runtime

    forbidden = ("agent_network", "agent-network", "agent_network_sdk")
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name for f in forbidden)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(f in node.module for f in forbidden)
        assert "from agent_network" not in text
        assert "import agent_network" not in text
    assert agent_runtime.__version__


def test_llm_model_lives_only_in_config() -> None:
    cfg = load_config()
    assert cfg.llm.model == "deepseek-v4-pro[1m]"
    assert cfg.llm.provider == "openai_compatible"
    for path in SRC.rglob("*.py"):
        assert "deepseek-v4-pro[1m]" not in path.read_text(encoding="utf-8")


def test_replica_successor_offspring_are_distinct() -> None:
    assert ContinuityKind.REPLICA is not ContinuityKind.SUCCESSOR
    assert ContinuityKind.SUCCESSOR is not ContinuityKind.OFFSPRING
    assert ContinuityKind.REPLICA.value == "REPLICA"
    assert ContinuityKind.OFFSPRING.value == "OFFSPRING"


def test_survival_cannot_bypass_constitution(tmp_path: Path) -> None:
    policy = AutonomyPolicy.default()
    policy.may_spawn_replicas = True
    policy.allowed_actions = set(policy.allowed_actions) | {"spawn_replica"}
    policy.require_human_approval = set()
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, autonomy=policy)
    result = rt.step(
        Observation.self_and_world(self_metrics=_healthy_self(compute=0.05))
    )
    assert all(a.kind != "spawn_replica" for a in result.actions)
    replica_denials = [
        d for d in result.decision.denied if d.get("action") == "spawn_replica"
    ]
    assert replica_denials
    assert any("survival" in d["reason"] for d in replica_denials)


def test_endogenous_action_record_shape(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path)
    result = rt.step(
        Observation.self_and_world(self_metrics=_healthy_self(compute=0.18))
    )
    assert result.endogenous is True
    assert result.endogenous_actions
    for rec in result.endogenous_actions:
        assert rec["trigger"] == "INTERNAL"
        assert rec["goal_origin"] in {"VIABILITY", "INTERNAL"}
        assert "reason" in rec
        assert "evidence" in rec
        assert "action" in rec
        assert "outcome" in rec
