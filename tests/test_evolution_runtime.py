from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    EvolutionFactKind,
    EvolutionHypothesisStatus,
    EvolutionNeedStatus,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)


def _runtime(tmp_path):
    return AgentRuntime.bootstrap(
        state_dir=tmp_path,
        clock=FrozenClock(NOW),
    )


def test_runtime_turns_evolution_need_into_internal_goal_and_drive(tmp_path):
    rt = _runtime(tmp_path)
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="credential-handoff",
        summary="Provider credential requires a human handoff",
        severity=0.6,
        evidence_refs=["human-request:1"],
    )
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="credential-handoff",
        summary="Another provider credential requires the same human handoff",
        severity=0.6,
        evidence_refs=["human-request:2"],
    )
    [need] = rt.evolution_needs

    result = rt.step()

    assert result.motivation.drives["evolution"] > 0
    goals = [goal for goal in rt.goals if goal.dimension == "evolution"]
    assert len(goals) == 1
    assert goals[0].origin.value == "INTERNAL"
    assert need.need_id in goals[0].evidence_refs
    assert goals[0].auto_complete is False
    assert "change itself as success" in goals[0].success_criteria


def test_evolution_state_survives_runtime_restart(tmp_path):
    rt = _runtime(tmp_path)
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.RESOURCE_FRICTION,
        subject="model-budget",
        summary="Model burn rate leaves too little runway",
        severity=0.9,
        evidence_refs=["budget:1"],
    )
    insight = rt.propose_evolution_insight(
        statement="A cheaper fallback may extend runway",
        proposed_change="shadow-test a cheaper model provider",
        evidence_refs=["budget:1"],
        expected_fitness={"runway": 0.2, "reliability": 0.0},
    )
    rt.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))

    assert restored.evolution_needs[0].statement == rt.evolution_needs[0].statement
    assert restored.evolution_hypotheses[0].hypothesis_id == insight.hypothesis_id
    assert restored.evolution_hypotheses[0].status == EvolutionHypothesisStatus.PROPOSED


def test_supported_trial_can_resolve_need_but_unverified_insight_cannot(tmp_path):
    rt = _runtime(tmp_path)
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="manual-deploy",
        summary="Deployment still depends on a manual operator step",
        severity=0.9,
        need_hint="Reduce manual deployment dependency",
        evidence_refs=["deploy:manual"],
    )
    [need] = rt.evolution_needs
    insight = rt.propose_evolution_insight(
        need_id=need.need_id,
        statement="Approval-bound deployment could remove routine manual work",
        proposed_change="trial an approval-bound deployment workflow",
        evidence_refs=["deploy:manual"],
        expected_fitness={"autonomy": 0.3, "safety": 0.1},
    )

    try:
        rt.adopt_evolution_hypothesis(insight.hypothesis_id)
    except PermissionError:
        pass
    else:
        raise AssertionError("unverified insight must not be adoptable")

    trial = rt.evaluate_evolution_trial(
        insight.hypothesis_id,
        fitness_before={"autonomy": 0.3, "safety": 0.8},
        fitness_after={"autonomy": 0.7, "safety": 0.85},
        evidence_refs=["test:passed", "deploy:shadow"],
    )
    assert trial.status == EvolutionHypothesisStatus.SUPPORTED
    rt.adopt_evolution_hypothesis(insight.hypothesis_id)

    assert need.status == EvolutionNeedStatus.RESOLVED
    assert rt.evolution_needs == []


def test_runtime_capability_gap_becomes_evolution_fact(tmp_path):
    from agent_runtime.action.proposal import ActionProposal

    rt = _runtime(tmp_path)
    proposal = ActionProposal(
        kind="secure.operator.secret_handoff",
        resource="operator",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="credential handoff is blocked",
        evidence=["human-request:cerebras"],
    )

    rt._detect_capability_gaps([proposal], NOW)

    assert any(
        fact.kind == EvolutionFactKind.CAPABILITY_GAP
        and fact.subject == "secure.operator.secret_handoff"
        for fact in rt.evolution.state.facts
    )
    assert any(
        pattern.subject == "secure.operator.secret_handoff"
        for pattern in rt.evolution.state.patterns
    )
