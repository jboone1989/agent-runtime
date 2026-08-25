"""Install an evidence-linked machine-enforced commitment while the runner is stopped."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.clock import new_id
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import RuntimeLease
from agent_runtime.social.commitment import (
    CommitmentConstraint,
    CommitmentState,
    CommitmentVisibility,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--counterpart-id", default="operator")
    parser.add_argument("--forbid-action", action="append", default=[])
    parser.add_argument("--forbid-resource", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()

    if not args.forbid_action and not args.forbid_resource:
        parser.error("at least one forbidden action or resource is required")
    if not args.evidence:
        parser.error("at least one evidence reference is required")

    state_dir = args.state_dir.resolve()
    with RuntimeLease(state_dir):
        runtime = AgentRuntime.restore(state_dir)
        duplicate = next(
            (
                item
                for item in runtime.commitments
                if item.statement == args.statement
                and item.state != CommitmentState.REVOKED
            ),
            None,
        )
        if duplicate is None:
            constraint = CommitmentConstraint(
                constraint_id=new_id("cmtc"),
                forbidden_action_kinds=set(args.forbid_action),
                forbidden_resources=set(args.forbid_resource),
                applies_to_triggers={"INTERNAL"},
                reason="self-authored future-binding restriction",
                evidence_refs=list(args.evidence),
            )
            duplicate = runtime.social.commit(
                counterpart_id=args.counterpart_id,
                statement=args.statement,
                visibility=CommitmentVisibility.PUBLIC,
                constraints=[constraint],
                evidence_refs=list(args.evidence),
            )
            runtime.persist()

        constraint_ids = [item.constraint_id for item in duplicate.constraints]
        print(
            f"agent_id={runtime.identity.agent_id} cycle={runtime.cycle} "
            f"commitment_id={duplicate.commitment_id} "
            f"constraint_ids={','.join(constraint_ids)} state={duplicate.state.value}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
