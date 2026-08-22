"""Observable views of others. No intimacy, attachment, or trust feelings."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RelationshipEvidence:
    other_id: str
    interaction_count: int = 0
    last_act: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class OtherModelView:
    agent_id: str
    identity_verified: bool = False
    evidence_refs: list[str] = field(default_factory=list)
    public_capabilities: list[str] = field(default_factory=list)
