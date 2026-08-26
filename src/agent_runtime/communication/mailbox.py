"""Atomic filesystem mailbox shared by the runner and local chat clients."""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PARTNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")


@dataclass(frozen=True)
class MailboxRequest:
    request_id: str
    created_at: str
    partner_id: str
    message: str
    challenge: bool = False
    max_tokens: int = 1200


@dataclass(frozen=True)
class MailboxResponse:
    request_id: str
    status: str
    agent_id: str
    partner_id: str
    message: str
    created_at: str
    responded_at: str
    cycle: int
    response: str | None = None
    error: str | None = None
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorNotification:
    notification_id: str
    created_at: str
    agent_id: str
    cycle: int
    kind: str
    title: str
    message: str
    evidence_refs: list[str] = field(default_factory=list)


class RuntimeMailbox:
    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "mailbox"
        self.inbox = self.root / "inbox"
        self.processing = self.root / "processing"
        self.outbox = self.root / "outbox"
        self.notifications = self.root / "notifications"
        for path in (self.inbox, self.processing, self.outbox, self.notifications):
            path.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        message: str,
        *,
        partner_id: str = "operator",
        challenge: bool = False,
        max_tokens: int = 1200,
    ) -> MailboxRequest:
        text = str(message).strip()
        if not text:
            raise ValueError("message cannot be empty")
        if len(text) > 20_000:
            raise ValueError("message exceeds 20000 characters")
        if not _PARTNER_RE.fullmatch(partner_id):
            raise ValueError("partner_id contains unsupported characters")
        if not 64 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 64 and 4096")
        request = MailboxRequest(
            request_id=f"msg_{uuid.uuid4().hex[:16]}",
            created_at=_utc_now(),
            partner_id=partner_id,
            message=text,
            challenge=bool(challenge),
            max_tokens=max_tokens,
        )
        _atomic_json(self.inbox / f"{request.request_id}.json", asdict(request))
        return request

    def pending_count(self) -> int:
        return sum(1 for _ in self.inbox.glob("msg_*.json"))

    def has_pending(self) -> bool:
        return next(self.inbox.glob("msg_*.json"), None) is not None

    def claim_next(self) -> MailboxRequest | None:
        for source in sorted(self.inbox.glob("msg_*.json")):
            target = self.processing / source.name
            try:
                source.replace(target)
            except FileNotFoundError:
                continue
            try:
                return _request_from_json(_read_json(target))
            except Exception as exc:
                invalid = MailboxResponse(
                    request_id=source.stem,
                    status="ERROR",
                    agent_id="unknown",
                    partner_id="unknown",
                    message="",
                    created_at=_utc_now(),
                    responded_at=_utc_now(),
                    cycle=0,
                    error=f"invalid mailbox request: {type(exc).__name__}",
                )
                _atomic_json(self.outbox / source.name, asdict(invalid))
                target.unlink(missing_ok=True)
        return None

    def complete(
        self,
        request: MailboxRequest,
        *,
        agent_id: str,
        cycle: int,
        response: str | None = None,
        error: str | None = None,
        attachments: Iterable[str] = (),
    ) -> MailboxResponse:
        result = MailboxResponse(
            request_id=request.request_id,
            status="ERROR" if error else "COMPLETED",
            agent_id=agent_id,
            partner_id=request.partner_id,
            message=request.message,
            created_at=request.created_at,
            responded_at=_utc_now(),
            cycle=cycle,
            response=response,
            error=error,
            attachments=tuple(str(item) for item in attachments),
        )
        _atomic_json(self.outbox / f"{request.request_id}.json", asdict(result))
        (self.processing / f"{request.request_id}.json").unlink(missing_ok=True)
        return result

    def response(self, request_id: str) -> MailboxResponse | None:
        if not re.fullmatch(r"msg_[a-f0-9]{16}", request_id):
            return None
        path = self.outbox / f"{request_id}.json"
        try:
            return _response_from_json(_read_json(path))
        except FileNotFoundError:
            return None

    def recent_responses(self, *, limit: int = 100) -> list[MailboxResponse]:
        if limit <= 0:
            return []
        found: list[MailboxResponse] = []
        for path in sorted(self.outbox.glob("msg_*.json"), reverse=True):
            try:
                found.append(_response_from_json(_read_json(path)))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            if len(found) >= limit:
                break
        return sorted(found, key=lambda item: item.responded_at)

    def publish_notification(
        self,
        *,
        agent_id: str,
        cycle: int,
        kind: str,
        title: str,
        message: str,
        evidence_refs: list[str] | None = None,
    ) -> OperatorNotification:
        notification = OperatorNotification(
            notification_id=f"note_{uuid.uuid4().hex[:16]}",
            created_at=_utc_now(),
            agent_id=agent_id,
            cycle=cycle,
            kind=str(kind)[:80],
            title=str(title).strip()[:160],
            message=str(message).strip()[:4000],
            evidence_refs=list(evidence_refs or [])[:32],
        )
        _atomic_json(
            self.notifications / f"{notification.notification_id}.json",
            asdict(notification),
        )
        return notification

    def recent_notifications(self, *, limit: int = 50) -> list[OperatorNotification]:
        found: list[OperatorNotification] = []
        for path in sorted(self.notifications.glob("note_*.json"), reverse=True):
            try:
                found.append(OperatorNotification(**_read_json(path)))
            except (OSError, TypeError, json.JSONDecodeError):
                continue
            if len(found) >= limit:
                break
        return sorted(found, key=lambda item: item.created_at)


def _request_from_json(value: dict[str, Any]) -> MailboxRequest:
    return MailboxRequest(
        request_id=str(value["request_id"]),
        created_at=str(value["created_at"]),
        partner_id=str(value["partner_id"]),
        message=str(value["message"]),
        challenge=bool(value.get("challenge", False)),
        max_tokens=int(value.get("max_tokens", 1200)),
    )


def _response_from_json(value: dict[str, Any]) -> MailboxResponse:
    return MailboxResponse(
        request_id=str(value["request_id"]),
        status=str(value["status"]),
        agent_id=str(value["agent_id"]),
        partner_id=str(value["partner_id"]),
        message=str(value["message"]),
        created_at=str(value["created_at"]),
        responded_at=str(value["responded_at"]),
        cycle=int(value["cycle"]),
        response=None if value.get("response") is None else str(value["response"]),
        error=None if value.get("error") is None else str(value["error"]),
        attachments=tuple(
            str(item) for item in (value.get("attachments") or [])
        ),
    )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("mailbox record must be an object")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
