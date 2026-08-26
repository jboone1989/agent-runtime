"""Notification fanout. Runtime code talks only to NotificationService.notify()."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..mailbox import OperatorNotification
from .models import CHANNEL_APPRISE, DeliveryReceipt, SOURCE_NOTIFICATION
from ._persist import redact_error


class NotificationTransport(Protocol):
    def notify(self, *, title: str, body: str) -> bool: ...


class NotificationService(Protocol):
    def notify(self, notification: OperatorNotification) -> DeliveryReceipt: ...


class AppriseNotificationService:
    """Sends OperatorNotification through Apprise. Never used for approval."""

    def __init__(
        self,
        urls: Sequence[str] = (),
        *,
        transport: NotificationTransport | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._urls = tuple(url for url in urls if url)
        self._transport = transport
        self._secrets = secrets

    def notify(self, notification: OperatorNotification) -> DeliveryReceipt:
        title = f"[{notification.agent_id}] {notification.title}".strip()
        body = notification.message
        try:
            transport = self._transport or _AppriseTransport(self._urls)
            ok = bool(transport.notify(title=title, body=body))
            error = None if ok else "apprise transport returned false"
        except Exception as exc:
            ok = False
            error = redact_error(exc, self._secrets)
        return DeliveryReceipt(
            delivery_id="",
            source_type=SOURCE_NOTIFICATION,
            source_id=notification.notification_id,
            channel=CHANNEL_APPRISE,
            status="DELIVERED" if ok else "FAILED",
            attempts=1,
            last_error=error,
        )


class _AppriseTransport:
    def __init__(self, urls: Sequence[str]) -> None:
        if not urls:
            raise RuntimeError("apprise urls are not configured")
        try:
            import apprise
        except ImportError as exc:
            raise RuntimeError(
                "operator extra missing; pip install -e '.[operator]'"
            ) from exc
        app = apprise.Apprise()
        for url in urls:
            if not app.add(url):
                raise RuntimeError("apprise rejected a notify url")
        self._app = app

    def notify(self, *, title: str, body: str) -> bool:
        return bool(self._app.notify(title=title, body=body))
