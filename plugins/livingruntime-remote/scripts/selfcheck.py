from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    python_files = [
        ROOT / "scripts" / "bridge.py",
        ROOT / "scripts" / "launcher.py",
        ROOT / "scripts" / "configure.py",
    ]
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    legacy = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    legacy_mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert plugin["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert plugin["name"] == "livingruntime-remote"
    assert mcp["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert mcp["mcpServers"]["livingruntime_remote"]["type"] == "stdio"
    assert mcp["mcpServers"]["livingruntime_remote"]["command"] == "python"
    assert legacy["mcpServers"] == "./.mcp.json"
    assert legacy_mcp["mcpServers"]["livingruntime_remote"]["type"] == "stdio"

    combined = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*") if path.is_file())
    forbidden = ["BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "password="]
    for marker in forbidden:
        assert marker not in combined, f"secret-like marker found: {marker}"

    print("LivingRuntime Remote plugin self-check OK")


if __name__ == "__main__":
    main()
