"""Continue a persistent conversation with the current Agent instance."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agent_runtime.llm.deepseek import DeepSeekAnthropicProvider
from agent_runtime.runtime import AgentRuntime


async def converse(
    state_dir: Path,
    message: str,
    *,
    partner_id: str,
    challenge: bool,
) -> str:
    provider = DeepSeekAnthropicProvider.from_environment()
    runtime = AgentRuntime.restore(state_dir, llm_provider=provider)
    return await runtime.converse(
        message,
        partner_id=partner_id,
        challenge=challenge,
        max_tokens=1600,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--partner-id", default="codex-interviewer")
    parser.add_argument("--challenge", action="store_true")
    parser.add_argument("message")
    args = parser.parse_args()
    answer = asyncio.run(
        converse(
            args.state_dir.resolve(),
            args.message,
            partner_id=args.partner_id,
            challenge=args.challenge,
        )
    )
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
