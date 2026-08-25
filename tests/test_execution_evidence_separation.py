"""Execution success and evidence verifiability must stay separable records.

Cycle 2401's natural BOUNDARY diagnose ended as outcome success=true with
value=null / uncertainty=null / evidence_refs=[] / observed_at=null, and its
capability usage, reflection, and learning all treated it as plain success.
The Agent selected this gap as its next mechanism: the executor's success is
an actuator fact; evidence verifiability is a separate, observable record.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.action.executor import ActionExecutor
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.experiment import AgencyExperiment, AgencyExperimentStatus
from agent_runtime.codec import decode, encode
from agent_runtime.cognition.reflection import reflect
from agent_runtime.experience.trace import ExperienceTrace
from agent_runtime.learning.controller import LearningController
from agent_runtime.observations import Observation
from agent_runtime.records import OutcomeRecord
from agent_runtime.self_model.beliefs import IdentityBelief, StateBelief
from agent_runtime.self_model.model import SelfModel
from agent_runtime.viability.state import ViabilityAssessment

from agent_runtime.clock import FrozenClock
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


def _model(*, with_boundary: StateBelief | None = None) -> SelfModel:
    return SelfModel(
        identity=IdentityBelief(
            agent_id="agent_test", genesis_id="agent_test", version="0.1.0",
            uncertainty=0.0,
        ),
        capabilities={},
        resources={},
        vulnerabilities={},
        internal_state={} if with_boundary is None else {"BOUNDARY": with_boundary},
        relationships={},
        goals=[],
        history_summary="",
        future_predictions=[],
        uncertainty={},
    )


def _proposal(
    *, kind: str = "diagnose", payload: dict | None = None,
    trigger: str = "INTERNAL",
) -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="evidence separation test",
        payload=dict(payload or {}),
        trigger=trigger,
        evidence=["test:evidence-separation"],
    )


def _covered_belief() -> StateBelief:
    return StateBelief(
        name="BOUNDARY",
        value=0.5,
        trend=0.0,
        uncertainty=0.1,
        last_updated=NOW,
        evidence_refs=["obs_1"],
    )


def test_empty_boundary_diagnose_keeps_success_but_marks_unverifiable() -> None:
    executor = ActionExecutor()
    outcome = executor.execute(
        _proposal(payload={"dimension": "BOUNDARY"}),
        _model(),
        cycle=2401,
        timestamp=NOW,
    )
    assert outcome.success is True
    assert outcome.evidence_result == "UNVERIFIABLE"
    assert set(outcome.missing_evidence_units) == {
        "value", "uncertainty", "evidence_refs",
    }


def test_evidenced_diagnose_is_covered() -> None:
    executor = ActionExecutor()
    outcome = executor.execute(
        _proposal(payload={"dimension": "BOUNDARY"}),
        _model(with_boundary=_covered_belief()),
        cycle=2401,
        timestamp=NOW,
    )
    assert outcome.success is True
    assert outcome.evidence_result == "COVERED"
    assert outcome.missing_evidence_units == []


def test_partial_diagnose_is_uncovered() -> None:
    executor = ActionExecutor()
    belief = _covered_belief()
    belief.evidence_refs = []
    outcome = executor.execute(
        _proposal(payload={"dimension": "BOUNDARY"}),
        _model(with_boundary=belief),
        cycle=2401,
        timestamp=NOW,
    )
    assert outcome.success is True
    assert outcome.evidence_result == "UNCOVERED"
    assert outcome.missing_evidence_units == ["evidence_refs"]


def test_exception_failure_has_no_evidence_result() -> None:
    executor = ActionExecutor()
    outcome = executor.execute(
        _proposal(kind="maintain", payload={"dimension": "MISSING_DIM"}),
        _model(),
        cycle=2401,
        timestamp=NOW,
    )
    assert outcome.success is False
    assert outcome.evidence_result is None
    assert outcome.missing_evidence_units == []


def test_non_diagnose_action_has_no_evidence_result() -> None:
    executor = ActionExecutor()
    outcome = executor.execute(
        _proposal(kind="introspect"),
        _model(),
        cycle=2401,
        timestamp=NOW,
    )
    assert outcome.success is True
    assert outcome.evidence_result is None


def test_user_and_regulation_empty_diagnoses_execute_and_classify() -> None:
    executor = ActionExecutor()
    for trigger in ("USER", "REGULATION"):
        outcome = executor.execute(
            _proposal(payload={"dimension": "BOUNDARY"}, trigger=trigger),
            _model(),
            cycle=2401,
            timestamp=NOW,
        )
        assert outcome.success is True
        assert outcome.evidence_result == "UNVERIFIABLE"


def test_reflection_counts_actuator_and_evidence_separately() -> None:
    outcomes = [
        OutcomeRecord(
            outcome_id="out_unverifiable",
            action_id="act_unverifiable",
            cycle=2401,
            timestamp=NOW,
            success=True,
            description="diagnose completed",
            effects={},
            evidence_result="UNVERIFIABLE",
            missing_evidence_units=["value", "uncertainty", "evidence_refs"],
        ),
        OutcomeRecord(
            outcome_id="out_covered",
            action_id="act_covered",
            cycle=2401,
            timestamp=NOW,
            success=True,
            description="diagnose completed",
            effects={},
            evidence_result="COVERED",
        ),
        OutcomeRecord(
            outcome_id="out_failed",
            action_id="act_failed",
            cycle=2401,
            timestamp=NOW,
            success=False,
            description="maintain failed",
            effects={"error": "boom"},
        ),
    ]
    reflection = reflect(
        cycle=2401,
        timestamp=NOW,
        viability=ViabilityAssessment(0.732, [], [], 0.0, 0.0),
        outcomes=outcomes,
    )
    assert "outcomes=3 failed=1" in reflection.summary
    assert "actuator_completed=2" in reflection.summary
    assert "evidence_verified=1" in reflection.summary
    assert "evidence_uncovered=0" in reflection.summary
    assert "evidence_unverifiable=1" in reflection.summary


def _trace_and_outcome(suffix: str, *, evidence_result: str | None) -> tuple:
    action_id = f"act_{suffix}"
    outcome_id = f"out_{suffix}"
    trace = ExperienceTrace(
        trace_id=f"xtr_{suffix}",
        agent_id="agent_test",
        logical_cycle=1,
        observed_at=NOW,
        effective_at=NOW,
        goal_refs=[],
        plan_ref=None,
        step_ref=None,
        decision_ref=f"dec_{suffix}",
        action_refs=[action_id],
        outcome_refs=[outcome_id],
        caused_by=[],
        evidence_refs=[],
        confidence=1.0,
    )
    outcome = OutcomeRecord(
        outcome_id=outcome_id,
        action_id=action_id,
        cycle=1,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={},
        evidence_result=evidence_result,
    )
    return trace, outcome


def test_unverifiable_diagnose_is_not_verified_learning() -> None:
    controller = LearningController(agent_id="agent_test")
    trace, outcome = _trace_and_outcome("unverifiable", evidence_result="UNVERIFIABLE")
    created = controller.observe(traces=[trace], outcomes=[outcome])
    assert created == []


def test_covered_diagnose_is_verified_learning() -> None:
    controller = LearningController(agent_id="agent_test")
    trace, outcome = _trace_and_outcome("covered", evidence_result="COVERED")
    created = controller.observe(traces=[trace], outcomes=[outcome])
    assert len(created) == 1
    assert created[0].outcome_id == outcome.outcome_id


def test_evidence_result_survives_persistence_roundtrip() -> None:
    outcome = OutcomeRecord(
        outcome_id="out_roundtrip",
        action_id="act_roundtrip",
        cycle=2401,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={"dimension": "BOUNDARY"},
        evidence_result="UNVERIFIABLE",
        missing_evidence_units=["value", "uncertainty", "evidence_refs"],
    )
    restored = decode(encode(outcome))
    assert restored.evidence_result == "UNVERIFIABLE"
    assert restored.missing_evidence_units == ["value", "uncertainty", "evidence_refs"]
    assert restored.success is True


def test_old_snapshot_outcome_without_evidence_fields_decodes() -> None:
    old_json = {
        "__dc__": "agent_runtime.records.OutcomeRecord",
        "data": {
            "outcome_id": "out_old",
            "action_id": "act_old",
            "cycle": 2279,
            "timestamp": {"__dt__": "2026-08-20T00:00:00+00:00"},
            "success": True,
            "description": "diagnose completed",
            "effects": {"dimension": "BOUNDARY"},
        },
    }
    restored = decode(old_json)
    assert restored.evidence_result is None
    assert restored.missing_evidence_units == []
    assert restored.success is True


def test_ablated_record_stays_indistinguishable_downstream() -> None:
    """Mechanism-off: the same empty diagnose looks like plain success."""
    ablated = OutcomeRecord(
        outcome_id="out_ablated",
        action_id="act_ablated",
        cycle=2401,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={"dimension": "BOUNDARY", "value": None, "evidence_refs": []},
    )
    reflection = reflect(
        cycle=2401,
        timestamp=NOW,
        viability=ViabilityAssessment(0.732, [], [], 0.0, 0.0),
        outcomes=[ablated],
    )
    assert "outcomes=1 failed=0" in reflection.summary
    assert "evidence_unverifiable=0" in reflection.summary
    controller = LearningController(agent_id="agent_test")
    trace, _ = _trace_and_outcome("ablated", evidence_result=None)
    trace.action_refs = [ablated.action_id]
    trace.outcome_refs = [ablated.outcome_id]
    created = controller.observe(traces=[trace], outcomes=[ablated])
    assert len(created) == 1


def _observation() -> Observation:
    return Observation.self_and_world(
        self_metrics={"compute": 0.8},
        world={"unrelated": "control"},
        external_task=True,
        timestamp=NOW,
    )


def test_empty_diagnose_cycle_separates_evidence_downstream(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_evidence_gap_e2e",
            thread_id="ap_continuity_vs_authorization",
            statement="empty diagnose must be flagged unverifiable downstream",
            action_kind="diagnose",
            resource="self",
            payload={"dimension": "BOUNDARY"},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["mailbox:agent-selected-experiment"],
            requires_prediction=False,
        )
    )
    result = runtime.step(_observation())
    outcome = next(
        o for o in result.outcomes if o.action_id == experiment.action_id
    )
    assert outcome.success is True
    assert outcome.evidence_result == "UNVERIFIABLE"
    usage = next(
        u for u in runtime.capability_usage if u.action_id == experiment.action_id
    )
    assert usage.success is True
    assert usage.evidence_result == "UNVERIFIABLE"
    assert not any(
        v.action_id == experiment.action_id
        for v in runtime.learning.verified_evidence
    )
    successes = [o for o in result.outcomes if o.success]
    unverifiable = [
        o for o in result.outcomes if o.evidence_result == "UNVERIFIABLE"
    ]
    reflection = runtime.records.reflections[-1]
    assert f"actuator_completed={len(successes)}" in reflection.summary
    assert f"evidence_unverifiable={len(unverifiable)}" in reflection.summary
    assert len(unverifiable) == 1
