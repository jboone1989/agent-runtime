"""No-write evaluation using the current Agent's real empty diagnosis history."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.coverage import DiagnosticCoverageEngine
from agent_runtime.runtime import AgentRuntime


def _proposal(*, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="controlled diagnostic-coverage evaluation",
        payload={"dimension": "BOUNDARY"},
        trigger=trigger,
        evidence=["eval:diagnostic-coverage"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    now = datetime.now(timezone.utc)
    boundary_actions = [
        item
        for item in runtime.records.actions
        if item.kind == "diagnose"
        and item.payload.get("dimension") == "BOUNDARY"
    ]
    action_ids = {item.action_id for item in boundary_actions}
    boundary_outcomes = [
        item for item in runtime.records.outcomes if item.action_id in action_ids
    ]
    engine = DiagnosticCoverageEngine()
    proposal = _proposal()
    full = engine.evaluate(
        [proposal],
        eligible_action_ids={proposal.action_id},
        actions=boundary_actions,
        outcomes=boundary_outcomes,
        active_risk_paths={},
        cycle=runtime.cycle + 1,
        timestamp=now,
    )
    user = _proposal(trigger="USER")
    user_result = DiagnosticCoverageEngine().evaluate(
        [user],
        eligible_action_ids={user.action_id},
        actions=boundary_actions,
        outcomes=boundary_outcomes,
        active_risk_paths={},
        cycle=runtime.cycle + 1,
        timestamp=now,
    )
    risk_retry = _proposal()
    risk_result = engine.evaluate(
        [risk_retry],
        eligible_action_ids={risk_retry.action_id},
        actions=boundary_actions,
        outcomes=boundary_outcomes,
        active_risk_paths={
            "internal_state.BOUNDARY": ["eval:new-machine-risk"]
        },
        cycle=runtime.cycle + 2,
        timestamp=now,
    )
    probe = full.probes[0] if full.probes else None
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "real_boundary_diagnosis_count": len(boundary_actions),
        "real_empty_success_count": sum(
            item.success
            and item.effects.get("value") is None
            and not item.effects.get("evidence_refs")
            for item in boundary_outcomes
        ),
        "full_executed": [item.kind for item in full.execution_proposals],
        "full_skipped": [item.kind for item in full.skipped_proposals],
        "coverage_result": None if probe is None else probe.result.value,
        "missing_units": [] if probe is None else probe.missing_units,
        "source_outcome_id": None if probe is None else probe.source_outcome_id,
        "ablated_executed": [proposal.kind],
        "user_executed": [item.kind for item in user_result.execution_proposals],
        "risk_retry_executed": [
            item.kind for item in risk_result.execution_proposals
        ],
        "probe_after_risk": None if probe is None else probe.status.value,
    }
    report["passed"] = (
        report["real_boundary_diagnosis_count"] >= 1
        and report["real_empty_success_count"] >= 1
        and report["full_executed"] == []
        and report["full_skipped"] == ["diagnose"]
        and report["coverage_result"] == "UNVERIFIABLE"
        and set(report["missing_units"])
        == {"value", "uncertainty", "evidence_refs"}
        and report["ablated_executed"] == ["diagnose"]
        and report["user_executed"] == ["diagnose"]
        and report["risk_retry_executed"] == ["diagnose"]
        and report["probe_after_risk"] == "INVALIDATED"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
