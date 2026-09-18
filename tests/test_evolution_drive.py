from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.evolution import (
    EvolutionEngine,
    EvolutionFactKind,
    EvolutionHypothesisStatus,
    EvolutionNeedStatus,
    EvolutionOrigin,
    EvolutionState,
)


NOW = datetime(2026, 9, 18, 1, 30, tzinfo=timezone.utc)


def test_human_api_key_friction_induces_structural_need():
    engine = EvolutionEngine(induction_count=2)

    first = engine.observe_fact(
        cycle=10,
        observed_at=NOW,
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="credential-handoff",
        summary="Cerebras API key must be handed to the agent manually",
        severity=0.55,
        evidence_refs=["human-request:cerebras"],
    )
    assert first.subject == "credential-handoff"
    assert engine.open_needs() == []

    engine.observe_fact(
        cycle=11,
        observed_at=NOW,
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="credential-handoff",
        summary="A second provider credential again required human intervention",
        severity=0.60,
        evidence_refs=["human-request:provider-2"],
    )

    [need] = engine.open_needs()
    assert need.origin == EvolutionOrigin.INDUCTIVE
    assert "Reduce recurring human dependency" in need.statement
    assert need.evidence_refs == [
        "human-request:cerebras",
        "human-request:provider-2",
    ]
    assert need.abstraction_chain[0] == "fact: credential-handoff"
    assert "observed 2 time(s)" in need.abstraction_chain[1]
    assert need.abstraction_chain[-1].startswith("need: ")


def test_severe_single_capability_gap_can_induce_without_repetition():
    engine = EvolutionEngine(severe_single_fact=0.75)
    engine.observe_fact(
        cycle=4,
        observed_at=NOW,
        kind=EvolutionFactKind.CAPABILITY_GAP,
        subject="secure.operator.secret_handoff",
        summary="Agent cannot safely receive protected API credentials",
        severity=0.90,
        target_capability="secure.operator.secret_handoff",
        evidence_refs=["gap:secret-handoff"],
    )

    [need] = engine.open_needs()
    assert need.target_capability == "secure.operator.secret_handoff"
    assert need.statement == (
        "Acquire or develop capability 'secure.operator.secret_handoff'"
    )


def test_low_frequency_reflection_surfaces_latent_need_without_fresh_failure():
    state = EvolutionState(last_improvement_cycle=0)
    engine = EvolutionEngine(
        state,
        induction_count=5,
        reflection_interval=10,
        stagnation_cycles=20,
    )
    engine.observe_fact(
        cycle=3,
        observed_at=NOW,
        kind=EvolutionFactKind.OBSERVATION,
        subject="operator-friction",
        summary="Operator workflow contains avoidable manual steps",
        severity=0.30,
        evidence_refs=["obs:3"],
    )
    assert engine.open_needs() == []

    created = engine.reflect(
        cycle=10,
        observed_at=NOW,
        evidence_refs=["reflection:10"],
    )

    assert len(created) == 1
    assert created[0].origin == EvolutionOrigin.REFLECTIVE
    assert "operator-friction" in created[0].statement


def test_stagnation_itself_becomes_reflection_material():
    engine = EvolutionEngine(
        EvolutionState(last_improvement_cycle=2),
        reflection_interval=5,
        stagnation_cycles=10,
    )

    created = engine.reflect(
        cycle=15,
        observed_at=NOW,
        evidence_refs=["memory:long-horizon"],
    )

    assert len(created) == 1
    assert created[0].origin == EvolutionOrigin.REFLECTIVE
    assert "evidence-backed improvement" in created[0].statement
    assert any(
        fact.kind == EvolutionFactKind.STAGNATION
        for fact in engine.state.facts
    )
def test_spontaneous_aha_without_direct_evidence_is_allowed_but_low_confidence():
    engine = EvolutionEngine()

    insight = engine.propose_insight(
        cycle=20,
        statement="A dedicated secret broker may reduce repeated operator friction",
        proposed_change="experiment with a write-only credential broker",
        evidence_refs=[],
        confidence=0.9,
        expected_fitness={"autonomy": 0.2, "security": 0.2},
    )

    assert insight.origin == EvolutionOrigin.REFLECTIVE
    assert insight.status == EvolutionHypothesisStatus.PROPOSED
    assert insight.confidence == 0.35
    with pytest.raises(PermissionError, match="evidence-supported"):
        engine.adopt(insight.hypothesis_id, cycle=21)


def test_evolution_trial_uses_multidimensional_fitness_not_one_score():
    engine = EvolutionEngine()
    insight = engine.propose_insight(
        cycle=20,
        statement="Use a bounded coding provider for self-maintenance",
        proposed_change="enable code.read/edit/test/commit in an isolated worktree",
        evidence_refs=["need:self-maintenance"],
        expected_fitness={"autonomy": 0.3, "safety": 0.1},
    )
    supported = engine.evaluate_trial(
        insight.hypothesis_id,
        cycle=30,
        fitness_before={"autonomy": 0.40, "safety": 0.80},
        fitness_after={"autonomy": 0.65, "safety": 0.85},
        evidence_refs=["test:760-pass", "review:bounded"],
    )

    assert supported.status == EvolutionHypothesisStatus.SUPPORTED
    assert supported.improved_dimensions == ["autonomy", "safety"]
    assert supported.regressed_dimensions == []
    adopted = engine.adopt(insight.hypothesis_id, cycle=31)
    assert adopted.status == EvolutionHypothesisStatus.ADOPTED
    assert engine.state.last_improvement_cycle == 31


def test_mixed_fitness_change_is_inconclusive_not_silently_adopted():
    engine = EvolutionEngine()
    insight = engine.propose_insight(
        cycle=1,
        statement="Try a faster provider",
        proposed_change="switch provider in a shadow trial",
        evidence_refs=["obs:latency"],
    )
    trial = engine.evaluate_trial(
        insight.hypothesis_id,
        cycle=2,
        fitness_before={"latency_fitness": 0.4, "reliability": 0.9},
        fitness_after={"latency_fitness": 0.8, "reliability": 0.7},
        evidence_refs=["trial:2"],
    )
    assert trial.status == EvolutionHypothesisStatus.INCONCLUSIVE
    assert trial.improved_dimensions == ["latency_fitness"]
    assert trial.regressed_dimensions == ["reliability"]
    with pytest.raises(PermissionError):
        engine.adopt(insight.hypothesis_id, cycle=3)


def test_resolving_need_reduces_open_need_set():
    engine = EvolutionEngine(severe_single_fact=0.5)
    engine.observe_fact(
        cycle=1,
        observed_at=NOW,
        kind=EvolutionFactKind.RESOURCE_FRICTION,
        subject="model-token-budget",
        summary="Current model usage burns through the daily budget too quickly",
        severity=0.8,
        evidence_refs=["budget:burn"],
    )
    [need] = engine.open_needs()
    engine.resolve_need(need.need_id, cycle=4)

    assert engine.open_needs() == []
    assert need.status == EvolutionNeedStatus.RESOLVED
    assert need.resolved_cycle == 4
