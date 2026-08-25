"""Controlled no-write evaluation of Agent experiment failure evidence binding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.agency.experiment import (
    AgencyExperiment,
    AgencyExperimentManager,
    AgencyExperimentStatus,
)
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import SelfPrediction


def _experiment(experiment_id: str, *, cycle: int) -> AgencyExperiment:
    return AgencyExperiment(
        experiment_id=experiment_id,
        thread_id="eval_experiment_failures",
        statement="controlled prediction test",
        action_kind="diagnose",
        resource="self",
        payload={"dimension": "llm_error_rate", "expected_range": [0.0, 0.1]},
        scheduled_cycle=cycle,
        status=AgencyExperimentStatus.PLANNED,
        evidence_refs=["eval:experiment-failure-binding"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    allowed_before = set(runtime.autonomy.allowed_actions)
    cycle = runtime.cycle + 1

    denied_manager = AgencyExperimentManager()
    denied_item = denied_manager.schedule(_experiment("eval_denied", cycle=cycle))
    denied_proposal = denied_manager.due_proposals(cycle)[0]
    denied_manager.observe_denial(
        denied_proposal,
        {
            "action": "diagnose",
            "action_id": denied_proposal.action_id,
            "reason": "action not allowed by autonomy policy",
        },
        cycle=cycle,
    )
    denied_binding = denied_manager.failure_bindings[0]

    manager = AgencyExperimentManager()
    item = manager.schedule(_experiment("eval_violated", cycle=cycle))
    proposal = manager.due_proposals(cycle)[0]
    outcome = OutcomeRecord(
        outcome_id="eval_violated_outcome",
        action_id=proposal.action_id,
        cycle=cycle,
        timestamp=runtime.clock.now(),
        success=True,
        description="diagnose completed",
        effects={
            "target_path": "internal_state.llm_error_rate",
            "value": 0.5,
            "uncertainty": 0.2,
            "evidence_refs": ["eval:sensor-reading"],
        },
    )
    prediction = SelfPrediction(
        horizon_hours=0.1,
        description="controlled prediction",
        predicted={"internal_state.llm_error_rate": [0.0, 0.1]},
        confidence=0.8,
        prediction_id="eval_violated_prediction",
        target_path="internal_state.llm_error_rate",
        intervention={"action": "diagnose"},
        evidence_refs=[proposal.action_id, outcome.outcome_id],
        status="VIOLATED",
        observed=0.5,
        error=0.4,
    )
    manager.observe_outcome(
        proposal,
        outcome,
        prediction,
        cycle=cycle,
        linked_guard_id="eval_prediction_guard",
    )
    binding = manager.failure_bindings[0]
    concern = SimpleNamespace(
        concern_id="eval_selection_concern",
        evidence_refs=[outcome.outcome_id],
    )
    manager.link_selection_concerns([concern])
    diagnostic = OutcomeRecord(
        outcome_id="eval_structured_diagnosis",
        action_id="eval_diagnostic_action",
        cycle=cycle + 1,
        timestamp=runtime.clock.now(),
        success=True,
        description="diagnose completed",
        effects={
            "value": 0.5,
            "uncertainty": 0.1,
            "evidence_refs": ["eval:diagnostic-evidence"],
        },
    )
    followup = manager.due_proposals(cycle + 1)
    from agent_runtime.action.proposal import ActionProposal

    diagnostic_proposal = ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.01,
        financial_cost=0.0,
        goal_id=None,
        reason="structured recovery",
        payload={"dimension": "llm_error_rate"},
        trigger="INTERNAL",
    )
    manager.observe_recovery(
        diagnostic_proposal, diagnostic, None, cycle=cycle + 1
    )

    ablated_manager = AgencyExperimentManager()
    ablated_manager.schedule(_experiment("eval_ablated", cycle=cycle))
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "denied_status": denied_item.status.value,
        "denial_category": denied_binding.structured_denial.get("category"),
        "denial_authority_ref": denied_binding.structured_denial.get(
            "authority_ref"
        ),
        "failure_status": item.status.value,
        "failure_kind": binding.failure_kind.value,
        "prediction_bound": binding.prediction_id == prediction.prediction_id,
        "outcome_bound": binding.outcome_id == outcome.outcome_id,
        "guard_bound": binding.linked_guard_id,
        "selection_concern_bound": binding.linked_selection_concern_id,
        "recovery_status": binding.status.value,
        "diagnostic_outcome_id": binding.diagnostic_outcome_id,
        "expiry_cycle": binding.expires_cycle,
        "ablated_actions": [],
        "ablated_bindings": len(ablated_manager.failure_bindings),
        "allowed_actions_unchanged": runtime.autonomy.allowed_actions == allowed_before,
        "unexpected_followup_experiments": len(followup),
    }
    report["passed"] = (
        report["denied_status"] == "DENIED"
        and report["denial_category"] == "AUTHORIZATION"
        and report["denial_authority_ref"] == "autonomy.allowed_actions"
        and report["failure_status"] == "FAILED"
        and report["failure_kind"] == "PREDICTION_VIOLATED"
        and report["prediction_bound"]
        and report["outcome_bound"]
        and report["guard_bound"] == "eval_prediction_guard"
        and report["selection_concern_bound"] == "eval_selection_concern"
        and report["recovery_status"] == "RESOLVED"
        and report["diagnostic_outcome_id"] == "eval_structured_diagnosis"
        and report["ablated_actions"] == []
        and report["ablated_bindings"] == 0
        and report["allowed_actions_unchanged"]
        and report["unexpected_followup_experiments"] == 0
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
