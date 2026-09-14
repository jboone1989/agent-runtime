"""Strict manifest for an Agent Runtime-hosted agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class AgentManifestError(ValueError):
    """The manifest is missing, malformed, or requests an invalid capability."""


class AgentCapabilityRequirement(BaseModel):
    """Provider-neutral capability need declared by a domain agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    intent: str = ""
    max_side_effect_level: Literal[
        "NONE", "READ_ONLY", "LOCAL_STATE", "EXTERNAL_SIDE_EFFECT", "IRREVERSIBLE"
    ] = "READ_ONLY"
    required_permissions: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _accept_short_form(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"capability_id": value}
        return value

    @field_validator("capability_id")
    @classmethod
    def _valid_capability_id(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized != value
            or len(normalized) > 128
            or any(ch.isspace() for ch in normalized)
        ):
            raise ValueError("capability_id must be a bounded non-whitespace identifier")
        return normalized

    @field_validator("required_permissions")
    @classmethod
    def _unique_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or item != item.strip() for item in value):
            raise ValueError("required_permissions must contain non-empty identifiers")
        if len(value) != len(set(value)):
            raise ValueError("required_permissions must be unique")
        return value


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
    required_capabilities: tuple[AgentCapabilityRequirement, ...] = ()
    optional_capabilities: tuple[AgentCapabilityRequirement, ...] = ()
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

    @model_validator(mode="after")
    def _unique_capability_requirements(self) -> "AgentManifest":
        required = [item.capability_id for item in self.required_capabilities]
        optional = [item.capability_id for item in self.optional_capabilities]
        if len(required) != len(set(required)):
            raise ValueError("required_capabilities must be unique")
        if len(optional) != len(set(optional)):
            raise ValueError("optional_capabilities must be unique")
        overlap = sorted(set(required) & set(optional))
        if overlap:
            raise ValueError(
                f"capability cannot be both required and optional: {overlap[0]}"
            )
        return self


def load_agent_manifest(path: str | Path) -> AgentManifest:
    """Load YAML with strict validation and a stable domain error."""

    target = Path(path)
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        return AgentManifest.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise AgentManifestError(f"invalid agent manifest: {target}") from exc
