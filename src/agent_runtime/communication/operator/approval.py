"""Durable trusted-authority approval. Channel adapters never execute actions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from ...action.proposal import ActionProposal
from ...clock import Clock, SystemClock, new_id
from .models import (
    APPROVAL_APPROVED,
    APPROVAL_CONSUMED,
    APPROVAL_DENIED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    ApprovalDecision,
    ApprovalRequest,
)
from ._persist import atomic_json, parse_iso, read_json, utc_now

_DIGEST_SKIP_PAYLOAD_KEYS = frozenset({"approval_id"})
_TERMINAL = frozenset(
    {APPROVAL_APPROVED, APPROVAL_DENIED, APPROVAL_EXPIRED, APPROVAL_CONSUMED}
)


class ApprovalStateError(RuntimeError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"approval cannot transition from {status}")


class ApprovalStore:
    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Clock | None = None,
        ttl_seconds: int = 1800,
    ) -> None:
        self.root = Path(state_dir) / "operator" / "approvals"
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.ttl_seconds = ttl_seconds

    def create_pending(
        self,
        *,
        agent_id: str,
        cycle: int,
        proposal: ActionProposal,
        summary: str | None = None,
        ttl_seconds: int | None = None,
    ) -> ApprovalRequest:
        self.expire_due()
        digest = compute_action_digest(proposal)
        existing = self.find_open(digest)
        if existing is not None:
            return existing
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        now = self.clock.now()
        request = ApprovalRequest(
            approval_id=new_id("apr"),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl)).isoformat(),
            agent_id=agent_id,
            cycle=cycle,
            action_kind=proposal.kind,
            summary=summary or proposal.reason,
            reason=proposal.reason,
            action_digest=digest,
            evidence_refs=list(proposal.evidence)[:32],
            status=APPROVAL_PENDING,
            action_snapshot=_snapshot(proposal),
        )
        self._write(request)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        path = self.root / f"{approval_id}.json"
        try:
            return _request_from_json(read_json(path))
        except FileNotFoundError:
            return None

    def records(self) -> list[ApprovalRequest]:
        return self._all()

    def pending(self) -> list[ApprovalRequest]:
        self.expire_due()
        return [item for item in self._all() if item.status == APPROVAL_PENDING]

    def approved_unconsumed(self) -> list[ApprovalRequest]:
        self.expire_due()
        return [item for item in self._all() if item.status == APPROVAL_APPROVED]

    def find_open(self, digest: str) -> ApprovalRequest | None:
        for item in self._all():
            if item.action_digest == digest and item.status in {
                APPROVAL_PENDING,
                APPROVAL_APPROVED,
            }:
                return item
        return None

    def find_approved(self, digest: str) -> ApprovalRequest | None:
        self.expire_due()
        for item in self._all():
            if item.action_digest == digest and item.status == APPROVAL_APPROVED:
                return item
        return None

    def approve(
        self,
        approval_id: str,
        *,
        operator_id: str,
        channel: str,
    ) -> ApprovalDecision:
        record = self._require(approval_id)
        self.expire_due()
        record = self._require(approval_id)
        if record.status == APPROVAL_APPROVED:
            return _decision_from_record(record)
        if record.status != APPROVAL_PENDING:
            raise ApprovalStateError(record.status)
        now = _stamp(self.clock)
        updated = _replace(
            record,
            status=APPROVAL_APPROVED,
            decided_at=now,
            operator_id=operator_id,
            channel=channel,
            decision=APPROVAL_APPROVED,
        )
        self._write(updated)
        return _decision_from_record(updated)

    def approve_by_authority(
        self,
        approval_id: str,
        *,
        authority_id: str,
        channel: str,
    ) -> ApprovalDecision:
        """Approve through any trusted authority identity, human or agent."""
        return self.approve(
            approval_id,
            operator_id=authority_id,
            channel=channel,
        )

    def deny_by_authority(
        self,
        approval_id: str,
        *,
        authority_id: str,
        channel: str,
    ) -> ApprovalDecision:
        """Deny through any trusted authority identity, human or agent."""
        return self.deny(
            approval_id,
            operator_id=authority_id,
            channel=channel,
        )

    def deny(
        self,
        approval_id: str,
        *,
        operator_id: str,
        channel: str,
    ) -> ApprovalDecision:
        record = self._require(approval_id)
        self.expire_due()
        record = self._require(approval_id)
        if record.status == APPROVAL_DENIED:
            return _decision_from_record(record)
        if record.status != APPROVAL_PENDING:
            raise ApprovalStateError(record.status)
        now = _stamp(self.clock)
        updated = _replace(
            record,
            status=APPROVAL_DENIED,
            decided_at=now,
            operator_id=operator_id,
            channel=channel,
            decision=APPROVAL_DENIED,
        )
        self._write(updated)
        return _decision_from_record(updated)

    def consume(self, approval_id: str) -> ApprovalRequest:
        record = self._require(approval_id)
        if record.status == APPROVAL_CONSUMED:
            return record
        if record.status != APPROVAL_APPROVED:
            raise ApprovalStateError(record.status)
        updated = _replace(
            record,
            status=APPROVAL_CONSUMED,
            consumed_at=_stamp(self.clock),
        )
        self._write(updated)
        return updated

    def expire_due(self) -> list[ApprovalRequest]:
        now = self.clock.now()
        expired: list[ApprovalRequest] = []
        for item in self._all():
            if item.status != APPROVAL_PENDING:
                continue
            try:
                due = parse_iso(item.expires_at)
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            current = now if now.tzinfo is not None else now.replace(tzinfo=due.tzinfo)
            if due.tzinfo is None or current.tzinfo is None:
                aware_due = due.replace(tzinfo=None)
                aware_now = current.replace(tzinfo=None)
            else:
                aware_due = due
                aware_now = current
            if aware_now < aware_due:
                continue
            updated = _replace(item, status=APPROVAL_EXPIRED)
            self._write(updated)
            expired.append(updated)
        return expired

    def _require(self, approval_id: str) -> ApprovalRequest:
        record = self.get(approval_id)
        if record is None:
            raise KeyError(approval_id)
        return record

    def _all(self) -> list[ApprovalRequest]:
        found: list[ApprovalRequest] = []
        for path in sorted(self.root.glob("apr_*.json")):
            try:
                found.append(_request_from_json(read_json(path)))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return found

    def _write(self, record: ApprovalRequest) -> None:
        atomic_json(self.root / f"{record.approval_id}.json", asdict(record))


def compute_action_digest(proposal: ActionProposal) -> str:
    payload = {
        key: value
        for key, value in dict(proposal.payload).items()
        if key not in _DIGEST_SKIP_PAYLOAD_KEYS
    }
    canonical = {
        "action_kind": proposal.kind,
        "target": payload.get("target"),
        "parameters": payload.get("parameters", payload),
        "resource": proposal.resource,
        "deployment_target": payload.get("deployment_target"),
        "cost": {
            "compute": proposal.compute_cost,
            "financial": proposal.financial_cost,
        },
        "security_context": {
            "trigger": proposal.trigger,
            "goal_origin": proposal.goal_origin,
        },
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proposal_from_snapshot(snapshot: dict[str, Any]) -> ActionProposal:
    data = dict(snapshot)
    risk = data.get("risk_domains") or []
    data["risk_domains"] = set(risk)
    known = {name: data[name] for name in ActionProposal.__dataclass_fields__ if name in data}
    return ActionProposal(**known)


def _snapshot(proposal: ActionProposal) -> dict[str, Any]:
    return {
        "kind": proposal.kind,
        "resource": proposal.resource,
        "compute_cost": proposal.compute_cost,
        "financial_cost": proposal.financial_cost,
        "goal_id": proposal.goal_id,
        "reason": proposal.reason,
        "payload": dict(proposal.payload),
        "trigger": proposal.trigger,
        "goal_origin": proposal.goal_origin,
        "evidence": list(proposal.evidence),
        "risk_domains": sorted(proposal.risk_domains),
        "action_id": proposal.action_id,
    }


def _request_from_json(value: dict[str, Any]) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=str(value["approval_id"]),
        created_at=str(value["created_at"]),
        expires_at=str(value["expires_at"]),
        agent_id=str(value["agent_id"]),
        cycle=int(value["cycle"]),
        action_kind=str(value["action_kind"]),
        summary=str(value["summary"]),
        reason=str(value["reason"]),
        action_digest=str(value["action_digest"]),
        evidence_refs=[str(item) for item in value.get("evidence_refs") or []],
        status=str(value.get("status") or APPROVAL_PENDING),
        action_snapshot=dict(value.get("action_snapshot") or {}),
        consumed_at=(
            None if value.get("consumed_at") is None else str(value["consumed_at"])
        ),
        decided_at=None if value.get("decided_at") is None else str(value["decided_at"]),
        operator_id=None if value.get("operator_id") is None else str(value["operator_id"]),
        channel=None if value.get("channel") is None else str(value["channel"]),
        decision=None if value.get("decision") is None else str(value["decision"]),
        last_error=None if value.get("last_error") is None else str(value["last_error"]),
    )


def _decision_from_record(record: ApprovalRequest) -> ApprovalDecision:
    if record.decided_at is None or record.operator_id is None or record.channel is None:
        raise ApprovalStateError(record.status)
    return ApprovalDecision(
        approval_id=record.approval_id,
        decided_at=record.decided_at,
        decision=record.decision or record.status,
        operator_id=record.operator_id,
        channel=record.channel,
        action_digest=record.action_digest,
    )


def _replace(record: ApprovalRequest, **changes: Any) -> ApprovalRequest:
    data = asdict(record)
    data.update(changes)
    return ApprovalRequest(**data)


def _stamp(clock: Clock) -> str:
    try:
        return clock.now().isoformat()
    except Exception:
        return utc_now()
