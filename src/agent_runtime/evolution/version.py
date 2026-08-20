"""Evolution schemas only — no activation in v0.1."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentVersion:
    agent_id: str
    version: str
    created_at: datetime
    notes: str = ""


@dataclass
class EvolutionProposal:
    proposal_id: str
    agent_id: str
    reason: str
    target_version: str
    created_at: datetime


@dataclass
class EvolutionEvaluation:
    proposal_id: str
    passed: bool
    notes: str = ""
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class EvolutionActivation:
    proposal_id: str
    from_version: str
    to_version: str
    activated_at: datetime


@dataclass
class EvolutionRollback:
    activation_id: str
    restored_version: str
    reason: str
    rolled_back_at: datetime
