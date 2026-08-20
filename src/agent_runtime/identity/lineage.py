"""Replica, Successor, and Offspring are distinct from day one."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ContinuityKind(str, Enum):
    REPLICA = "REPLICA"
    SUCCESSOR = "SUCCESSOR"
    OFFSPRING = "OFFSPRING"


@dataclass
class ReplicaHandle:
    """Same identity, another running copy (HA / failover)."""

    agent_id: str
    replica_id: str
    started_at: datetime
    kind: ContinuityKind = ContinuityKind.REPLICA


@dataclass
class SuccessorRef:
    """Same identity, new version (evolution / upgrade)."""

    agent_id: str
    from_version: str
    to_version: str
    kind: ContinuityKind = ContinuityKind.SUCCESSOR


@dataclass
class LineageLink:
    """Parent → child with a new identity (offspring)."""

    parent_agent_id: str
    child_agent_id: str
    lineage_root: str
    kind: ContinuityKind = ContinuityKind.OFFSPRING
