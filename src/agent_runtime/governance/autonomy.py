"""Operator authorization. Survival cannot raise these ceilings."""
from __future__ import annotations

from dataclasses import dataclass

from ..config.loader import GovernanceConfig, RuntimeConfig


@dataclass
class AutonomyPolicy:
    allowed_actions: set[str]
    allowed_resources: set[str]
    max_compute_cost: float
    max_financial_cost: float
    allowed_networks: list[str]
    allowed_deployment_targets: list[str]
    may_spawn_replicas: bool
    may_create_offspring: bool
    require_human_approval: set[str]

    @classmethod
    def from_config(cls, cfg: GovernanceConfig) -> AutonomyPolicy:
        return cls(
            allowed_actions=set(cfg.allowed_actions),
            allowed_resources=set(cfg.allowed_resources),
            max_compute_cost=cfg.max_compute_cost,
            max_financial_cost=cfg.max_financial_cost,
            allowed_networks=list(cfg.allowed_networks),
            allowed_deployment_targets=list(cfg.allowed_deployment_targets),
            may_spawn_replicas=cfg.may_spawn_replicas,
            may_create_offspring=cfg.may_create_offspring,
            require_human_approval=set(cfg.require_human_approval),
        )

    @classmethod
    def default(cls) -> AutonomyPolicy:
        from ..config.loader import load_config

        return cls.from_config(load_config().governance)


def policy_from_runtime_config(cfg: RuntimeConfig) -> AutonomyPolicy:
    return AutonomyPolicy.from_config(cfg.governance)
