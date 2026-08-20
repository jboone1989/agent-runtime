"""Continuity: restarting a process is not death."""
from __future__ import annotations

from .identity import AgentIdentity


def is_same_agent(before: AgentIdentity, after: AgentIdentity) -> bool:
    return (
        before.agent_id == after.agent_id
        and before.genesis_id == after.genesis_id
        and before.lineage_root == after.lineage_root
    )
