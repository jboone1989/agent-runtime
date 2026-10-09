"""Run an existing bounded coding capability through the real Pi Durable package."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agent_runtime import ApplicationRuntime, BoundedCodingProvider, CodingPolicy
from agent_runtime.durable_runtime import AgentTask, TaskStep


async def run(args):
    app = ApplicationRuntime.bootstrap(args.state, subject_id="pi-capability-demo",
        allowed_permissions=frozenset({"workspace.code.read"}))
    app.register_provider(BoundedCodingProvider(CodingPolicy(repo_root=Path(args.repo))))
    task = AgentTask(id=args.task_id, goal="Read source for the agent's next decision",
        steps=(TaskStep(id="read", handler="code.read", payload={"path": args.path},
                        replay_safe=True, max_attempts=2),))
    result = await app.run(task)
    print(result.model_dump_json(indent=2))
    print("Durable application events:")
    for event in await app.durable.events(task.id):
        print(f"{event['id']}: {event['kind']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--task-id", required=True,
                        help="Reuse to replay the same task; use a new id for a new request")
    asyncio.run(run(parser.parse_args()))
