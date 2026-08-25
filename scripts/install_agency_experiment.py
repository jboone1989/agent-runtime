"""Schedule an evidence-linked one-shot Agent-selected internal experiment."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.agency.experiment import (
    AgencyExperiment,
    AgencyExperimentStatus,
)
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import RuntimeLease


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--action-kind", required=True)
    parser.add_argument("--resource", default="self")
    parser.add_argument("--dimension")
    parser.add_argument("--expected-min", type=float)
    parser.add_argument("--expected-max", type=float)
    parser.add_argument("--run-in", type=int, default=1)
    parser.add_argument(
        "--no-require-prediction",
        action="store_true",
        help="Allow a read-only experiment such as introspect to complete without a self-prediction.",
    )
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()
    if args.run_in < 1:
        parser.error("--run-in must be positive")
    if not args.evidence:
        parser.error("at least one --evidence is required")

    state_dir = args.state_dir.resolve()
    with RuntimeLease(state_dir):
        runtime = AgentRuntime.restore(state_dir)
        if runtime.agency_thread_tracker.thread(args.thread_id) is None:
            parser.error(f"unknown agency thread: {args.thread_id}")
        payload = {}
        if args.dimension:
            payload["dimension"] = args.dimension
        if (args.expected_min is None) != (args.expected_max is None):
            parser.error("--expected-min and --expected-max must be provided together")
        if args.expected_min is not None:
            if args.expected_min > args.expected_max:
                parser.error("expected minimum cannot exceed maximum")
            payload["expected_range"] = [args.expected_min, args.expected_max]
        experiment = runtime.agency_experiment_manager.schedule(
            AgencyExperiment(
                experiment_id=args.experiment_id,
                thread_id=args.thread_id,
                statement=args.statement,
                action_kind=args.action_kind,
                resource=args.resource,
                payload=payload,
                scheduled_cycle=runtime.cycle + args.run_in,
                status=AgencyExperimentStatus.PLANNED,
                evidence_refs=list(args.evidence),
                requires_prediction=not args.no_require_prediction,
            )
        )
        runtime.persist()
        print(
            f"agent_id={runtime.identity.agent_id} cycle={runtime.cycle} "
            f"experiment_id={experiment.experiment_id} "
            f"scheduled_cycle={experiment.scheduled_cycle} "
            f"status={experiment.status.value}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
