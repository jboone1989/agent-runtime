"""No-write evaluation of execution-success / evidence-verifiability separation.

Uses the Agent's real cycle-2401 empty BOUNDARY diagnosis (action
act_ebad44fe21944d76, outcome out_f75636c09fd54832) as the counterexample.
Nothing here calls persist: the live state is only read.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.action.evidence import classify_diagnostic_evidence
from agent_runtime.action.executor import ActionExecutor
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.cognition.reflection import reflect
from agent_runtime.learning.controller import LearningController
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import IdentityBelief
from agent_runtime.self_model.model import SelfModel
from agent_runtime.viability.state import ViabilityAssessment


def _empty_model() -> SelfModel:
    return SelfModel(
        identity=IdentityBelief(
            agent_id="eval", genesis_id="eval", version="0.0.0", uncertainty=0.0
        ),
        capabilities={},
        resources={},
        vulnerabilities={},
        internal_state={},
        relationships={},
        goals=[],
        history_summary="",
        future_predictions=[],
        uncertainty={},
    )


def _proposal(trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="controlled evidence-separation evaluation",
        payload={"dimension": "BOUNDARY"},
        trigger=trigger,
        evidence=["eval:execution-evidence-separation"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    action = next(
        (
            item
            for item in runtime.records.actions
            if item.action_id == "act_ebad44fe21944d76"
        ),
        None,
    )
    outcome = next(
        (
            item
            for item in runtime.records.outcomes
            if item.outcome_id == "out_f75636c09fd54832"
        ),
        None,
    )
    usage = next(
        (
            item
            for item in runtime.capability_usage
            if item.action_id == "act_ebad44fe21944d76"
        ),
        None,
    )
    learned = next(
        (
            item
            for item in runtime.learning.verified_evidence
            if item.action_id == "act_ebad44fe21944d76"
        ),
        None,
    )
    old_reflection = next(
        (
            item
            for item in runtime.records.reflections
            if item.cycle == 2401
        ),
        None,
    )

    classified_result, missing = (
        (None, [])
        if outcome is None
        else classify_diagnostic_evidence(outcome.effects)
    )
    classified_outcome = (
        None
        if outcome is None
        else replace(
            outcome,
            evidence_result=None if classified_result is None else classified_result.value,
            missing_evidence_units=list(missing),
        )
    )

    new_reflection = None
    ablated_reflection = None
    if outcome is not None:
        viability = ViabilityAssessment(0.732, [], [], 0.0, 0.0)
        new_reflection = reflect(
            cycle=2401,
            timestamp=datetime.now(timezone.utc),
            viability=viability,
            outcomes=[classified_outcome],
        ).summary
        ablated_reflection = reflect(
            cycle=2401,
            timestamp=datetime.now(timezone.utc),
            viability=viability,
            outcomes=[outcome],
        ).summary

    learning_gate_blocks = False
    if classified_outcome is not None:
        controller = LearningController(agent_id=runtime.identity.agent_id)
        proposal = _proposal()
        from agent_runtime.experience.trace import ExperienceTrace

        trace = ExperienceTrace(
            trace_id="xtr_eval_evidence_gate",
            agent_id=runtime.identity.agent_id,
            logical_cycle=2401,
            observed_at=datetime.now(timezone.utc),
            effective_at=datetime.now(timezone.utc),
            goal_refs=[],
            plan_ref=None,
            step_ref=None,
            decision_ref="dec_eval",
            action_refs=[classified_outcome.action_id],
            outcome_refs=[classified_outcome.outcome_id],
            caused_by=[],
            evidence_refs=[],
            confidence=1.0,
        )
        learning_gate_blocks = (
            controller.observe(
                traces=[trace], outcomes=[classified_outcome]
            )
            == []
        )
        _ = proposal

    executor_user = ActionExecutor().execute(
        _proposal(trigger="USER"),
        _empty_model(),
        cycle=runtime.cycle + 1,
        timestamp=datetime.now(timezone.utc),
    )
    executor_regulation = ActionExecutor().execute(
        _proposal(trigger="REGULATION"),
        _empty_model(),
        cycle=runtime.cycle + 1,
        timestamp=datetime.now(timezone.utc),
    )

    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "real_action_found": action is not None,
        "real_outcome_found": outcome is not None,
        "real_outcome_success_preserved": (
            None if outcome is None else outcome.success
        ),
        "real_empty_evidence": (
            None
            if outcome is None
            else (
                outcome.effects.get("value") is None
                and outcome.effects.get("uncertainty") is None
                and outcome.effects.get("evidence_refs") == []
                and outcome.effects.get("observed_at") is None
            )
        ),
        "classified_result": (
            None if classified_result is None else classified_result.value
        ),
        "missing_units": missing,
        "old_usage_record_had_evidence_result": (
            None if usage is None else usage.evidence_result is not None
        ),
        "new_usage_would_carry": (
            None
            if classified_outcome is None
            else classified_outcome.evidence_result
        ),
        "old_learning_accepted_empty_diagnose": (
            learned is not None and learned.success
        ),
        "new_learning_gate_blocks": learning_gate_blocks,
        "old_reflection_lacked_evidence_split": (
            None
            if old_reflection is None
            else "evidence_unverifiable" not in old_reflection.summary
        ),
        "new_reflection_splits": (
            None
            if new_reflection is None
            else "evidence_unverifiable=1" in new_reflection
        ),
        "ablated_reflection_indistinguishable": (
            None
            if ablated_reflection is None
            else "evidence_unverifiable=0" in ablated_reflection
        ),
        "user_empty_diagnose_executed_and_classified": (
            executor_user.success
            and executor_user.evidence_result == "UNVERIFIABLE"
        ),
        "regulation_empty_diagnose_executed_and_classified": (
            executor_regulation.success
            and executor_regulation.evidence_result == "UNVERIFIABLE"
        ),
    }
    report["passed"] = (
        report["real_action_found"]
        and report["real_outcome_found"]
        and report["real_outcome_success_preserved"] is True
        and report["real_empty_evidence"] is True
        and report["classified_result"] == "UNVERIFIABLE"
        and set(report["missing_units"])
        == {"value", "uncertainty", "evidence_refs"}
        and report["old_usage_record_had_evidence_result"] is False
        and report["new_usage_would_carry"] == "UNVERIFIABLE"
        and report["old_learning_accepted_empty_diagnose"] is True
        and report["new_learning_gate_blocks"] is True
        and report["old_reflection_lacked_evidence_split"] is True
        and report["new_reflection_splits"] is True
        and report["ablated_reflection_indistinguishable"] is True
        and report["user_empty_diagnose_executed_and_classified"] is True
        and report["regulation_empty_diagnose_executed_and_classified"] is True
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
