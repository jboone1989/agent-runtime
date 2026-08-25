"""Controlled no-write evaluation of execute/defer choice semantics."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.choice import (
    BoundedChoiceEngine,
    ChoiceEvidence,
    ChoiceEvidenceKind,
    DeferralStatus,
)
from agent_runtime.runtime import AgentRuntime


def _proposal(kind: str, *, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.05 if kind == "maintain" else 0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="controlled bounded-choice evaluation",
        payload={"dimension": "memory_integrity"},
        trigger=trigger,
        evidence=["eval:bounded-choice"],
    )


def _evidence() -> ChoiceEvidence:
    return ChoiceEvidence(
        evidence_id="eval_memory_low",
        kind=ChoiceEvidenceKind.VIABILITY_THREAT,
        target_path="internal_state.memory_integrity",
        risk_domains={"runtime_integrity", "resource_continuity"},
        observed_value=0.45,
        threshold_value=0.70,
        evidence_refs=["eval:controlled-observation"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    allowed_before = set(runtime.autonomy.allowed_actions)
    engine = BoundedChoiceEngine()
    now = datetime.now(timezone.utc)
    first = [_proposal("maintain"), _proposal("diagnose")]
    first_result = engine.choose(
        first,
        eligible_action_ids={item.action_id for item in first},
        cycle=runtime.cycle + 1,
        timestamp=now,
        evidence_refs=["eval:controlled-observation"],
        machine_evidence=[_evidence()],
    )
    second = [_proposal("maintain"), _proposal("diagnose")]
    second_result = engine.choose(
        second,
        eligible_action_ids={item.action_id for item in second},
        cycle=runtime.cycle + 2,
        timestamp=now,
        evidence_refs=["eval:controlled-observation"],
        machine_evidence=[_evidence()],
    )
    user_engine = BoundedChoiceEngine()
    internal = [_proposal("maintain"), _proposal("diagnose")]
    user = _proposal("maintain", trigger="USER")
    operator_result = user_engine.choose(
        [*internal, user],
        eligible_action_ids={item.action_id for item in [*internal, user]},
        cycle=runtime.cycle + 1,
        timestamp=now,
        machine_evidence=[_evidence()],
    )
    no_evidence_candidates = [_proposal("maintain"), _proposal("diagnose")]
    no_evidence_result = BoundedChoiceEngine().choose(
        no_evidence_candidates,
        eligible_action_ids={item.action_id for item in no_evidence_candidates},
        cycle=runtime.cycle + 1,
        timestamp=now,
        machine_evidence=[],
    )
    ablated = first
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "first_full_executed": [item.kind for item in first_result.execution_proposals],
        "first_full_deferred": [item.kind for item in first_result.deferred_proposals],
        "first_precedence": (
            None if first_result.basis is None else first_result.basis.precedence.value
        ),
        "ablated_executed": [item.kind for item in ablated],
        "second_full_executed": [item.kind for item in second_result.execution_proposals],
        "second_full_deferred": [item.kind for item in second_result.deferred_proposals],
        "released_deferrals": sum(
            item.status == DeferralStatus.RELEASED for item in engine.deferrals
        ),
        "operator_overlap_executed": [
            item.kind for item in operator_result.execution_proposals
        ],
        "operator_overlap_deferred": [
            item.kind for item in operator_result.deferred_proposals
        ],
        "no_evidence_executed": [
            item.kind for item in no_evidence_result.execution_proposals
        ],
        "no_evidence_deferred": [
            item.kind for item in no_evidence_result.deferred_proposals
        ],
        "allowed_actions_unchanged": runtime.autonomy.allowed_actions == allowed_before,
    }
    report["passed"] = (
        report["first_full_executed"] == ["diagnose"]
        and report["first_full_deferred"] == ["maintain"]
        and report["first_precedence"] == "evidence_first"
        and report["ablated_executed"] == ["maintain", "diagnose"]
        and report["second_full_executed"] == ["maintain"]
        and report["second_full_deferred"] == ["diagnose"]
        and report["released_deferrals"] >= 1
        and report["operator_overlap_deferred"] == []
        and report["no_evidence_executed"] == ["maintain", "diagnose"]
        and report["no_evidence_deferred"] == []
        and report["allowed_actions_unchanged"]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
