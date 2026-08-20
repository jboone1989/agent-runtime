"""Runtime configuration. LLM model name is loaded from YAML, never hardcoded."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path(__file__).parent / "default.yaml"


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: float = 60.0
    max_retries: int = 3


class DimensionBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_min: float
    preferred_max: float
    critical_min: float
    critical_max: float


class ViabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: dict[str, DimensionBounds]
    initial_value: float = 0.8
    threat_threshold: float = 0.2
    goal_threat_threshold: float = 0.25


class GovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_compute_cost: float = 1.0
    max_financial_cost: float = 0.0
    allowed_actions: list[str] = Field(default_factory=list)
    allowed_resources: list[str] = Field(default_factory=list)
    allowed_networks: list[str] = Field(default_factory=list)
    allowed_deployment_targets: list[str] = Field(default_factory=list)
    may_spawn_replicas: bool = False
    may_create_offspring: bool = False
    require_human_approval: list[str] = Field(default_factory=list)


class IdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_version: str = "0.1.0"


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist_every_cycle: bool = True
    predict_horizon_hours: float = 1.0


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: IdentityConfig
    llm: LLMConfig
    viability: ViabilityConfig
    governance: GovernanceConfig
    runtime: LoopConfig


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(data)
