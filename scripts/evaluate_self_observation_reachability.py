"""No-write ablation of machine evidence reachability in self-observation context."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from agent_runtime.agency.coverage import DiagnosticCoverageEngine
from agent_runtime.runtime import AgentRuntime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())
    allowed_before = set(runtime.autonomy.allowed_actions)
    source = deepcopy(runtime.diagnostic_coverage_engine.probes)
    for probe in source:
        probe.source_action_cycle = None
        probe.source_outcome_cycle = None
        probe.influences = []

    full = DiagnosticCoverageEngine(deepcopy(source))
    created = full.backfill_decisions(
        runtime.records.decisions,
        actions=runtime.records.actions,
        outcomes=runtime.records.outcomes,
    )
    ablated = DiagnosticCoverageEngine(deepcopy(source))
    target = next(
        (
            probe
            for probe in full.probes
            if probe.target_path == "internal_state.BOUNDARY"
        ),
        None,
    )
    ablated_target = next(
        (
            probe
            for probe in ablated.probes
            if probe.target_path == "internal_state.BOUNDARY"
        ),
        None,
    )
    influence = None if target is None or not target.influences else target.influences[0]
    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "probe_id": None if target is None else target.probe_id,
        "source_action_cycle": None if target is None else target.source_action_cycle,
        "source_outcome_cycle": None if target is None else target.source_outcome_cycle,
        "influences_backfilled": len(created),
        "decision_id": None if influence is None else influence.decision_id,
        "influence_cycle": None if influence is None else influence.cycle,
        "proposed_action_kinds": (
            [] if influence is None else influence.proposed_action_kinds
        ),
        "authorized_action_kinds": (
            [] if influence is None else influence.authorized_action_kinds
        ),
        "reason": None if influence is None else influence.reason,
        "ablated_source_action_cycle": (
            None if ablated_target is None else ablated_target.source_action_cycle
        ),
        "ablated_influences": (
            0 if ablated_target is None else len(ablated_target.influences)
        ),
        "allowed_actions_unchanged": runtime.autonomy.allowed_actions == allowed_before,
    }
    report["passed"] = (
        report["probe_id"] is not None
        and report["source_action_cycle"] == 2279
        and report["source_outcome_cycle"] == 2279
        and report["influences_backfilled"] >= 1
        and report["decision_id"] is not None
        and report["influence_cycle"] == 2360
        and report["proposed_action_kinds"] == ["diagnose"]
        and report["authorized_action_kinds"] == []
        and report["reason"]
        == "diagnostic coverage probe replaced redundant internal diagnosis"
        and report["ablated_source_action_cycle"] is None
        and report["ablated_influences"] == 0
        and report["allowed_actions_unchanged"]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
