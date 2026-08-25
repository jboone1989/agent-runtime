from __future__ import annotations

import json
from urllib.request import urlopen

from agent_runtime.dashboard.server import DashboardServer


def test_server_serves_digest_and_index(tmp_path) -> None:
    server = DashboardServer(state_dir=tmp_path, port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urlopen(base + "/api/digest", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["status"] == "UNKNOWN"  # 空状态目录
            assert set(payload["activations"].keys())
        with urlopen(base + "/", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in body or "<html" in body
        with urlopen(base + "/api/digest?since=999", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["events"] == []
    finally:
        server.stop()
