"""Operator contact records. Approval is a security boundary, not a notification kind."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DELIVERY_PENDING = "PENDING"
DELIVERY_FAILED = "FAILED"
DELIVERY_RETRYING = "RETRYING"
DELIVERY_DELIVERED = "DELIVERED"

APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_DENIED = "DENIED"
APPROVAL_EXPIRED = "EXPIRED"
APPROVAL_CONSUMED = "CONSUMED"

SOURCE_NOTIFICATION = "notification"
SOURCE_MAILBOX_RESPONSE = "mailbox_response"
SOURCE_APPROVAL_REQUEST = "approval_request"
SOURCE_APPROVAL_RESULT = "approval_result"

CHANNEL_APPRISE = "apprise"
CHANNEL_WECOM = "wecom"


@dataclass(frozen=True)
class OperatorIdentity:
    operator_id: str
    channel: str
    userid: str


@dataclass(frozen=True)
class DeliveryReceipt:
    delivery_id: str
    source_type: str
    source_id: str
    channel: str
    status: str
    attempts: int
    last_error: str | None = None


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    source_type: str
    source_id: str
    channel: str
    status: str
    attempts: int
    created_at: str
    last_attempt_at: str | None = None
    delivered_at: str | None = None
    last_error: str | None = None

    def receipt(self) -> DeliveryReceipt:
        return DeliveryReceipt(
            delivery_id=self.delivery_id,
            source_type=self.source_type,
            source_id=self.source_id,
            channel=self.channel,
            status=self.status,
            attempts=self.attempts,
            last_error=self.last_error,
        )


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    created_at: str
    expires_at: str
    agent_id: str
    cycle: int
    action_kind: str
    summary: str
    reason: str
    action_digest: str
    evidence_refs: list[str] = field(default_factory=list)
    status: str = APPROVAL_PENDING
    action_snapshot: dict[str, Any] = field(default_factory=dict)
    consumed_at: str | None = None
    decided_at: str | None = None
    operator_id: str | None = None
    channel: str | None = None
    decision: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    decided_at: str
    decision: str
    operator_id: str
    channel: str
    action_digest: str
