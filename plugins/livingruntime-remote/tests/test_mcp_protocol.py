from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CORE_TOOLS = ("connection_status", "read_file", "git", "logs")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config(directory: Path) -> Path:
    path = directory / "remote.json"
    path.write_text(
        json.dumps(
            {
                "ssh_host": "livingruntime-vm",
                "roots": ["/home/ubuntu"],
                "systemd_units": ["content-agent.service"],
            }
        ),
        encoding="utf-8",
    )
    return path


class McpProtocolDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stdio_tools_list_returns_acceptance_tools(self) -> None:
        async def _run() -> list[str]:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(SCRIPTS / "bridge.py")],
                env={
                    **os.environ,
                    "LIVINGRUNTIME_REMOTE_CONFIG": str(self.config),
                    "PYTHONPATH": str(SCRIPTS),
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    return sorted(tool.name for tool in listed.tools)

        names = asyncio.run(_run())
        missing = [name for name in CORE_TOOLS if name not in names]
        self.assertEqual(missing, [], msg=f"stdio tools/list missing {missing}; got {names}")

    def test_http_loopback_tools_list_returns_acceptance_tools(self) -> None:
        port = _free_port()
        env = {
            **os.environ,
            "LIVINGRUNTIME_REMOTE_CONFIG": str(self.config),
            "PYTHONPATH": str(SCRIPTS),
        }
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "bridge.py"), "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            deadline = time.time() + 15
            last_error = None
            names: list[str] = []

            async def _run() -> list[str]:
                async with streamable_http_client(url) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        return sorted(tool.name for tool in listed.tools)

            while time.time() < deadline:
                try:
                    names = asyncio.run(_run())
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if proc.poll() is not None:
                        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                        raise RuntimeError(f"HTTP MCP exited early: {stderr}") from exc
                    time.sleep(0.2)
            if last_error is not None:
                raise last_error
            missing = [name for name in CORE_TOOLS if name not in names]
            self.assertEqual(missing, [], msg=f"http tools/list missing {missing}; got {names}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
