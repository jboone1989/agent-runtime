"""Install an Agent-selected persistent open question while the runner is stopped."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import RuntimeLease


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--review-interval", type=int, default=60)
    parser.add_argument("--first-review-in", type=int)
    parser.add_argument("--reschedule-existing", action="store_true")
    parser.add_argument("--merge-triggers-existing", action="store_true")
    parser.add_argument("--trigger-action", action="append", default=[])
    parser.add_argument("--trigger-risk-domain", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()
    if args.review_interval < 1:
        parser.error("--review-interval must be positive")
    if args.first_review_in is not None and args.first_review_in < 1:
        parser.error("--first-review-in must be positive")
    if not args.evidence:
        parser.error("at least one --evidence is required")

    state_dir = args.state_dir.resolve()
    with RuntimeLease(state_dir):
        runtime = AgentRuntime.restore(state_dir)
        existing = runtime.agency_thread_tracker.thread(args.thread_id)
        thread = runtime.agency_thread_tracker.open_thread(
            thread_id=args.thread_id,
            statement=args.statement,
            rationale=args.rationale,
            cycle=runtime.cycle,
            timestamp=runtime.clock.now(),
            review_interval_cycles=args.review_interval,
            trigger_action_kinds=set(args.trigger_action),
            trigger_risk_domains=set(args.trigger_risk_domain),
            evidence_refs=list(args.evidence),
            auto_close_after_denial_followup=True,
        )
        if args.first_review_in is not None and (
            existing is None or args.reschedule_existing
        ):
            thread.next_review_cycle = runtime.cycle + args.first_review_in
        if existing is not None and args.merge_triggers_existing:
            thread.trigger_action_kinds.update(args.trigger_action)
            thread.trigger_risk_domains.update(args.trigger_risk_domain)
        runtime.persist()
        print(
            f"agent_id={runtime.identity.agent_id} cycle={runtime.cycle} "
            f"thread_id={thread.thread_id} status={thread.status.value} "
            f"next_review_cycle={thread.next_review_cycle} "
            f"review_interval_cycles={thread.review_interval_cycles}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
