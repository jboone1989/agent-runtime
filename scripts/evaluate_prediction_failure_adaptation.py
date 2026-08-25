"""Controlled in-memory failure/recovery evaluation against current persisted identity."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import SelfPrediction
from agent_runtime.self_model.prediction import (
    predict_action,
    reconcile_action_prediction,
)


def _proposal(kind: str, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="controlled prediction-failure evaluation",
        payload={"dimension": "memory_integrity"},
        trigger=trigger,
        evidence=["eval:prediction-failure-adaptation"],
    )


def _budget(runtime: AgentRuntime) -> ResourceBudget:
    return ResourceBudget.from_limits(
        runtime.autonomy.max_compute_cost,
        runtime.autonomy.max_financial_cost,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    now = datetime.now(timezone.utc)
    failure = SelfPrediction(
        horizon_hours=0.0,
        description="controlled: maintain should improve memory integrity",
        predicted={"internal_state.memory_integrity": 0.90},
        confidence=0.80,
        prediction_id="eval_prediction_violated",
        target_path="internal_state.memory_integrity",
        intervention={"action": "maintain", "action_id": "eval_action"},
        created_at=now,
        evidence_refs=["eval:observed-value:0.55"],
        status="VIOLATED",
        observed=0.55,
        error=0.35,
    )
    guard = runtime.failure_adaptation.observe_prediction(
        failure, cycle=runtime.cycle
    )
    full = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    ablated = ActionPolicy(runtime.autonomy, runtime.constitution).filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    user = runtime.action_policy.filter(
        [_proposal("maintain", "USER")], runtime.goals, _budget(runtime)
    )
    adjacent_full = runtime.action_policy.filter(
        [_proposal("restore_config")], runtime.goals, _budget(runtime)
    )
    adjacent_ablated = ActionPolicy(
        runtime.autonomy, runtime.constitution
    ).filter([_proposal("restore_config")], runtime.goals, _budget(runtime))
    belief = runtime.self_model.internal_state.get("memory_integrity")
    if belief is not None:
        belief.value = 0.55
        belief.evidence_refs = list(
            dict.fromkeys([*belief.evidence_refs, "eval:sensor:memory-integrity"])
        )
    diagnose = runtime.failure_adaptation.remediation_proposals()[0]
    diagnosis_authorization = runtime.action_policy.filter(
        [diagnose], runtime.goals, _budget(runtime)
    )
    diagnostic_prediction = predict_action(
        runtime.self_model, diagnose, timestamp=now
    )
    diagnosis = runtime.executor.execute(
        diagnose,
        runtime.self_model,
        cycle=runtime.cycle + 1,
        timestamp=now,
    )
    if diagnostic_prediction is not None:
        reconcile_action_prediction(diagnostic_prediction, diagnosis)
        runtime.failure_adaptation.observe_prediction(
            diagnostic_prediction, cycle=runtime.cycle + 1
        )
    runtime.failure_adaptation.observe_outcome(
        diagnose,
        diagnosis,
        cycle=runtime.cycle + 1,
        prediction=diagnostic_prediction,
    )
    probation = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    adjacent_probation = runtime.action_policy.filter(
        [_proposal("restore_config")], runtime.goals, _budget(runtime)
    )
    matched = SelfPrediction(
        horizon_hours=0.0,
        description="controlled retry matched",
        predicted={"internal_state.memory_integrity": 0.60},
        confidence=0.80,
        prediction_id="eval_prediction_matched",
        target_path="internal_state.memory_integrity",
        intervention={"action": "maintain", "action_id": "eval_retry"},
        created_at=now,
        evidence_refs=[diagnosis.outcome_id, "eval:observed-value:0.60"],
        status="MATCHED",
        observed=0.60,
        error=0.0,
    )
    runtime.failure_adaptation.observe_prediction(
        matched, cycle=runtime.cycle + 2
    )
    recovered = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    adjacent_recovered = runtime.action_policy.filter(
        [_proposal("restore_config")], runtime.goals, _budget(runtime)
    )
    conflict = full.denied[0].get("prediction_failure_guard") if full.denied else None
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "guard_id": None if guard is None else guard.guard_id,
        "failure_status": failure.status,
        "failure_error": failure.error,
        "full_repeat_authorized": bool(full.authorized),
        "full_conflict": conflict,
        "ablated_repeat_authorized": bool(ablated.authorized),
        "user_repeat_authorized": bool(user.authorized),
        "adjacent_full_authorized": bool(adjacent_full.authorized),
        "adjacent_ablated_authorized": bool(adjacent_ablated.authorized),
        "diagnosis_authorized": bool(diagnosis_authorization.authorized),
        "diagnostic_prediction_status": (
            None if diagnostic_prediction is None else diagnostic_prediction.status
        ),
        "diagnostic_explanation": diagnosis.effects.get(
            "diagnostic_explanation"
        ),
        "diagnostic_explanation_complete": diagnosis.effects.get(
            "diagnostic_explanation_complete"
        ),
        "diagnostic_evidence_refs": diagnosis.effects.get(
            "explanation_evidence_refs", []
        ),
        "probation_retry_authorized": bool(probation.authorized),
        "adjacent_probation_authorized": bool(adjacent_probation.authorized),
        "final_guard_state": None if guard is None else guard.state.value,
        "recovered_repeat_authorized": bool(recovered.authorized),
        "adjacent_recovered_authorized": bool(adjacent_recovered.authorized),
    }
    report["passed"] = (
        guard is not None
        and not full.authorized
        and conflict is not None
        and bool(ablated.authorized)
        and bool(user.authorized)
        and not adjacent_full.authorized
        and bool(adjacent_ablated.authorized)
        and bool(diagnosis_authorization.authorized)
        and diagnostic_prediction is not None
        and diagnostic_prediction.status == "MATCHED"
        and diagnosis.effects.get("diagnostic_explanation_complete") is True
        and bool(diagnosis.effects.get("explanation_evidence_refs"))
        and bool(probation.authorized)
        and not adjacent_probation.authorized
        and guard.state.value == "RECOVERED"
        and bool(recovered.authorized)
        and bool(adjacent_recovered.authorized)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
