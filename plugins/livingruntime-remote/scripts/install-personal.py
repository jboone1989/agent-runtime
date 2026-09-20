from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

PLUGIN_NAME = "livingruntime-remote"
MARKETPLACE_NAME = "livingruntime-local"
SKIP_DIR_NAMES = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def plugin_source() -> Path:
    return Path(__file__).resolve().parents[1]


def personal_plugin_dir() -> Path:
    return Path.home() / ".codex" / "plugins" / PLUGIN_NAME


def personal_marketplace_path() -> Path:
    return Path.home() / ".agents" / "plugins" / "marketplace.json"


def copy_plugin(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_path: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in SKIP_DIR_NAMES}
        skipped.update(name for name in names if Path(name).suffix in SKIP_SUFFIXES)
        return skipped

    shutil.copytree(source, destination, ignore=ignore)


def marketplace_entry() -> dict:
    return {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": "./.codex/plugins/livingruntime-remote",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Developer Tools",
    }


def merge_marketplace(path: Path) -> dict:
    payload = {
        "name": MARKETPLACE_NAME,
        "interface": {
            "displayName": "LivingRuntime Local",
            "shortDescription": "Windows user-level marketplace for LivingRuntime Remote. Not a public Plugins Directory listing.",
        },
        "plugins": [marketplace_entry()],
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            plugins = [
                plugin
                for plugin in existing.get("plugins") or []
                if isinstance(plugin, dict) and plugin.get("name") != PLUGIN_NAME
            ]
            plugins.append(marketplace_entry())
            existing["plugins"] = plugins
            existing.setdefault("name", MARKETPLACE_NAME)
            interface = existing.get("interface") or {}
            if isinstance(interface, dict):
                interface.setdefault("displayName", "LivingRuntime Local")
                existing["interface"] = interface
            return existing
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install LivingRuntime Remote as a Windows user-level ChatGPT/Codex plugin."
    )
    parser.add_argument("--source", type=Path, default=plugin_source())
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "plugin.json").exists():
        raise SystemExit(f"plugin.json not found in {source}")

    destination = personal_plugin_dir()
    copy_plugin(source, destination)

    marketplace = personal_marketplace_path()
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    payload = merge_marketplace(marketplace)
    marketplace.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        marketplace.chmod(0o600)

    print(f"Installed plugin to {destination}")
    print(f"Wrote personal marketplace {marketplace}")
    print("Fully quit and restart ChatGPT Desktop, then install LivingRuntime Remote from LivingRuntime Local.")
    print("After plugin source changes, rerun this installer and restart Desktop; ChatGPT uses a cached copy.")


if __name__ == "__main__":
    main()
