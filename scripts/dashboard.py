"""Serve the read-only Agent dashboard at http://127.0.0.1:8765 ."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.dashboard.server import DashboardServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = DashboardServer(state_dir=args.state_dir, port=args.port)
    print(
        f"Agent dashboard: http://127.0.0.1:{server.port} "
        f"(state: {server.state_dir}, read-only)"
    )
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
