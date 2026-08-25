"""Post-expiry retry boundary: an expired unresolved coverage probe must still
constrain plain same-path INTERNAL diagnoses for a bounded window.

Real evidence: probe dcov_9674eed6c24244dd (created cycle 2403, UNVERIFIABLE,
expires_cycle 2427, no resolution_outcome_id) suppressed act_74aeac3dac3747fd
at 2403; after 2427 the constraint vanished entirely. The Agent selected this
gap: after expiry, a retry without new machine evidence must be deferred, and
the deferral must leave a citable record; the window must stay bounded.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.coverage import (
    CoverageProbeStatus,
    CoverageResult,
    DiagnosticCoverageEngine,
    DiagnosticCoverageProbe,
)
from agent_runtime.codec import decode, encode
from agent_runtime.records import DecisionRecord

NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


def _proposal(*, trigger: str = "INTERNAL", payload: dict | None = None,
              evidence: list[str] | None = None) -> ActionProposal:
    return ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="retry boundary test",
        payload=dict(payload or {"dimension": "BOUNDARY"}),
        trigger=trigger,
        evidence=list(evidence or []),
    )


def _expired_probe(
    *,
    result: CoverageResult = CoverageResult.UNVERIFIABLE,
    resolution_outcome_id: str | None = None,
    expires_cycle: int = 24,
    evidence_refs: list[str] | None = None,
) -> DiagnosticCoverageProbe:
    return DiagnosticCoverageProbe(
        probe_id="dcov_expired_1",
        cycle=10,
        timestamp=NOW,
        target_path="internal_state.BOUNDARY",
        result=result,
        missing_units=["value", "uncertainty", "evidence_refs"],
        source_action_id="act_src",
        source_outcome_id="out_src",
        evidence_refs=list(evidence_refs or ["act_src", "out_src", "obs_old_1"]),
        status=CoverageProbeStatus.EXPIRED,
        expires_cycle=expires_cycle,
        resolution_outcome_id=resolution_outcome_id,
    )


def _evaluate(
    engine: DiagnosticCoverageEngine,
    proposal: ActionProposal,
    *,
    cycle: int,
):
    return engine.evaluate(
        [proposal],
        eligible_action_ids={proposal.action_id},
        actions=[],
        outcomes=[],
        active_risk_paths={},
        cycle=cycle,
        timestamp=NOW,
    )


def _decision(cycle: int = 30, proposed: list[str] | None = None,
              authorized: list[str] | None = None) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"dec_{cycle}",
        cycle=cycle,
        timestamp=NOW,
        goal_ids=[],
        proposed_actions=list(proposed or ["diagnose"]),
        authorized_actions=list(authorized or []),
        denied=[],
        reason="",
        viability_score=0.7,
        motivation_urgency=0.1,
    )


def test_plain_retry_within_window_is_deferred() -> None:
    engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
    proposal = _proposal(evidence=["obs_old_1"])

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == []
    assert decision.skipped_proposals == [proposal]


def test_retry_with_new_evidence_executes_after_expiry() -> None:
    engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
    proposal = _proposal(evidence=["obs_new_9"])

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == [proposal]
    assert decision.skipped_proposals == []


def test_deferral_window_is_bounded() -> None:
    engine = DiagnosticCoverageEngine(probes=[_expired_probe(expires_cycle=24)])
    proposal = _proposal()

    decision = _evaluate(engine, proposal, cycle=60)

    assert decision.execution_proposals == [proposal]
    assert decision.skipped_proposals == []


def test_resolved_expired_probe_does_not_defer() -> None:
    engine = DiagnosticCoverageEngine(
        probes=[_expired_probe(resolution_outcome_id="out_resolved")]
    )
    proposal = _proposal()

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == [proposal]


def test_covered_expired_probe_does_not_defer() -> None:
    engine = DiagnosticCoverageEngine(
        probes=[_expired_probe(result=CoverageResult.COVERED)]
    )
    proposal = _proposal()

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == [proposal]


def test_user_and_regulation_retries_are_not_deferred() -> None:
    for trigger in ("USER", "REGULATION"):
        engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
        proposal = _proposal(trigger=trigger)

        decision = _evaluate(engine, proposal, cycle=30)

        assert decision.execution_proposals == [proposal]
        assert decision.skipped_proposals == []


def test_lifecycle_keyed_retries_are_not_deferred() -> None:
    for key in (
        "agency_experiment_id",
        "agency_thread_id",
        "agency_discovery_id",
        "prediction_failure_guard_id",
        "selection_concern_id",
    ):
        engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
        proposal = _proposal(payload={"dimension": "BOUNDARY", key: f"{key}_1"})

        decision = _evaluate(engine, proposal, cycle=30)

        assert decision.execution_proposals == [proposal], key


def test_active_probe_still_suppresses_and_records_suppression() -> None:
    active = DiagnosticCoverageProbe(
        probe_id="dcov_active_1",
        cycle=10,
        timestamp=NOW,
        target_path="internal_state.BOUNDARY",
        result=CoverageResult.UNVERIFIABLE,
        missing_units=["value", "uncertainty", "evidence_refs"],
        source_action_id="act_src",
        source_outcome_id="out_src",
        status=CoverageProbeStatus.ACTIVE,
        expires_cycle=34,
    )
    engine = DiagnosticCoverageEngine(probes=[active])
    proposal = _proposal()

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == []
    assert decision.skipped_proposals == [proposal]
    influences = engine.record_suppressions(
        decision.skipped_proposals, _decision(), cycle=30
    )
    assert len(influences) == 1
    assert influences[0].authorization_result == "SUPPRESSED_BEFORE_EXECUTION"


def test_deferral_records_citable_influence() -> None:
    engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
    proposal = _proposal()
    decision = _evaluate(engine, proposal, cycle=30)
    assert decision.skipped_proposals == [proposal]

    influences = engine.record_suppressions(
        decision.skipped_proposals,
        _decision(cycle=30, authorized=[]),
        cycle=30,
    )

    assert len(influences) == 1
    influence = influences[0]
    assert influence.authorization_result == "DEFERRED_AFTER_EXPIRY_NO_NEW_EVIDENCE"
    assert influence.suppressed_action_id == proposal.action_id
    assert influence.probe_id == "dcov_expired_1"
    assert influence.target_path == "internal_state.BOUNDARY"


def test_deferral_influence_survives_persistence_roundtrip() -> None:
    engine = DiagnosticCoverageEngine(probes=[_expired_probe()])
    proposal = _proposal()
    decision = _evaluate(engine, proposal, cycle=30)
    influences = engine.record_suppressions(
        decision.skipped_proposals, _decision(cycle=30), cycle=30
    )

    restored = decode(encode(influences[0]))

    assert restored.authorization_result == "DEFERRED_AFTER_EXPIRY_NO_NEW_EVIDENCE"
    assert restored.suppressed_action_id == proposal.action_id
    assert restored.probe_id == "dcov_expired_1"


def test_ablation_flag_disables_retry_boundary() -> None:
    engine = DiagnosticCoverageEngine(
        probes=[_expired_probe()], enforce_retry_boundary=False
    )
    proposal = _proposal()

    decision = _evaluate(engine, proposal, cycle=30)

    assert decision.execution_proposals == [proposal]
    assert decision.skipped_proposals == []
