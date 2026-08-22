"""Consent grants. An agent may only mint or revoke its own consent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class ConsentAgencyError(ValueError):
    """Raised when an agent tries to create or revoke another agent's consent."""


@dataclass
class ConsentGrant:
    grant_id: str
    grantor_id: str
    grantee_id: str
    scope: str
    actor_id: str
    revoked: bool = False


@dataclass
class ConsentRevocation:
    revocation_id: str
    grant_id: str
    actor_id: str
    reason: str = ""
    timestamp: datetime | None = None
