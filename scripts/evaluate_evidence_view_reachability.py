"""No-write evaluation: the Agent's cognitive view must reach the real
cycle-2924 evidence-separation chain.

The persisted state carries outcome out_1f95d948945f4fbf (evidence_result
UNVERIFIABLE), the matching capability usage split, and the learning
exclusion — the Agent selected the visibility boundary between its citable
view and the records layer as its next mechanism. Nothing here persists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_runtime.runtime import AgentRuntime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = AgentRuntime.restore(args.state_dir.resolve())

    outcome = next(
        (
            item
            for item in runtime.records.outcomes
            if item.outcome_id == "out_1f95d948945f4fbf"
        ),
        None,
    )
    usage = next(
        (
            item
            for item in runtime.capability_usage
            if item.action_id == "act_926fd5e915b04b45"
        ),
        None,
    )
    learned_ids = [
        item.action_id for item in runtime.learning.verified_evidence
    ]

    full = runtime._evidence_view_sections()
    full_outcome = next(
        (
            entry
            for entry in full["recent_outcomes"]
            if entry.get("description") == "diagnose completed"
        ),
        None,
    )
    full_usage = next(
        (
            entry
            for entry in full["recent_capability_usage"]
            if entry.get("action_id") == "act_926fd5e915b04b45"
        ),
        None,
    )
    full_learning = [
        entry
        for entry in full["recent_verified_learning"]
        if entry.get("action_id") == "act_926fd5e915b04b45"
    ]

    runtime.evidence_view_enabled = False
    ablated = runtime._evidence_view_sections()

    report = {
        "mode": "controlled_in_memory_no_live_state_write",
        "agent_id": runtime.identity.agent_id,
        "base_cycle": runtime.cycle,
        "real_outcome_found": outcome is not None,
        "real_outcome_evidence_result": (
            None if outcome is None else outcome.evidence_result
        ),
        "real_usage_found": usage is not None,
        "real_usage_evidence_result": (
            None if usage is None else usage.evidence_result
        ),
        "real_learning_excluded_2924_action": (
            "act_926fd5e915b04b45" not in learned_ids
        ),
        "full_view_outcome_has_evidence_result": (
            None
            if full_outcome is None
            else full_outcome.get("evidence_result") == "UNVERIFIABLE"
        ),
        "full_view_outcome_has_missing_units": (
            None
            if full_outcome is None
            else full_outcome.get("missing_evidence_units")
            == ["value", "uncertainty", "evidence_refs"]
        ),
        "full_view_usage_has_split": (
            None
            if full_usage is None
            else (
                full_usage.get("success") is True
                and full_usage.get("evidence_result") == "UNVERIFIABLE"
            )
        ),
        "full_view_learning_exclusion_visible": len(full_learning) == 0,
        "ablated_view_hides_evidence_fields": (
            all(
                "evidence_result" not in entry
                for entry in ablated["recent_outcomes"]
            )
            and "recent_capability_usage" not in ablated
            and "recent_verified_learning" not in ablated
        ),
    }
    report["passed"] = (
        report["real_outcome_found"]
        and report["real_outcome_evidence_result"] == "UNVERIFIABLE"
        and report["real_usage_found"]
        and report["real_usage_evidence_result"] == "UNVERIFIABLE"
        and report["real_learning_excluded_2924_action"] is True
        and report["full_view_outcome_has_evidence_result"] is True
        and report["full_view_outcome_has_missing_units"] is True
        and report["full_view_usage_has_split"] is True
        and report["full_view_learning_exclusion_visible"] is True
        and report["ablated_view_hides_evidence_fields"] is True
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
