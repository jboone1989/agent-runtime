"""Commitments. Public statements are exportable; private intent is not."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..time.deadline import Deadline


class CommitmentVisibility(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class CommitmentState(str, Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    FULFILLED = "FULFILLED"
    VIOLATED = "VIOLATED"
    RENEGOTIATING = "RENEGOTIATING"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class CommitmentConstraint:
    """Machine-readable restriction created by a commitment.

    Constraints can only remove choices already permitted by governance.  The
    default scope is autonomous/internal action so a self-authored commitment
    cannot overrule an explicit operator instruction or safety regulation.
    """

    constraint_id: str
    forbidden_action_kinds: set[str] = field(default_factory=set)
    forbidden_resources: set[str] = field(default_factory=set)
    applies_to_triggers: set[str] = field(default_factory=lambda: {"INTERNAL"})
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    def conflicts_with(self, *, kind: str, resource: str, trigger: str) -> bool:
        if trigger not in self.applies_to_triggers:
            return False
        return (
            kind in self.forbidden_action_kinds
            or resource in self.forbidden_resources
        )


@dataclass(frozen=True)
class CommitmentRevision:
    revision_id: str
    state: CommitmentState
    actor_id: str
    reason: str
    timestamp: datetime
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class Commitment:
    commitment_id: str
    counterpart_id: str
    statement: str
    visibility: CommitmentVisibility
    state: CommitmentState
    intent: str | None = None
    deadline: Deadline | None = None
    constraints: list[CommitmentConstraint] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    revisions: list[CommitmentRevision] = field(default_factory=list)
