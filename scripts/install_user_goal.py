"""Install an operator USER goal into the current Agent instance.

Use --no-auto-complete for standing long-horizon objectives: the goal stays
ACTIVE even after the machinery's two-step plan finishes.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import RuntimeLease


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--reason", default="operator submitted")
    parser.add_argument(
        "--no-auto-complete",
        action="store_true",
        help="Keep the goal ACTIVE after its generated plan completes.",
    )
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    with RuntimeLease(state_dir):
        runtime = AgentRuntime.restore(state_dir)
        goal = runtime.submit_goal(
            args.description,
            reason=args.reason,
            auto_complete=not args.no_auto_complete,
        )
        runtime.persist()
        print(
            f"agent_id={runtime.identity.agent_id} cycle={runtime.cycle} "
            f"goal_id={goal.goal_id} origin={goal.origin.value} "
            f"status={goal.status.value} auto_complete={goal.auto_complete}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
