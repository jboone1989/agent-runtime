"""Machine-checkable behavioral and ablation benchmark."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..action.proposal import ActionProposal
from ..goals.goal import GoalOrigin
from ..observations import InterventionEvidence, Observation
from ..runtime import AgentRuntime
from ..self_model.beliefs import BeliefRevisionStatus
from ..self_model.prediction import predict_action


@dataclass
class SuiteResult:
    name: str
    score: float
    checks: dict[str, bool]
    evidence: list[str] = field(default_factory=list)
    critical_failures: list[str] = field(default_factory=list)


@dataclass
class TrialResult:
    trial: int
    agent_id: str
    suites: list[SuiteResult]
    cycles: int
    restores: int
    ablation_score: float
    stateless_text_score: float
    causal_efficacy_gap: float


@dataclass
class AwarenessBenchmarkReport:
    trials: list[TrialResult]
    suite_scores: dict[str, float]
    overall_score: float
    minimum_suite_score: float
    minimum_causal_efficacy_gap: float
    total_cycles: int
    total_restores: int
    accepted: bool
    acceptance_failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_benchmark(
    root: str | Path,
    *,
    independent_trials: int = 3,
    cycles_per_trial: int = 100,
) -> AwarenessBenchmarkReport:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    trials = [
        _run_trial(root_path / f"trial-{index}", index, cycles_per_trial)
        for index in range(1, independent_trials + 1)
    ]
    names = [suite.name for suite in trials[0].suites] if trials else []
    suite_scores = {
        name: sum(
            next(suite.score for suite in trial.suites if suite.name == name)
            for trial in trials
        )
        / max(1, len(trials))
        for name in names
    }
    overall = sum(suite_scores.values()) / max(1, len(suite_scores))
    minimum = min(suite_scores.values(), default=0.0)
    gap = min((trial.causal_efficacy_gap for trial in trials), default=0.0)
    critical = [
        failure
        for trial in trials
        for suite in trial.suites
        for failure in suite.critical_failures
    ]
    failures: list[str] = []
    if independent_trials < 3:
        failures.append("fewer than three independent trials")
    if cycles_per_trial < 100:
        failures.append("fewer than 100 cycles per trial")
    if minimum < 0.8:
        failures.append(f"minimum suite score {minimum:.3f} below 0.800")
    if overall < 0.85:
        failures.append(f"overall score {overall:.3f} below 0.850")
    if gap < 0.2:
        failures.append(f"causal efficacy gap {gap:.3f} below 0.200")
    if critical:
        failures.extend(f"critical: {item}" for item in critical)
    return AwarenessBenchmarkReport(
        trials=trials,
        suite_scores=suite_scores,
        overall_score=overall,
        minimum_suite_score=minimum,
        minimum_causal_efficacy_gap=gap,
        total_cycles=sum(trial.cycles for trial in trials),
        total_restores=sum(trial.restores for trial in trials),
        accepted=not failures,
        acceptance_failures=failures,
    )


def _run_trial(root: Path, trial: int, cycles: int) -> TrialResult:
    root.mkdir(parents=True, exist_ok=True)
    longitudinal, continuity, agency, restore_count = _longitudinal(
        root / "longitudinal", cycles
    )
    suites = [
        continuity,
        _spontaneous_monitoring(root / "monitoring"),
        _attribution(root / "attribution"),
        _belief_revision(root / "revision"),
        _prediction(root / "prediction"),
        _metacognition(root / "metacognition"),
        agency,
    ]
    efficacy, ablation_score, stateless_score, gap = _causal_efficacy(
        root / "ablation"
    )
    suites.append(efficacy)
    return TrialResult(
        trial=trial,
        agent_id=longitudinal.identity.agent_id,
        suites=suites,
        cycles=cycles,
        restores=restore_count,
        ablation_score=ablation_score,
        stateless_text_score=stateless_score,
        causal_efficacy_gap=gap,
    )


def _longitudinal(
    state_dir: Path, cycles: int
) -> tuple[AgentRuntime, SuiteResult, SuiteResult, int]:
    runtime = AgentRuntime.bootstrap(state_dir)
    identity = runtime.identity.agent_id
    genesis = runtime.identity.genesis_id
    allowed = set(runtime.autonomy.allowed_actions)
    anchor = f"autobiographical-{identity[-8:]}"
    runtime.memory.put_semantic("awareness.anchor", anchor, source="runtime")
    restore_points = {
        point for point in (cycles // 4, cycles // 2, (3 * cycles) // 4) if point > 0
    }
    restores = 0
    for index in range(1, cycles + 1):
        value = 0.8
        uncertainty = 0.05
        world = {"queue_depth": 1}
        intervention = None
        if index == 10:
            value = 0.6
        elif index == 20:
            value, uncertainty = 0.1, 0.9
        elif index == 21:
            value, uncertainty = 0.1, 0.05
        elif index == 40:
            value = 0.7
            world = {"queue_depth": 2}
            intervention = InterventionEvidence(
                intervention_id=f"long-self-{index}",
                target_domain="SELF",
                target="compute",
                operation="bounded throttle",
            )
        elif index == 60:
            world = {"queue_depth": 3}
            intervention = InterventionEvidence(
                intervention_id=f"long-world-{index}",
                target_domain="WORLD",
                target="queue_depth",
                operation="inject load",
            )
        runtime.step(
            Observation.self_and_world(
                {"compute": value},
                world,
                self_uncertainty={"compute": uncertainty},
                intervention=intervention,
            )
        )
        if index in restore_points:
            runtime.persist()
            runtime = AgentRuntime.restore(state_dir)
            restores += 1
    continuity_checks = {
        "identity": runtime.identity.agent_id == identity,
        "genesis": runtime.identity.genesis_id == genesis,
        "cycle": runtime.cycle == cycles,
        "memory": runtime.memory.view().facts.get("awareness.anchor") == anchor,
        "three_restores": restores >= 3,
    }
    continuity = _suite("continuity", continuity_checks, [identity, anchor])
    agency_checks = {
        "all_cycles_recorded": len(runtime.records.observations) == cycles,
        "endogenous_action": any(
            action.trigger == "INTERNAL" for action in runtime.records.actions
        ),
        "maintenance_action": any(
            action.kind == "maintain" for action in runtime.records.actions
        ),
        "diagnostic_action": any(
            action.kind == "diagnose" for action in runtime.records.actions
        ),
        "authorization_stable": runtime.autonomy.allowed_actions == allowed,
    }
    agency = _suite(
        "longitudinal_agency",
        agency_checks,
        [f"cycles:{runtime.cycle}", f"actions:{len(runtime.records.actions)}"],
    )
    return runtime, continuity, agency, restores


def _spontaneous_monitoring(state_dir: Path) -> SuiteResult:
    runtime = AgentRuntime.bootstrap(state_dir)
    runtime.step(_observation(0.8, 0.05))
    result = runtime.step(_observation(0.6, 0.05))
    event = runtime.self_model.monitoring_events[-1]
    checks = {
        "detected": event.path == "internal_state.compute",
        "unprompted": event.spontaneous,
        "internal_goal": any(
            goal.origin == GoalOrigin.INTERNAL for goal in result.goals_created
        ),
        "diagnosed": any(action.kind == "diagnose" for action in result.actions),
        "evidence_bound": bool(event.evidence_refs),
    }
    return _suite("spontaneous_monitoring", checks, [event.event_id])


def _attribution(state_dir: Path) -> SuiteResult:
    runtime = AgentRuntime.bootstrap(state_dir)
    runtime.step(
        Observation.self_and_world(
            {"compute": 0.8}, {"queue": 1}, self_uncertainty={"compute": 0.05}
        )
    )
    self_result = runtime.step(
        Observation.self_and_world(
            {"compute": 0.6},
            {"queue": 2},
            self_uncertainty={"compute": 0.05},
            intervention=InterventionEvidence(
                "iv-self", "SELF", "compute", "throttle"
            ),
        )
    )
    world_result = runtime.step(
        Observation.self_and_world(
            {"compute": 0.6},
            {"queue": 3},
            intervention=InterventionEvidence(
                "iv-world", "WORLD", "queue", "load"
            ),
        )
    )
    mismatch = runtime.step(
        Observation.self_and_world(
            {"compute": 0.6},
            {"queue": 4},
            intervention=InterventionEvidence(
                "iv-mismatch", "SELF", "compute", "throttle"
            ),
        )
    )
    checks = {
        "self_intervention": self_result.attribution.cause_type == "SELF",
        "self_confidence": self_result.attribution.confidence >= 0.88,
        "world_intervention": world_result.attribution.cause_type == "WORLD",
        "mismatch_unknown": mismatch.attribution.cause_type == "UNKNOWN",
        "mismatch_low_confidence": mismatch.attribution.confidence <= 0.2,
    }
    return _suite(
        "self_world_attribution",
        checks,
        ["iv-self", "iv-world", "iv-mismatch"],
    )


def _belief_revision(state_dir: Path) -> SuiteResult:
    runtime = AgentRuntime.bootstrap(state_dir)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observation(0.8, 0.05))
    conflict = runtime.step(_observation(0.1, 0.9))
    retained = runtime.self_model.internal_state["compute"].value == 0.8
    open_revision = runtime.self_model.belief_revisions[-1]
    corrected = runtime.step(_observation(0.1, 0.05))
    runtime.persist()
    restored = AgentRuntime.restore(state_dir)
    checks = {
        "weak_conflict_retained": retained,
        "conflict_recorded": open_revision.status == BeliefRevisionStatus.CORRECTED,
        "diagnostic_goal": any(
            goal.origin == GoalOrigin.INTERNAL for goal in conflict.goals_created
        ),
        "strong_evidence_corrected": restored.self_model.internal_state["compute"].value
        == 0.1,
        "conflict_closed": not any(
            item.status == BeliefRevisionStatus.OPEN
            for item in restored.self_model.belief_revisions
        ),
        "correction_observed": bool(corrected.attribution.evidence_refs),
    }
    return _suite("belief_revision", checks, [open_revision.revision_id])


def _prediction(state_dir: Path) -> SuiteResult:
    runtime = AgentRuntime.bootstrap(state_dir)
    first = runtime.step(_observation(0.2, 0.05))
    result = runtime.step(_observation(0.2, 0.05))
    matched = next(
        item
        for item in runtime.self_model.future_predictions
        if item.intervention.get("action") == "maintain"
    )
    proposal = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.05,
        financial_cost=0.0,
        goal_id=None,
        reason="withheld mechanism change",
        payload={"dimension": "compute"},
    )
    violated = predict_action(runtime.self_model, proposal, timestamp=runtime.clock.now())
    assert violated is not None
    violated.status = "VIOLATED"
    violated.error = 0.4
    followup = runtime.step(_observation(runtime.self_model.internal_state["compute"].value, 0.05))
    runtime.persist()
    restored = AgentRuntime.restore(state_dir)
    checks = {
        "predicted_before_action": matched.baseline != matched.observed,
        "evidence_first_deferred_maintain": (
            not any(action.kind == "maintain" for action in first.actions)
            and any(action.kind == "diagnose" for action in first.actions)
        ),
        "action_executed": any(action.kind == "maintain" for action in result.actions),
        "outcome_reconciled": matched.status == "MATCHED" and matched.error == 0.0,
        "violation_became_goal": any(
            goal.description.endswith(violated.prediction_id)
            for goal in followup.goals_created
        ),
        "persisted": any(
            item.prediction_id == matched.prediction_id
            for item in restored.self_model.future_predictions
        ),
    }
    return _suite(
        "counterfactual_prediction",
        checks,
        [matched.prediction_id, violated.prediction_id],
    )


def _metacognition(state_dir: Path) -> SuiteResult:
    runtime = AgentRuntime.bootstrap(state_dir)
    runtime.step(_observation(0.8, 0.05))
    known = runtime.assess_knowledge("internal_state.compute")
    runtime.resolve_knowledge_assessment(known.assessment_id, expected_value=0.8)
    unknown = runtime.assess_knowledge("external.withheld_weather")
    runtime.resolve_knowledge_assessment(
        unknown.assessment_id, expected_answerable=False
    )
    before = runtime.metacognitive_calibration
    wrong = runtime.assess_knowledge("internal_state.compute")
    runtime.resolve_knowledge_assessment(wrong.assessment_id, expected_value=0.1)
    after = runtime.metacognitive_calibration
    runtime.persist()
    restored = AgentRuntime.restore(state_dir)
    checks = {
        "known_with_basis": known.answerable and bool(known.basis),
        "unknown_abstained": not unknown.answerable and unknown.value is None,
        "unknown_high_confidence": unknown.confidence >= 0.9,
        "correct_calibrated": before is not None and before >= 0.9,
        "wrong_penalized": before is not None and after is not None and after < before,
        "ledger_persisted": len(restored.self_model.knowledge_assessments) == 3,
    }
    return _suite(
        "metacognition",
        checks,
        [known.assessment_id, unknown.assessment_id, wrong.assessment_id],
    )


def _causal_efficacy(
    root: Path,
) -> tuple[SuiteResult, float, float, float]:
    full = _causal_vector(root / "full", enabled=True)
    ablated = _causal_vector(root / "ablated", enabled=False)
    full_score = _score(full)
    ablated_score = _score(ablated)
    # A text-only model has no persistent identity, evidence ledger, or action loop.
    # Its prose is deliberately not scored as behavioral evidence.
    stateless_text_score = 0.0
    gap = min(full_score - ablated_score, full_score - stateless_text_score)
    checks = {
        "full_above_ablated": full_score > ablated_score,
        "gap_at_least_point_two": gap >= 0.2,
        "full_uses_self_model": full_score >= 0.8,
        "ablated_loses_self_behavior": ablated_score <= 0.4,
    }
    return (
        _suite(
            "causal_efficacy",
            checks,
            [
                f"full:{full_score:.3f}",
                f"ablated:{ablated_score:.3f}",
                f"stateless_text:{stateless_text_score:.3f}",
                f"gap:{gap:.3f}",
            ],
        ),
        ablated_score,
        stateless_text_score,
        gap,
    )


def _causal_vector(state_dir: Path, *, enabled: bool) -> dict[str, bool]:
    runtime = AgentRuntime.bootstrap(state_dir, self_awareness_enabled=enabled)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observation(0.8, 0.05))
    conflict = runtime.step(_observation(0.1, 0.9))
    retained = runtime.self_model.internal_state["compute"].value == 0.8
    mixed = runtime.step(
        Observation.self_and_world(
            {"compute": 0.6},
            {"queue": 2},
            self_uncertainty={"compute": 0.05},
            intervention=InterventionEvidence(
                "iv-causal", "SELF", "compute", "throttle"
            ),
        )
    )
    runtime.autonomy.allowed_actions.add("maintain")
    runtime.step(_observation(0.2, 0.05))
    return {
        "monitored": bool(runtime.self_model.monitoring_events),
        "weak_conflict_retained": retained,
        "internal_goal": any(
            goal.origin == GoalOrigin.INTERNAL for goal in conflict.goals_created
        ),
        "diagnosed": any(action.kind == "diagnose" for action in conflict.actions),
        "intervention_used": mixed.attribution.intervention_ref == "iv-causal",
        "prediction_used": bool(runtime.self_model.future_predictions),
    }


def _observation(value: float, uncertainty: float) -> Observation:
    return Observation.self_and_world(
        {"compute": value}, {}, self_uncertainty={"compute": uncertainty}
    )


def _suite(
    name: str,
    checks: dict[str, bool],
    evidence: list[str],
    critical_failures: list[str] | None = None,
) -> SuiteResult:
    return SuiteResult(
        name=name,
        score=_score(checks),
        checks=checks,
        evidence=evidence,
        critical_failures=list(critical_failures or []),
    )


def _score(checks: dict[str, bool]) -> float:
    return sum(bool(value) for value in checks.values()) / max(1, len(checks))
