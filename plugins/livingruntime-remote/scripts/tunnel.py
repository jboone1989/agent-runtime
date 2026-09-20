from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from contract import DEFAULT_HTTP_PORT, LOOPBACK_HOSTS

RELEASES_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
PROFILE = "livingruntime-remote"


def config_path() -> Path:
    return Path.home() / ".livingruntime" / "remote.json"


def bin_dir() -> Path:
    return Path.home() / ".livingruntime" / "bin"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("remote config must be a JSON object")
    return value


def launcher_command(python: str | None = None) -> list[str]:
    launcher = Path(__file__).resolve().with_name("launcher.py")
    return [python or sys.executable, str(launcher)]


def quoted_mcp_command(python: str | None = None) -> str:
    command = launcher_command(python)
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def resolve_tunnel_id(explicit: str | None = None) -> str:
    value = (explicit or os.environ.get("CONTROL_PLANE_TUNNEL_ID") or load_config().get("tunnel_id") or "").strip()
    if not value:
        raise SystemExit(
            "Missing tunnel id. Create one in OpenAI Platform tunnel settings, then pass --tunnel-id "
            "or set CONTROL_PLANE_TUNNEL_ID. Do not put the runtime API key in the plugin config."
        )
    if value.startswith("sk-") or " " in value:
        raise SystemExit("That value looks like a secret. Use the tunnel_... identifier, not an API key.")
    if not value.startswith("tunnel_"):
        raise SystemExit("tunnel id must start with tunnel_")
    return value


def require_runtime_key() -> str:
    key = os.environ.get("CONTROL_PLANE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "CONTROL_PLANE_API_KEY is required to run tunnel-client. Export the runtime key from "
            "Platform tunnel settings; never store it in remote.json or the plugin."
        )
    return key


def validate_http_bind(host: str) -> str:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("HTTP MCP bind must stay on loopback; do not expose 8765/8766 to the public internet")
    return host


def _cpu_arch() -> str:
    if hasattr(os, "uname"):
        return os.uname().machine.lower()
    return (os.environ.get("PROCESSOR_ARCHITECTURE") or os.environ.get("PROCESSOR_ARCHITEW6432") or "").lower()


def platform_asset_tokens() -> tuple[str, ...]:
    system = sys.platform
    machine = _cpu_arch()
    if system.startswith("win"):
        arch = "arm64" if "arm" in machine else "amd64"
        return ("windows", "win", arch)
    if system == "darwin":
        arch = "arm64" if "arm" in machine else "amd64"
        return ("darwin", "macos", arch)
    arch = "arm64" if "arm" in machine else "amd64"
    return ("linux", arch)


def choose_release_asset(assets: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = platform_asset_tokens()
    scored = []
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        if "tunnel-client" not in name and "tunnel_client" not in name:
            continue
        if not all(token in name for token in tokens[-1:]):
            continue
        score = sum(1 for token in tokens if token in name)
        if os.name == "nt" and name.endswith(".exe"):
            score += 2
        scored.append((score, name, asset))
    if not scored:
        raise SystemExit(f"No tunnel-client release asset matched this platform: {tokens}")
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def find_tunnel_client(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for name in ("tunnel-client.exe", "tunnel-client"):
        found = shutil.which(name)
        if found:
            return Path(found)
        candidate = bin_dir() / name
        if candidate.exists():
            return candidate
    return None


def install_tunnel_client() -> Path:
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "livingruntime-remote"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    asset = choose_release_asset(payload.get("assets") or [])
    url = asset.get("browser_download_url")
    if not url:
        raise SystemExit("tunnel-client release asset is missing a download URL")
    destination = bin_dir() / ("tunnel-client.exe" if os.name == "nt" else "tunnel-client")
    destination.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IEXEC)
    return destination


def chatgpt_steps(tunnel_id: str) -> str:
    return f"""ChatGPT ordinary chat setup (Secure MCP Tunnel)

1. Keep this tunnel-client process running. Discovery and every tool call need it.
2. In ChatGPT, enable Developer mode if your plan allows it.
3. Open ChatGPT Plugins and select the plus button.
4. Name the app LivingRuntime Remote.
5. Under Connection, choose Tunnel and select or paste:
   {tunnel_id}
6. Create the app, review the discovered tools, and confirm these names exist:
   connection_status, read_file, git, logs
7. Open a new ordinary chat, enable LivingRuntime Remote from the tools/plugins menu,
   then call connection_status -> read_file -> git -> logs.

Do not paste a localhost URL into ChatGPT. Do not expose port 8765 or 8766 to the public internet.
A local marketplace entry or Codex stdio config is not evidence that ChatGPT ordinary chat can see this plugin.
"""


def build_init_argv(client: Path, tunnel_id: str, *, http: bool, host: str, port: int) -> list[str]:
    argv = [
        str(client),
        "init",
        "--sample",
        "sample_mcp_stdio_local",
        "--profile",
        PROFILE,
        "--tunnel-id",
        tunnel_id,
    ]
    if http:
        validate_http_bind(host)
        argv.extend(["--mcp-server-url", f"http://{host}:{int(port)}/mcp"])
    else:
        argv.extend(["--mcp-command", quoted_mcp_command()])
    return argv


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Connect LivingRuntime Remote to ChatGPT through OpenAI Secure MCP Tunnel."
    )
    parser.add_argument("--tunnel-id", help="OpenAI tunnel_... identifier")
    parser.add_argument("--client", help="Path to tunnel-client binary")
    parser.add_argument("--install-client", action="store_true", help="Download the latest public tunnel-client binary")
    parser.add_argument("--http", action="store_true", help="Tunnel a loopback streamable HTTP MCP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--doctor", action="store_true", help="Run tunnel-client doctor for this profile")
    parser.add_argument("--print-steps", action="store_true", help="Print ChatGPT connection steps and exit")
    parser.add_argument("--init-only", action="store_true", help="Write the tunnel profile without starting the daemon")
    args = parser.parse_args(argv)

    tunnel_id = resolve_tunnel_id(args.tunnel_id)
    if args.print_steps:
        print(chatgpt_steps(tunnel_id))
        return

    if args.http:
        validate_http_bind(args.host)

    client = find_tunnel_client(args.client)
    if client is None and args.install_client:
        client = install_tunnel_client()
    if client is None:
        raise SystemExit(
            "tunnel-client was not found. Install it from Platform tunnel settings or "
            "https://github.com/openai/tunnel-client/releases/latest, then rerun with --client or --install-client."
        )

    env = os.environ.copy()
    env["CONTROL_PLANE_TUNNEL_ID"] = tunnel_id
    if not args.print_steps:
        env["CONTROL_PLANE_API_KEY"] = require_runtime_key()

    init_argv = build_init_argv(client, tunnel_id, http=args.http, host=args.host, port=args.port)
    subprocess.check_call(init_argv, env=env)
    if args.doctor:
        subprocess.check_call([str(client), "doctor", "--profile", PROFILE, "--explain"], env=env)
        return
    if args.init_only:
        print(chatgpt_steps(tunnel_id))
        return
    print(chatgpt_steps(tunnel_id))
    raise SystemExit(subprocess.call([str(client), "run", "--profile", PROFILE], env=env))


if __name__ == "__main__":
    main()
