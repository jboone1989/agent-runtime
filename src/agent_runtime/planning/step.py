"""A single executable step. required_capabilities is the Resolver input (v0.2.4)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capabilities.model import CapabilityRequirement
from .state import PlanStatus


@dataclass
class PlanStep:
    step_id: str
    objective: str
    required_capabilities: list[CapabilityRequirement] = field(default_factory=list)
    status: PlanStatus = PlanStatus.PROPOSED
    evidence_refs: list[str] = field(default_factory=list)
    caused_by: list[str] = field(default_factory=list)
