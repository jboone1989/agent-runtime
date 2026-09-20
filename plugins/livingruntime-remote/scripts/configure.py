from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path


def config_path() -> Path:
    return Path.home() / ".livingruntime" / "remote.json"


def validate_host(value: str) -> str:
    value = value.strip()
    if not value or value.startswith("-") or any(ch.isspace() for ch in value):
        raise argparse.ArgumentTypeError("host must be an SSH config alias or host token without whitespace")
    return value


def test_ssh(host: str) -> None:
    remote_command = shlex.join(
        [
            "python3",
            "-c",
            "import getpass,socket; print(getpass.getuser()+'@'+socket.gethostname())",
        ]
    )
    proc = subprocess.run(
        [
            "ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10",
            host,
            remote_command,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "SSH verification failed. Configure key-based SSH first and verify the host key.\n"
            + (proc.stderr or proc.stdout)
        )
    print("SSH OK:", proc.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure LivingRuntime Remote without storing SSH passwords or private keys."
    )
    parser.add_argument("--host", type=validate_host, help="SSH config alias, e.g. livingruntime-vm")
    parser.add_argument("--root", action="append", dest="roots", help="Allowed remote workspace root; repeatable")
    parser.add_argument("--unit", action="append", dest="units", help="Allowlisted systemd .service unit; repeatable")
    parser.add_argument("--tunnel-id", dest="tunnel_id", help="OpenAI Secure MCP Tunnel id (non-secret); never store the runtime API key here")
    parser.add_argument("--test", action="store_true", help="Verify BatchMode SSH after writing configuration")
    parser.add_argument("--show", action="store_true", help="Show the current non-secret configuration")
    args = parser.parse_args()

    path = config_path()
    if args.show:
        if not path.exists():
            raise SystemExit(f"No configuration at {path}")
        print(path.read_text(encoding="utf-8"))
        return

    if not args.host:
        parser.error("--host is required unless --show is used")

    payload = {
        "ssh_host": args.host,
        "roots": args.roots or ["/home/ubuntu"],
        "systemd_units": args.units or [],
    }
    if args.tunnel_id:
        tunnel_id = args.tunnel_id.strip()
        if not tunnel_id.startswith("tunnel_"):
            raise SystemExit("tunnel id must start with tunnel_ and must not be an API key")
        payload["tunnel_id"] = tunnel_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)

    print(f"Wrote non-secret configuration to {path}")
    print("SSH credentials remain in your normal SSH config/agent; they are not copied into the plugin.")

    if args.test:
        test_ssh(args.host)


if __name__ == "__main__":
    main()
