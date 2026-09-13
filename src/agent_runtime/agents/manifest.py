"""Strict manifest for an Agent Runtime-hosted agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class AgentManifestError(ValueError):
    """The manifest is missing, malformed, or requests an invalid capability."""


class AgentManifest(BaseModel):
    """Portable declaration of a domain agent, not its mutable runtime state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = "1.0"
    agent_type: str
    name: str
    version: str
    description: str = ""
    runtime_api: str = ">=0.3,<1.0"
    capabilities: tuple[str, ...] = ()
    sensors: tuple[str, ...] = ()
    actuators: tuple[str, ...] = ()
    mind: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("manifest_version")
    @classmethod
    def _supported_manifest_version(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("unsupported manifest_version")
        return value

    @field_validator("agent_type")
    @classmethod
    def _valid_agent_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in normalized):
            raise ValueError("agent_type must use lowercase letters, digits, '-' or '_'")
        return normalized

    @field_validator("capabilities", "sensors", "actuators")
    @classmethod
    def _unique_nonempty_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("component names must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("component names must be unique")
        return value


def load_agent_manifest(path: str | Path) -> AgentManifest:
    """Load YAML with strict validation and a stable domain error."""

    target = Path(path)
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        return AgentManifest.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise AgentManifestError(f"invalid agent manifest: {target}") from exc

