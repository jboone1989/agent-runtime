from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

os.environ.setdefault(
    "LIVINGRUNTIME_REMOTE_CONFIG",
    str(Path(__file__).resolve().parent / "fixtures" / "missing-config.json"),
)

from contract import CORE_TOOLS  # noqa: E402
import bridge  # noqa: E402


def _write_config(directory: Path) -> Path:
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


class DiscoveryTests(unittest.TestCase):
    def test_tools_list_includes_acceptance_tools(self) -> None:
        names = bridge.advertised_tool_names()
        missing = [name for name in CORE_TOOLS if name not in names]
        self.assertEqual(missing, [], msg=f"tools/list is missing {missing}; got {names}")

    def test_async_list_tools_matches_core_contract(self) -> None:
        tools = asyncio.run(bridge.server.list_tools())
        names = {tool.name for tool in tools}
        self.assertTrue(set(CORE_TOOLS).issubset(names))
        by_name = {tool.name: tool for tool in tools}
        for name in CORE_TOOLS:
            schema = by_name[name].inputSchema or {}
            self.assertEqual(schema.get("type"), "object")
            self.assertTrue(by_name[name].description)

    def test_core_tool_annotations_are_explicit(self) -> None:
        tools = asyncio.run(bridge.server.list_tools())
        by_name = {tool.name: tool for tool in tools}
        expected = {
            "connection_status": (True, False, False),
            "read_file": (True, False, False),
            "git": (False, True, True),
            "logs": (True, False, False),
        }
        for name, (read_only, destructive, open_world) in expected.items():
            annotations = by_name[name].annotations
            self.assertIsNotNone(annotations)
            self.assertEqual(annotations.readOnlyHint, read_only, name)
            self.assertEqual(annotations.destructiveHint, destructive, name)
            self.assertEqual(annotations.openWorldHint, open_world, name)


class SchemaAndToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = _write_config(Path(self.tmp.name))
        self.workspace = Path(self.tmp.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "README.md").write_text("hello from livingruntime\n", encoding="utf-8")
        self.audit = Path(self.tmp.name) / "remote-audit.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ssh(self, remote_argv, *, stdin=None, timeout=30):
        command = " ".join(map(str, remote_argv))
        if "getpass" in command:
            return {
                "returncode": 0,
                "stdout": json.dumps({"user": "ubuntu", "hostname": "livingruntime-host", "cwd": "/home/ubuntu"}),
                "stderr": "",
                "duration_ms": 12.5,
            }
        if remote_argv and remote_argv[0] == "journalctl":
            self.assertIn("content-agent.service", remote_argv)
            return {
                "returncode": 0,
                "stdout": "Sep 20 00:00:00 host content-agent[1]: ready\n",
                "stderr": "",
                "duration_ms": 8.0,
            }
        if stdin:
            payload = json.loads(stdin.decode("utf-8"))
            if payload["op"] == "read_file":
                content = (self.workspace / "README.md").read_text(encoding="utf-8")
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "path": "/home/ubuntu/README.md",
                            "offset": 0,
                            "bytes_read": len(content),
                            "content": content,
                            "sha256": "deadbeef",
                        }
                    ),
                    "stderr": "",
                    "duration_ms": 9.0,
                }
            if payload["op"] == "exec" and payload.get("argv", [None])[0] == "git":
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "returncode": 0,
                            "stdout": "On branch main\n",
                            "stderr": "",
                            "cwd": payload.get("cwd"),
                        }
                    ),
                    "stderr": "",
                    "duration_ms": 11.0,
                }
        raise AssertionError(f"unexpected ssh call {remote_argv!r}")

    def test_connection_status_shape_and_no_secrets(self) -> None:
        with patch.object(bridge, "_config_path", return_value=str(self.config)), patch.object(bridge, "_ssh", self._ssh):
            status = bridge.connection_status()
        self.assertTrue(status["ok"])
        self.assertTrue(status["endpoint"]["reachable"])
        self.assertEqual(status["remote_host"]["hostname"], "livingruntime-host")
        self.assertTrue(status["connectivity"]["ssh"])
        self.assertTrue(status["connectivity"]["authenticated"])
        self.assertTrue(status["connectivity"]["authorized"])
        self.assertIsNotNone(status["connectivity"]["latency_ms"])
        self.assertIsNotNone(status["connectivity"]["last_success_unix"])
        blob = json.dumps(status)
        self.assertNotIn("sk-", blob)
        self.assertNotIn("TOKEN", blob)
        self.assertNotIn("BEGIN", blob)

    def test_read_file_git_logs_integration(self) -> None:
        with patch.object(bridge, "_config_path", return_value=str(self.config)), patch.object(bridge, "_ssh", self._ssh):
            file_result = bridge.read_file("/home/ubuntu/README.md")
            git_result = bridge.git("/home/ubuntu", ["status"])
            log_result = bridge.logs("content-agent.service", lines=50)
        self.assertIn("hello from livingruntime", file_result["content"])
        self.assertEqual(git_result["returncode"], 0)
        self.assertIn("content-agent", log_result["stdout"])

    def test_logs_reject_units_outside_allowlist(self) -> None:
        with patch.object(bridge, "_config_path", return_value=str(self.config)):
            with self.assertRaises(PermissionError):
                bridge.logs("ssh.service")

    def test_git_blocks_credential_helper(self) -> None:
        with patch.object(bridge, "_config_path", return_value=str(self.config)):
            with self.assertRaises(PermissionError):
                bridge.git("/home/ubuntu", ["-c", "credential.helper=!evil", "status"])

    def test_http_transport_refuses_public_bind(self) -> None:
        with self.assertRaises(ValueError):
            bridge.configure_http_transport("0.0.0.0", 8766)
        bridge.configure_http_transport("127.0.0.1", 8766)
        self.assertEqual(bridge._ACTIVE_TRANSPORT, "streamable-http")
        self.assertEqual(bridge._HTTP_BIND, "127.0.0.1:8766")
        self.assertFalse(bridge._transport_endpoint()["public_ingress"])


if __name__ == "__main__":
    unittest.main()
