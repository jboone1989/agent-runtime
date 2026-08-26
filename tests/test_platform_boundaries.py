"""Static guards against accidental parallel runtimes and vendor leakage."""
from __future__ import annotations

import ast
from pathlib import Path

from agent_runtime.agents import load_agent_manifest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent_runtime"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_runtime_production_code_does_not_import_virtualbrain() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if any(name == "virtualbrain" or name.startswith("virtualbrain.") for name in _imports(path)):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_vendor_agent_sdks_are_isolated_to_backend_adapters() -> None:
    vendor_roots = ("openai_codex", "claude_agent_sdk")
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.parent == SRC / "backends":
            continue
        if any(
            name == root or name.startswith(root + ".")
            for name in _imports(path)
            for root in vendor_roots
        ):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_agent_starter_manifest_is_valid() -> None:
    manifest = load_agent_manifest(ROOT / "templates" / "agent-starter" / "agent.yaml")
    assert manifest.agent_type == "example-agent"
