"""Commitments. Public statements are exportable; private intent is not."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class Commitment:
    commitment_id: str
    counterpart_id: str
    statement: str
    visibility: CommitmentVisibility
    state: CommitmentState
    intent: str | None = None
    deadline: Deadline | None = None
