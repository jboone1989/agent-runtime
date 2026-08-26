"""Command-line entry point for bounded Agent Runtime sessions."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from .backends import LegacyProviderBackend
from .embodiment import mock_physical_pack
from .llm.deepseek import DeepSeekAnthropicProvider
from .runtime import AgentRuntime
from .service import ContinuousRunner, clear_runtime_stop, read_runtime_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runtime",
        description="Start or resume an Agent Runtime and run a bounded session.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("state"),
        help="Persistent state directory (default: ./state).",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=1,
        help="Number of life-loop cycles to run (default: 1).",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Run continuously until interrupted or a STOP file is requested.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=0.0,
        help="Delay between cycles (default: 0).",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1,
        help="Persist a full checkpoint every N cycles (default: 1).",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=3,
        help="Stop after this many consecutive cycle failures (default: 3).",
    )
    parser.add_argument(
        "--journal-every",
        type=int,
        default=10,
        help="Append an observable lifecycle snapshot every N cycles (default: 10).",
    )
    parser.add_argument(
        "--reflection-every",
        type=int,
        default=0,
        help="Run a bounded LLM continuity reflection every N cycles; 0 disables it.",
    )
    parser.add_argument(
        "--reflect-on-start",
        action="store_true",
        help="Write one grounded continuity reflection when the runner starts.",
    )
    parser.add_argument(
        "--reflection-max-tokens",
        type=int,
        default=600,
        help="Maximum tokens for each periodic reflection (default: 600).",
    )
    parser.add_argument(
        "--clear-stop",
        action="store_true",
        help="Clear an existing STOP request before starting.",
    )
    parser.add_argument(
        "--physical",
        action="store_true",
        help="Create a new mock-physical agent instead of a digital agent.",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "Deprecated compatibility flag: connect the configured DeepSeek "
            "service through an advisory AgentBackend."
        ),
    )
    parser.add_argument(
        "--llm-prompt",
        help="Run one bounded LLM cognition probe after the life-loop cycles.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.ticks < 1:
        parser.error("--ticks must be at least 1")
    if args.idle_seconds < 0:
        parser.error("--idle-seconds cannot be negative")
    if args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be at least 1")
    if args.max_consecutive_errors < 1:
        parser.error("--max-consecutive-errors must be at least 1")
    if args.journal_every < 1:
        parser.error("--journal-every must be at least 1")
    if args.reflection_every < 0:
        parser.error("--reflection-every cannot be negative")
    if args.reflection_max_tokens < 64:
        parser.error("--reflection-max-tokens must be at least 64")

    state_dir = args.state_dir.resolve()
    state_file = state_dir / "agent_state.json"
    if args.clear_stop:
        clear_runtime_stop(state_dir)
    restored = state_file.exists()
    legacy_provider = (
        DeepSeekAnthropicProvider.from_environment()
        if args.llm or args.llm_prompt or args.reflect_on_start or args.reflection_every
        else None
    )
    agent_backend = (
        LegacyProviderBackend(legacy_provider) if legacy_provider is not None else None
    )
    if restored:
        runtime = AgentRuntime.restore(state_dir, agent_backend=agent_backend)
        if (
            args.physical
            and runtime.embodiment_descriptor.embodiment_type != "mock_physical"
        ):
            parser.error("--physical cannot replace an existing digital embodiment")
    else:
        embodiment = mock_physical_pack() if args.physical else None
        runtime = AgentRuntime.bootstrap(
            state_dir, embodiment=embodiment, agent_backend=agent_backend
        )

    start_cycle = runtime.cycle
    runner = ContinuousRunner(runtime)
    run_status = runner.run(
        max_ticks=None if args.forever else args.ticks,
        idle_seconds=args.idle_seconds,
        checkpoint_every=args.checkpoint_every,
        max_consecutive_errors=args.max_consecutive_errors,
        journal_every=args.journal_every,
        reflection_every=args.reflection_every or None,
        reflect_on_start=args.reflect_on_start,
        reflection_max_tokens=args.reflection_max_tokens,
    )
    llm_response = None
    if runtime.has_advisory_backend and _should_run_exit_probe(args):
        llm_response = asyncio.run(
            runtime.cognitive_probe(
                args.llm_prompt
                or "用中文简要说明你是谁、当前状态、能做什么，以及不能声称什么。"
            )
        )
    runtime.persist()
    summary = {
        "mode": "restored" if restored else "genesis",
        "agent_id": runtime.identity.agent_id,
        "genesis_id": runtime.identity.genesis_id,
        "embodiment": runtime.embodiment_descriptor.embodiment_type,
        "start_cycle": start_cycle,
        "end_cycle": runtime.cycle,
        "cycles_run": runtime.cycle - start_cycle,
        "run_status": run_status.status,
        "stop_reason": run_status.stop_reason,
        "usable_capabilities": runtime.capability_inventory.usable_ids(),
        "capability_usage": len(runtime.capability_usage),
        "open_capability_gaps": sum(
            gap.status.value == "OPEN" for gap in runtime.capability_gaps
        ),
        "goals": len(runtime.goals),
        "actions": len(runtime.records.actions),
        "outcomes": len(runtime.records.outcomes),
        "experience_traces": len(runtime.experience_traces),
        "verified_learning_evidence": len(runtime.verified_learning_evidence),
        # Kept for CLI JSON compatibility; new clients should read backend_connected.
        "llm_connected": agent_backend is not None,
        "backend_connected": agent_backend is not None,
        "llm_response": llm_response,
        "state_file": str(state_file),
        "heartbeat": read_runtime_status(state_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _should_run_exit_probe(args: argparse.Namespace) -> bool:
    """Avoid an unrequested extra provider call when a forever runner stops."""
    return args.llm_prompt is not None or (args.llm and not args.forever)


if __name__ == "__main__":
    raise SystemExit(main())
