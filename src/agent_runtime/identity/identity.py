"""AgentIdentity is not bound to process, container, or machine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..clock import Clock, new_id


@dataclass
class AgentIdentity:
    agent_id: str
    genesis_id: str
    created_at: datetime
    current_version: str
    parent_agent_id: str | None
    lineage_root: str
    public_key_id: str


def create_genesis_identity(version: str, clock: Clock) -> AgentIdentity:
    agent_id = new_id("agent")
    return AgentIdentity(
        agent_id=agent_id,
        genesis_id=agent_id,
        created_at=clock.now(),
        current_version=version,
        parent_agent_id=None,
        lineage_root=agent_id,
        public_key_id=new_id("pk"),
    )
