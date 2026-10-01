from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.integrations import (  # noqa: E402
    BROWSER_INSPECT,
    BROWSER_NAVIGATE,
    BrowserHarnessProvider,
    BrowserHarnessRuntime,
    BrowserPolicy,
)


def _provider(args: argparse.Namespace) -> BrowserHarnessProvider:
    return BrowserHarnessProvider(
        policy=BrowserPolicy(
            max_sessions=2,
            timeout_seconds=args.timeout,
            max_text_chars=4000,
            max_elements=120,
        ),
        runtime=BrowserHarnessRuntime(
            executable=args.executable,
            cdp_url=args.cdp_url,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real Browser Harness persistence acceptance"
    )
    parser.add_argument(
        "--executable",
        default="/home/ubuntu/browser-use-venv/bin/browser-harness",
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--url", default="https://chatgpt.com/")
    parser.add_argument("--session-id", default="acceptance-chatgpt")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    first = _provider(args)
    nav = first.invoke(
        BROWSER_NAVIGATE,
        {"session_id": args.session_id, "url": args.url},
    )
    inspected = first.invoke(
        BROWSER_INSPECT,
        {
            "session_id": args.session_id,
            "max_chars": 4000,
            "max_elements": 120,
        },
    )
    first.close()

    second = _provider(args)
    after_restart = second.invoke(
        BROWSER_INSPECT,
        {
            "session_id": args.session_id,
            "max_chars": 1,
            "max_elements": 120,
        },
    )

    names = [
        str(row.get("name") or "").strip().lower()
        for row in inspected["elements"]
    ]
    login_visible = any(name in {"log in", "login"} for name in names)
    expected_host = urlsplit(args.url).hostname
    host_before = urlsplit(inspected["url"]).hostname
    host_after = urlsplit(after_restart["url"]).hostname
    result = {
        "status": (
            "PASS"
            if host_before == host_after == expected_host
            else "FAIL"
        ),
        "provider": nav["provider"],
        "restart_semantics": nav["restart_semantics"],
        "url_before_restart": inspected["url"],
        "url_after_restart": after_restart["url"],
        "session_persisted": inspected["url"] == after_restart["url"],
        "login_control_visible": login_visible,
        "interactive_elements": len(inspected["elements"]),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
