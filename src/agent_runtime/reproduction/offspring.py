"""Reproduction schemas. v0.1 never auto-executes offspring creation."""
from __future__ import annotations

from dataclasses import dataclass

from ..governance.autonomy import AutonomyPolicy
from ..governance.budget import ResourceBudget


@dataclass
class OffspringProposal:
    parent_agent_id: str
    reason: str
    initial_goal: str
    inherited_capabilities: list[str]
    inherited_memory_policy: str
    resource_budget: ResourceBudget
    autonomy_policy: AutonomyPolicy
