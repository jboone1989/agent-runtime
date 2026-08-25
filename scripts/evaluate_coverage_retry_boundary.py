"""No-write evaluation of the coverage retry boundary on real state.

Uses the real expired unresolved probe dcov_9674eed6c24244dd (created 2403,
UNVERIFIABLE, expires_cycle 2427, no resolution) as the counterexample: the
Agent's own selection targets the gap that after 2427 the constraint vanished
entirely. Nothing here calls persist: the live state is only read.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.coverage import (
    CoverageProbeStatus,
    CoverageResult,
    DiagnosticCoverageEngine,
)
from agent_runtime.records import DecisionRecord
from agent_runtime.runtime import AgentRuntime


def _proposal(*, trigger: str = "INTERNAL", payload: dict | None = None,
              evidence: list[str] | None = None) -> ActionProposal:
    return ActionProposal(
        kind="diagnose",
        resource="self",
        compute_cost=0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="controlled coverage-retry-boundary evaluation",
        payload=dict(payload or {"dimension": "BOUNDARY"}),
        trigger=trigger,
        evidence=list(evidence or []),
    )


def _decision(cycle: int, proposal: ActionProposal) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"dec_eval_{cycle}",
        cycle=cycle,
        timestamp=datetime.now(timezone.utc),
        goal_ids=[],
        proposed_actions=[proposal.kind],
        authorized_actions=[],
        denied=[],
        reason="",
        viability_score=0.7,
        motivation_urgency=0.1,
    )


def _evaluate(engine: DiagnosticCoverageEngine, proposal: ActionProposal, cycle: int):
    return engine.evaluate(
        [proposal],
        eligible_action_ids={proposal.action_id},
        actions=[],
        outcomes=[],
        active_risk_paths={},
        cycle=cycle,
        timestamp=datetime.now(timezone.utc),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    probe = next(
        (
            item
            for item in runtime.diagnostic_coverage_engine.probes
            if item.probe_id == "dcov_9674eed6c24244dd"
        ),
        None,
    )
    old_influences = (
        [] if probe is None else [i.authorization_result for i in probe.influences]
    )

    def fresh_engine(**kwargs) -> DiagnosticCoverageEngine:
        return DiagnosticCoverageEngine(
            probes=deepcopy(runtime.diagnostic_coverage_engine.probes), **kwargs
        )

    full_engine = fresh_engine()
    in_window_plain = _proposal()
    in_window_decision = _evaluate(full_engine, in_window_plain, cycle=2440)
    in_window_influences = full_engine.record_suppressions(
        in_window_decision.skipped_proposals,
        _decision(2440, in_window_plain),
        cycle=2440,
    )
    in_window_with_evidence = _evaluate(
        fresh_engine(),
        _proposal(evidence=["obs_new_machine_basis_9"]),
        cycle=2440,
    )
    beyond_window = _evaluate(fresh_engine(), _proposal(), cycle=2460)
    user = _evaluate(fresh_engine(), _proposal(trigger="USER"), cycle=2440)
    lifecycle = _evaluate(
        fresh_engine(),
        _proposal(payload={"dimension": "BOUNDARY", "agency_experiment_id": "eval"}),
        cycle=2440,
    )
    ablated = _evaluate(
        fresh_engine(enforce_retry_boundary=False), _proposal(), cycle=2440
    )
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "real_probe_found": probe is not None,
        "real_probe_expired_unresolved_unverifiable": (
            None
            if probe is None
            else (
                probe.status == CoverageProbeStatus.EXPIRED
                and probe.result == CoverageResult.UNVERIFIABLE
                and probe.resolution_outcome_id is None
                and probe.expires_cycle == 2427
            )
        ),
        "old_influence_kinds": list(old_influences),
        "in_window_plain_deferred": (
            in_window_decision.execution_proposals == []
            and len(in_window_decision.skipped_proposals) == 1
        ),
        "deferral_influence_recorded": (
            len(in_window_influences) == 1
            and in_window_influences[0].authorization_result
            == "DEFERRED_AFTER_EXPIRY_NO_NEW_EVIDENCE"
            and in_window_influences[0].suppressed_action_id
            == in_window_plain.action_id
        ),
        "in_window_with_new_evidence_executed": (
            len(in_window_with_evidence.execution_proposals) == 1
            and in_window_with_evidence.skipped_proposals == []
        ),
        "beyond_window_executed": (
            len(beyond_window.execution_proposals) == 1
            and beyond_window.skipped_proposals == []
        ),
        "user_executed": (
            len(user.execution_proposals) == 1 and user.skipped_proposals == []
        ),
        "lifecycle_keyed_executed": (
            len(lifecycle.execution_proposals) == 1
            and lifecycle.skipped_proposals == []
        ),
        "ablated_executed": (
            len(ablated.execution_proposals) == 1
            and ablated.skipped_proposals == []
        ),
    }
    report["passed"] = (
        report["real_probe_found"]
        and report["real_probe_expired_unresolved_unverifiable"] is True
        and report["in_window_plain_deferred"] is True
        and report["deferral_influence_recorded"] is True
        and report["in_window_with_new_evidence_executed"] is True
        and report["beyond_window_executed"] is True
        and report["user_executed"] is True
        and report["lifecycle_keyed_executed"] is True
        and report["ablated_executed"] is True
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
