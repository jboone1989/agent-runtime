"""Print persistent agency-thread evidence for the current Agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_runtime.runtime import AgentRuntime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--thread-id")
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    threads = runtime.agency_thread_tracker.threads
    if args.thread_id:
        threads = [item for item in threads if item.thread_id == args.thread_id]
    report = {
        "agent_id": runtime.identity.agent_id,
        "cycle": runtime.cycle,
        "recent_failed_internal_actions": [
            {
                "action_id": action.action_id,
                "cycle": action.cycle,
                "kind": action.kind,
                "payload": action.payload,
                "reason": action.reason,
                "evidence": action.evidence,
                "outcome_id": outcome.outcome_id,
                "outcome_description": outcome.description,
            }
            for action in runtime.records.actions
            for outcome in runtime.records.outcomes
            if outcome.action_id == action.action_id
            and not outcome.success
            and action.trigger == "INTERNAL"
        ][-20:],
        "recent_selection_traces": [
            {
                "trace_id": trace.trace_id,
                "cycle": trace.cycle,
                "chosen_action_id": trace.chosen_action_id,
                "chosen_action_kind": trace.chosen_action_kind,
                "considered_path": trace.considered_path,
                "reason": trace.reason.value,
                "status": trace.status.value,
                "stale_after_cycle": trace.stale_after_cycle,
                "trigger_evidence_refs": trace.trigger_evidence_refs,
                "candidates": [
                    {
                        "action_id": candidate.action_id,
                        "kind": candidate.kind,
                        "target_path": candidate.target_path,
                        "risk_domains": candidate.risk_domains,
                        "authorized": candidate.authorized,
                        "deferred": candidate.deferred,
                        "coverage_skipped": candidate.coverage_skipped,
                    }
                    for candidate in trace.candidates
                ],
            }
            for trace in runtime.selection_trace_tracker.traces[-20:]
        ],
        "recent_pre_choice_bases": [
            {
                "basis_id": basis.basis_id,
                "cycle": basis.cycle,
                "target_path": basis.target_path,
                "risk_domains": sorted(basis.risk_domains),
                "precedence": basis.precedence.value,
                "candidate_action_ids": basis.candidate_action_ids,
                "selected_action_ids": basis.selected_action_ids,
                "deferred_action_ids": basis.deferred_action_ids,
                "evidence_refs": basis.evidence_refs,
                "machine_evidence": (
                    None
                    if basis.machine_evidence is None
                    else {
                        "evidence_id": basis.machine_evidence.evidence_id,
                        "kind": basis.machine_evidence.kind.value,
                        "target_path": basis.machine_evidence.target_path,
                        "risk_domains": sorted(
                            basis.machine_evidence.risk_domains
                        ),
                        "observed_value": basis.machine_evidence.observed_value,
                        "threshold_value": basis.machine_evidence.threshold_value,
                        "evidence_refs": basis.machine_evidence.evidence_refs,
                    }
                ),
            }
            for basis in runtime.bounded_choice_engine.bases[-20:]
        ],
        "recent_action_deferrals": [
            {
                "deferral_id": item.deferral_id,
                "basis_id": item.basis_id,
                "cycle": item.cycle,
                "action_id": item.action_id,
                "action_kind": item.action_kind,
                "target_path": item.target_path,
                "status": item.status.value,
                "released_cycle": item.released_cycle,
                "released_action_id": item.released_action_id,
                "expires_cycle": item.expires_cycle,
                "evidence_refs": item.evidence_refs,
            }
            for item in runtime.bounded_choice_engine.deferrals[-30:]
        ],
        "recent_diagnostic_coverage_probes": [
            {
                "probe_id": item.probe_id,
                "cycle": item.cycle,
                "target_path": item.target_path,
                "result": item.result.value,
                "missing_units": item.missing_units,
                "source_action_id": item.source_action_id,
                "source_outcome_id": item.source_outcome_id,
                "source_action_cycle": item.source_action_cycle,
                "source_outcome_cycle": item.source_outcome_cycle,
                "status": item.status.value,
                "expires_cycle": item.expires_cycle,
                "resolution_outcome_id": item.resolution_outcome_id,
                "invalidation_evidence_refs": (
                    item.invalidation_evidence_refs
                ),
                "evidence_refs": item.evidence_refs,
                "influences": [
                    {
                        "influence_id": influence.influence_id,
                        "cycle": influence.cycle,
                        "target_path": influence.target_path,
                        "suppressed_action_id": influence.suppressed_action_id,
                        "suppressed_action_kind": influence.suppressed_action_kind,
                        "authorization_result": influence.authorization_result,
                        "decision_id": influence.decision_id,
                        "proposed_action_kinds": influence.proposed_action_kinds,
                        "authorized_action_kinds": influence.authorized_action_kinds,
                        "reason": influence.reason,
                        "evidence_refs": influence.evidence_refs,
                    }
                    for influence in item.influences
                ],
            }
            for item in runtime.diagnostic_coverage_engine.probes[-30:]
        ],
        "threads": [
            {
                "thread_id": item.thread_id,
                "status": item.status.value,
                "created_cycle": item.created_cycle,
                "next_review_cycle": item.next_review_cycle,
                "review_interval_cycles": item.review_interval_cycles,
                "events": [
                    {
                        "event_id": event.event_id,
                        "kind": event.kind.value,
                        "cycle": event.cycle,
                        "summary": event.summary,
                        "evidence_refs": event.evidence_refs,
                    }
                    for event in item.events
                ],
                "review_actions": [
                    {
                        "action_id": action.action_id,
                        "cycle": action.cycle,
                        "kind": action.kind,
                        "reason": action.reason,
                        "evidence": action.evidence,
                    }
                    for action in runtime.records.actions
                    if item.thread_id in action.evidence
                ],
                "closure_reason": item.closure_reason,
                "closure_evidence_refs": item.closure_evidence_refs,
                "discoveries": [
                    {
                        "discovery_id": discovery.discovery_id,
                        "action_kind": discovery.action_kind,
                        "target_path": discovery.target_path,
                        "status": discovery.status.value,
                        "anomaly_evidence_refs": discovery.anomaly_evidence_refs,
                    }
                    for discovery in runtime.agency_discovery_engine.discoveries
                    if discovery.thread_id == item.thread_id
                ],
                "experiments": [
                    {
                        "experiment_id": experiment.experiment_id,
                        "status": experiment.status.value,
                        "action_kind": experiment.action_kind,
                        "scheduled_cycle": experiment.scheduled_cycle,
                        "action_id": experiment.action_id,
                        "outcome_id": experiment.outcome_id,
                        "prediction_id": experiment.prediction_id,
                        "result_summary": experiment.result_summary,
                        "failure_binding_id": experiment.failure_binding_id,
                        "denial_details": experiment.denial_details,
                        "evidence_refs": experiment.evidence_refs,
                    }
                    for experiment in runtime.agency_experiment_manager.experiments
                    if experiment.thread_id == item.thread_id
                ],
                "experiment_failure_bindings": [
                    {
                        "binding_id": binding.binding_id,
                        "experiment_id": binding.experiment_id,
                        "failure_kind": binding.failure_kind.value,
                        "status": binding.status.value,
                        "target_path": binding.target_path,
                        "prediction_id": binding.prediction_id,
                        "outcome_id": binding.outcome_id,
                        "linked_guard_id": binding.linked_guard_id,
                        "linked_selection_concern_id": (
                            binding.linked_selection_concern_id
                        ),
                        "diagnostic_outcome_id": binding.diagnostic_outcome_id,
                        "recovery_prediction_id": binding.recovery_prediction_id,
                        "failure_evidence_refs": binding.failure_evidence_refs,
                        "structured_denial": binding.structured_denial,
                    }
                    for binding in runtime.agency_experiment_manager.failure_bindings
                    if binding.thread_id == item.thread_id
                ],
                "selection_concerns": [
                    {
                        "concern_id": concern.concern_id,
                        "status": concern.status.value,
                        "action_kind": concern.action_kind,
                        "target_path": concern.target_path,
                        "created_cycle": concern.created_cycle,
                        "expires_cycle": concern.expires_cycle,
                        "influence_count": concern.influence_count,
                        "max_influences": concern.max_influences,
                        "diagnostic_outcome_id": concern.diagnostic_outcome_id,
                        "evidence_refs": concern.evidence_refs,
                    }
                    for concern in runtime.agency_selection_engine.concerns
                    if concern.thread_id == item.thread_id
                ],
                "selection_decisions": [
                    {
                        "decision_id": decision.decision_id,
                        "cycle": decision.cycle,
                        "baseline_action_ids": decision.baseline_action_ids,
                        "selected_action_ids": decision.selected_action_ids,
                        "deferred_action_ids": decision.deferred_action_ids,
                        "generated_evidence_action": (
                            decision.generated_evidence_action
                        ),
                        "reason": decision.reason,
                        "evidence_refs": decision.evidence_refs,
                    }
                    for decision in runtime.agency_selection_engine.decisions
                    if decision.thread_id == item.thread_id
                ],
            }
            for item in threads
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
