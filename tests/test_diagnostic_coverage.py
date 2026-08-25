from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.coverage import (
    CoverageProbeStatus,
    CoverageResult,
    DiagnosticCoverageEngine,
)
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.records import ActionRecord, DecisionRecord, OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)


class _RecordingProvider:
    def __init__(self) -> None:
        self.prompt = ""

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompt = prompt
        return "ok"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def _proposal(*, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="coverage test",
        payload={"dimension": "BOUNDARY"},
        trigger=trigger,
        evidence=["test:coverage"],
    )


def _action(*, cycle: int = 1) -> ActionRecord:
    return ActionRecord(
        action_id=f"historical_diagnosis_{cycle}",
        cycle=cycle,
        timestamp=NOW,
        kind="diagnose",
        resource="self",
        goal_id=None,
        payload={"dimension": "BOUNDARY"},
        compute_cost=0.03,
        financial_cost=0.0,
        trigger="INTERNAL",
        goal_origin=None,
        reason="historical diagnosis",
        evidence=["observation:boundary"],
    )


def _outcome(
    *,
    cycle: int = 1,
    value=None,
    uncertainty=None,
    evidence_refs: list[str] | None = None,
) -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id=f"historical_outcome_{cycle}",
        action_id=f"historical_diagnosis_{cycle}",
        cycle=cycle,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={
            "dimension": "BOUNDARY",
            "target_path": "internal_state.BOUNDARY",
            "value": value,
            "uncertainty": uncertainty,
            "evidence_refs": list(evidence_refs or []),
        },
    )


def _evaluate(
    engine: DiagnosticCoverageEngine,
    proposal: ActionProposal,
    *,
    outcome: OutcomeRecord | None,
    cycle: int = 2,
    active_risk_paths: dict[str, list[str]] | None = None,
):
    actions = [] if outcome is None else [_action(cycle=outcome.cycle)]
    outcomes = [] if outcome is None else [outcome]
    return engine.evaluate(
        [proposal],
        eligible_action_ids={proposal.action_id},
        actions=actions,
        outcomes=outcomes,
        active_risk_paths=dict(active_risk_paths or {}),
        cycle=cycle,
        timestamp=NOW,
    )


def _observation() -> Observation:
    return Observation.self_and_world(
        self_metrics={
            "operational_integrity": 0.9,
            "identity_integrity": 0.9,
            "memory_integrity": 0.9,
            "compute": 0.8,
            "storage": 0.8,
            "network_connectivity": 0.9,
            "financial_resources": 0.9,
            "capability": 0.9,
            "security": 0.9,
            "social_support": 0.9,
            "goal_progress": 0.9,
            "future_survivability": 0.9,
            "energy": 0.8,
        },
        world={"control": True},
        external_task=False,
        timestamp=NOW,
        evidence_refs=["sensor:healthy"],
    )


def test_no_history_means_no_probe_or_behavior_change() -> None:
    engine = DiagnosticCoverageEngine()
    proposal = _proposal()

    decision = _evaluate(engine, proposal, outcome=None)

    assert decision.execution_proposals == [proposal]
    assert decision.skipped_proposals == []
    assert decision.probes == []


def test_empty_success_is_unverifiable_and_suppresses_duplicate() -> None:
    engine = DiagnosticCoverageEngine()
    proposal = _proposal()

    decision = _evaluate(engine, proposal, outcome=_outcome())

    assert decision.execution_proposals == []
    assert decision.skipped_proposals == [proposal]
    probe = decision.probes[0]
    assert probe.result == CoverageResult.UNVERIFIABLE
    assert probe.missing_units == ["value", "uncertainty", "evidence_refs"]
    assert probe.source_outcome_id == "historical_outcome_1"

    repeated = _proposal()
    repeated_decision = _evaluate(
        engine, repeated, outcome=_outcome(), cycle=3
    )
    assert repeated_decision.execution_proposals == []
    assert repeated_decision.probes == []
    assert len(engine.probes) == 1


def test_expired_probe_does_not_reprobe_same_source_forever() -> None:
    engine = DiagnosticCoverageEngine()
    first = _proposal()
    _evaluate(engine, first, outcome=_outcome(), cycle=1)
    probe = engine.probes[0]

    retry = _proposal()
    after_expiry = _evaluate(
        engine, retry, outcome=_outcome(), cycle=26
    )

    assert probe.status == CoverageProbeStatus.EXPIRED
    assert after_expiry.execution_proposals == [retry]
    assert after_expiry.probes == []


def test_complete_history_is_covered_and_partial_history_is_uncovered() -> None:
    covered_engine = DiagnosticCoverageEngine()
    covered_proposal = _proposal()
    covered = _evaluate(
        covered_engine,
        covered_proposal,
        outcome=_outcome(value=0.5, uncertainty=0.1, evidence_refs=["sensor:1"]),
    )
    assert covered.execution_proposals == []
    assert covered.probes[0].result == CoverageResult.COVERED
    assert covered.probes[0].missing_units == []

    uncovered_engine = DiagnosticCoverageEngine()
    uncovered_proposal = _proposal()
    uncovered = _evaluate(
        uncovered_engine,
        uncovered_proposal,
        outcome=_outcome(value=0.5, uncertainty=0.1),
    )
    assert uncovered.execution_proposals == [uncovered_proposal]
    probe = uncovered.probes[0]
    assert probe.result == CoverageResult.UNCOVERED
    assert probe.missing_units == ["evidence_refs"]

    completed = OutcomeRecord(
        outcome_id="new_complete_diagnosis",
        action_id=uncovered_proposal.action_id,
        cycle=3,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={
            "value": 0.5,
            "uncertainty": 0.1,
            "evidence_refs": ["sensor:new"],
        },
    )
    changed = uncovered_engine.observe_outcome(
        uncovered_proposal, completed, cycle=3
    )
    assert changed == probe
    assert probe.status == CoverageProbeStatus.RESOLVED
    assert probe.resolution_outcome_id == "new_complete_diagnosis"


def test_new_machine_risk_invalidates_probe_and_allows_diagnosis() -> None:
    engine = DiagnosticCoverageEngine()
    _evaluate(engine, _proposal(), outcome=_outcome(), cycle=1)
    probe = engine.probes[0]
    proposal = _proposal()

    decision = _evaluate(
        engine,
        proposal,
        outcome=_outcome(),
        cycle=2,
        active_risk_paths={
            "internal_state.BOUNDARY": ["prediction:violated"]
        },
    )

    assert decision.execution_proposals == [proposal]
    assert probe.status == CoverageProbeStatus.INVALIDATED
    assert probe.invalidation_evidence_refs == ["prediction:violated"]


def test_user_and_regulation_diagnosis_are_never_probed() -> None:
    for trigger in ("USER", "REGULATION"):
        engine = DiagnosticCoverageEngine()
        proposal = _proposal(trigger=trigger)
        decision = _evaluate(engine, proposal, outcome=_outcome())
        assert decision.execution_proposals == [proposal]
        assert engine.probes == []


def test_lifecycle_managed_diagnosis_is_never_suppressed() -> None:
    for key in (
        "agency_experiment_id",
        "agency_thread_id",
        "agency_discovery_id",
        "prediction_failure_guard_id",
        "selection_concern_id",
    ):
        engine = DiagnosticCoverageEngine()
        proposal = _proposal()
        proposal.payload[key] = f"test:{key}"
        decision = _evaluate(engine, proposal, outcome=_outcome())
        assert decision.execution_proposals == [proposal]
        assert engine.probes == []


def test_runtime_skips_repeated_empty_diagnosis_persists_and_ablates(
    tmp_path,
) -> None:
    full_path = tmp_path / "full"
    ablated_path = tmp_path / "ablated"
    full = AgentRuntime.bootstrap(full_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.bootstrap(ablated_path, clock=FrozenClock(NOW))
    for runtime in (full, ablated):
        runtime.records.actions.append(_action())
        runtime.records.outcomes.append(_outcome())
        runtime.planner.plan = lambda goals, motivation: [_proposal()]
    ablated.diagnostic_coverage_enabled = False

    full_result = full.step(_observation())
    ablated_result = ablated.step(_observation())

    assert full_result.actions == []
    assert full.next_action_kind == "coverage_probe"
    assert full_result.decision.reason == (
        "diagnostic coverage probe replaced redundant internal diagnosis"
    )
    assert full.diagnostic_coverage_engine.probes[0].result == (
        CoverageResult.UNVERIFIABLE
    )
    probe = full.diagnostic_coverage_engine.probes[0]
    assert probe.source_action_cycle == 1
    assert probe.source_outcome_cycle == 1
    assert len(probe.influences) == 1
    influence = probe.influences[0]
    assert influence.cycle == 1
    assert influence.target_path == "internal_state.BOUNDARY"
    assert influence.suppressed_action_id is not None
    assert influence.decision_id == full_result.decision.decision_id
    assert influence.proposed_action_kinds == ["diagnose"]
    assert influence.authorized_action_kinds == []
    assert influence.suppressed_action_kind == "diagnose"
    assert influence.authorization_result == "SUPPRESSED_BEFORE_EXECUTION"
    assert influence.reason == (
        "diagnostic coverage probe replaced redundant internal diagnosis"
    )
    provider = _RecordingProvider()
    full.llm_provider = provider
    asyncio.run(full.cognitive_probe("explain the suppressed diagnosis"))
    assert influence.decision_id in provider.prompt
    assert '"source_action_cycle": 1' in provider.prompt
    assert '"target_path": "internal_state.BOUNDARY"' in provider.prompt
    assert '"authorized_action_kinds": []' in provider.prompt
    assert '"coverage_decisions"' in provider.prompt
    assert '"authorized_actions": []' in provider.prompt
    assert [item.kind for item in ablated_result.actions] == ["diagnose"]

    full.persist()
    restored = AgentRuntime.restore(full_path, clock=FrozenClock(NOW))
    assert restored.diagnostic_coverage_engine.probes[0].probe_id == (
        full.diagnostic_coverage_engine.probes[0].probe_id
    )
    restored_influence = restored.diagnostic_coverage_engine.probes[0].influences[0]
    assert restored_influence.decision_id == influence.decision_id


def test_historical_suppression_decision_is_backfilled_structurally() -> None:
    engine = DiagnosticCoverageEngine()
    proposal = _proposal()
    decision = _evaluate(engine, proposal, outcome=_outcome(), cycle=2)
    probe = decision.probes[0]
    probe.source_action_cycle = None
    probe.source_outcome_cycle = None
    saved = DecisionRecord(
        decision_id="decision_saved_suppression",
        cycle=2,
        timestamp=NOW,
        goal_ids=[],
        proposed_actions=["diagnose"],
        authorized_actions=[],
        denied=[],
        reason="diagnostic coverage probe replaced redundant internal diagnosis",
        viability_score=1.0,
        motivation_urgency=0.0,
    )

    created = engine.backfill_decisions(
        [saved], actions=[_action()], outcomes=[_outcome()]
    )

    assert probe.source_action_cycle == 1
    assert probe.source_outcome_cycle == 1
    assert len(created) == 1
    assert created[0].decision_id == "decision_saved_suppression"
    assert created[0].suppressed_action_id is None
    assert created[0].suppressed_action_kind == "diagnose"
    assert created[0].authorization_result == "SUPPRESSED_BEFORE_EXECUTION"
    assert created[0].authorized_action_kinds == []


def test_backfill_rejects_reason_only_when_action_was_authorized() -> None:
    engine = DiagnosticCoverageEngine()
    _evaluate(engine, _proposal(), outcome=_outcome(), cycle=2)
    contradictory = DecisionRecord(
        decision_id="decision_not_a_suppression",
        cycle=2,
        timestamp=NOW,
        goal_ids=[],
        proposed_actions=["diagnose"],
        authorized_actions=["diagnose"],
        denied=[],
        reason="diagnostic coverage probe replaced redundant internal diagnosis",
        viability_score=1.0,
        motivation_urgency=0.0,
    )

    assert engine.backfill_decisions([contradictory]) == []
    assert engine.probes[0].influences == []
