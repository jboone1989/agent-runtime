from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.integrations import (  # noqa: E402
    BROWSER_TAKEOVER_START,
    BROWSER_TAKEOVER_STATUS,
    BrowserTakeoverUnavailable,
    BrowserTakeoverPolicy,
    PersistentBrowserTakeoverProvider,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Supervise one persistent Agent Runtime browser takeover profile"
    )
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--display", type=int, required=True)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--websocket-port", type=int, required=True)
    parser.add_argument(
        "--health-seconds",
        type=float,
        default=10.0,
        help="Seconds between browser/takeover health checks",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Start or recover the profile, verify it once, and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.health_seconds <= 0:
        raise SystemExit("--health-seconds must be positive")

    policy = BrowserTakeoverPolicy(
        display_start=args.display,
        display_end=args.display,
        cdp_port_start=args.cdp_port,
        cdp_port_end=args.cdp_port,
        websocket_port_start=args.websocket_port,
        websocket_port_end=args.websocket_port,
    )
    provider = PersistentBrowserTakeoverProvider(
        state_root=args.state_root,
        policy=policy,
        profile_dirs={args.profile_id: args.profile_dir},
    )
    started = provider.invoke(
        BROWSER_TAKEOVER_START,
        {"profile_id": args.profile_id, "url": args.url},
    )
    if not started["active"] or not started["browser_running"]:
        raise RuntimeError("browser takeover did not become active")
    print(
        json.dumps(
            {
                "status": "READY",
                "profile_id": args.profile_id,
                "profile_dir": str(args.profile_dir),
                "display": args.display,
                "cdp_url": started["cdp_url"],
                "takeover_url": started["url"],
                "websocket_url": started["websocket_url"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.once:
        provider.close()
        return 0

    stopping = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while not stopping.wait(args.health_seconds):
            status = provider.invoke(
                BROWSER_TAKEOVER_STATUS,
                {"profile_id": args.profile_id},
            )
            if not status["active"] or not status["browser_running"]:
                try:
                    recovered = provider.invoke(
                        BROWSER_TAKEOVER_START,
                        {"profile_id": args.profile_id, "url": args.url},
                    )
                except BrowserTakeoverUnavailable as exc:
                    print(
                        json.dumps(
                            {
                                "status": "RECOVERY_FAILED",
                                "profile_id": args.profile_id,
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                if not recovered["active"] or not recovered["browser_running"]:
                    print(
                        json.dumps(
                            {
                                "status": "RECOVERY_INCOMPLETE",
                                "profile_id": args.profile_id,
                                "takeover": recovered,
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                print(
                    json.dumps(
                        {
                            "status": "RECOVERED",
                            "profile_id": args.profile_id,
                            "cdp_url": recovered["cdp_url"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    finally:
        # Closing the provider intentionally leaves Chrome, Xvnc and websockify
        # alive. A replacement supervisor can adopt the same runtime state.
        provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
