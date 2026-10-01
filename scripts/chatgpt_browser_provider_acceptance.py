from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real ChatGPT Browser Provider acceptance"
    )
    parser.add_argument(
        "--prompt",
        default=(
            "Reply with exactly: AGENT_RUNTIME_CHATGPT_PROVIDER_OK"
        ),
    )
    args = parser.parse_args()

    provider = ChatGPTBrowserProvider.from_environment()
    response = asyncio.run(provider.complete(args.prompt))
    expected = "AGENT_RUNTIME_CHATGPT_PROVIDER_OK"
    status = "PASS" if expected in response else "FAIL"
    print(
        json.dumps(
            {
                "status": status,
                "provider": provider.provider_name,
                "model": provider.model,
                "response": response,
                "metadata": provider.last_metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
