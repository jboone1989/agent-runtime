"""Independent operator process: WeCom + Apprise around the Runtime mailbox."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...clock import Clock, SystemClock
from ..mailbox import MailboxResponse, OperatorNotification, RuntimeMailbox
from .approval import ApprovalStore
from .delivery import DeliveryStore
from .models import (
    CHANNEL_APPRISE,
    CHANNEL_WECOM,
    DELIVERY_DELIVERED,
    SOURCE_APPROVAL_REQUEST,
    SOURCE_APPROVAL_RESULT,
    SOURCE_MAILBOX_RESPONSE,
    SOURCE_NOTIFICATION,
    ApprovalRequest,
    DeliveryRecord,
)
from .notification import AppriseNotificationService, NotificationService
from .wecom import (
    ConversationRoutes,
    OfficialWeComClient,
    WeComOperatorGateway,
    WeComTransport,
)
from ._persist import redact_error

logger = logging.getLogger(__name__)

CRITICAL_NOTIFICATION_KINDS = frozenset(
    {"security_warning", "runtime_failed", "runtime_degraded"}
)


class BurstGuard:
    def __init__(self, *, limit: int = 8, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = timedelta(seconds=window_seconds)
        self._seen: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, agent_id: str, *, kind: str, now: datetime) -> bool:
        if kind in CRITICAL_NOTIFICATION_KINDS:
            return True
        bucket = self._seen[agent_id]
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


class OperatorService:
    def __init__(
        self,
        state_dir: Path,
        *,
        wecom_client: WeComTransport,
        notification_service: NotificationService,
        operator_ids: Iterable[str],
        clock: Clock | None = None,
        burst_limit: int = 8,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.state_dir = Path(state_dir)
        self.clock = clock or SystemClock()
        self.mailbox = RuntimeMailbox(self.state_dir)
        self.deliveries = DeliveryStore(self.state_dir, clock=self.clock)
        self.approvals = ApprovalStore(self.state_dir, clock=self.clock)
        self.routes = ConversationRoutes(self.state_dir)
        self.operator_ids = tuple(dict.fromkeys(str(item) for item in operator_ids if item))
        self.notification_service = notification_service
        self.secrets = secrets
        self.burst = BurstGuard(limit=burst_limit)
        self.wecom = WeComOperatorGateway(
            client=wecom_client,
            mailbox=self.mailbox,
            approvals=self.approvals,
            operator_ids=set(self.operator_ids),
            routes=self.routes,
            secrets=secrets,
        )
        self.status = "RUNNING"

    def tick(self) -> None:
        self.approvals.expire_due()
        self._deliver_notifications()
        self._deliver_mailbox_responses()
        self._deliver_approval_requests()
        self._deliver_approval_results()

    def handle_inbound_text(self, *, userid: str, text: str, chattype: str = "single") -> str | None:
        return self.wecom.handle_inbound_text(userid=userid, text=text, chattype=chattype)

    def handle_card_event(self, *, userid: str, event_key: str, chattype: str = "single") -> None:
        self.wecom.handle_card_event(userid=userid, event_key=event_key, chattype=chattype)

    def _deliver_notifications(self) -> None:
        for notification in self.mailbox.recent_notifications(limit=200):
            self._deliver_one(
                source_type=SOURCE_NOTIFICATION,
                source_id=notification.notification_id,
                channel=CHANNEL_APPRISE,
                send=lambda item=notification: self._send_notification(item),
                burst_key=f"{CHANNEL_APPRISE}:{notification.agent_id}",
                burst_kind=notification.kind,
            )
            self._deliver_one(
                source_type=SOURCE_NOTIFICATION,
                source_id=notification.notification_id,
                channel=CHANNEL_WECOM,
                send=lambda item=notification: self._send_wecom_notification(item),
                burst_key=f"{CHANNEL_WECOM}:{notification.agent_id}",
                burst_kind=notification.kind,
            )

    def _send_wecom_notification(self, notification: OperatorNotification) -> None:
        if not self.operator_ids:
            raise RuntimeError("no wecom operators configured")
        text = f"**{notification.title}**\n{notification.message}"
        for userid in self.operator_ids:
            self.wecom.send_text(userid, text)

    def _send_notification(self, notification: OperatorNotification) -> None:
        receipt = self.notification_service.notify(notification)
        if receipt.status != DELIVERY_DELIVERED:
            raise RuntimeError(receipt.last_error or "notification delivery failed")

    def _deliver_mailbox_responses(self) -> None:
        for response in self.mailbox.recent_responses(limit=200):
            self._deliver_one(
                source_type=SOURCE_MAILBOX_RESPONSE,
                source_id=response.request_id,
                channel=CHANNEL_WECOM,
                send=lambda item=response: self._send_mailbox_response(item),
            )

    def _send_mailbox_response(self, response: MailboxResponse) -> None:
        userid = self.routes.userid_for(response.request_id) or self.wecom.userid_from_partner(
            response.partner_id
        )
        if userid is None or not self.wecom.is_allowed(userid):
            raise RuntimeError("mailbox response has no allowed wecom recipient")
        text = (response.response or response.error or "").strip()
        if not text:
            text = f"[{response.status}] empty operator reply"
        self.wecom.send_text(userid, text)
        for path in response.attachments:
            media_id = self.wecom.upload_image(path)
            self.wecom.send_image(userid, media_id)

    def _deliver_approval_requests(self) -> None:
        for request in self.approvals.pending():
            self._deliver_one(
                source_type=SOURCE_APPROVAL_REQUEST,
                source_id=request.approval_id,
                channel=CHANNEL_WECOM,
                send=lambda item=request: self._send_approval_cards(item),
            )

    def _send_approval_cards(self, request: ApprovalRequest) -> None:
        if not self.operator_ids:
            raise RuntimeError("no wecom operators configured")
        for userid in self.operator_ids:
            self.wecom.send_approval_card(userid, request)

    def _deliver_approval_results(self) -> None:
        for record in self.approvals.approved_unconsumed():
            self._deliver_result(record, "已记录批准，等待 Runtime 按原执行路径核验后执行。")
        for record in self.approvals.records():
            if record.status == "DENIED":
                self._deliver_result(record, "已拒绝该授权请求，受保护动作保持 blocked。")

    def _deliver_result(self, request: ApprovalRequest, message: str) -> None:
        self._deliver_one(
            source_type=SOURCE_APPROVAL_RESULT,
            source_id=f"{request.approval_id}:{request.status}",
            channel=CHANNEL_WECOM,
            send=lambda: self._broadcast_text(
                f"[{request.agent_id}]\n{message}\n操作 {request.action_kind}"
            ),
        )

    def _broadcast_text(self, text: str) -> None:
        if not self.operator_ids:
            raise RuntimeError("no wecom operators configured")
        for userid in self.operator_ids:
            self.wecom.send_text(userid, text)

    def _deliver_one(
        self,
        *,
        source_type: str,
        source_id: str,
        channel: str,
        send,
        burst_key: str | None = None,
        burst_kind: str | None = None,
    ) -> DeliveryRecord:
        record = self.deliveries.get_or_create(
            source_type=source_type,
            source_id=source_id,
            channel=channel,
        )
        if record.status == DELIVERY_DELIVERED:
            return record
        if burst_key is not None and burst_kind is not None:
            if not self.burst.allow(burst_key, kind=burst_kind, now=self.clock.now()):
                logger.info(
                    "burst guard delayed delivery_id=%s agent_id=%s",
                    record.delivery_id,
                    burst_key,
                )
                return record
        attempted = self.deliveries.begin_attempt(record)
        try:
            send()
        except Exception as exc:
            self.status = "DEGRADED"
            error = redact_error(exc, self.secrets)
            logger.info(
                "delivery failed delivery_id=%s source_type=%s source_id=%s error=%s",
                attempted.delivery_id,
                source_type,
                source_id,
                error,
            )
            return self.deliveries.record_result(attempted, ok=False, error=error)
        return self.deliveries.record_result(attempted, ok=True)


def load_operator_config(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent-runtime-operator",
        description="Deliver Agent Runtime operator contact over WeCom and Apprise.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("AGENT_RUNTIME_STATE_DIR", "state")),
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=1.0,
        help="Delay between delivery ticks while the WeCom client stays connected.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = load_operator_config(argv)
    bot_id = os.environ.get("AGENT_RUNTIME_WECOM_BOT_ID") or os.environ.get(
        "AGENT_RUNTIME_WECOM_AGENT_ID", ""
    )
    secret = os.environ.get("AGENT_RUNTIME_WECOM_SECRET", "")
    operator_ids = _csv(os.environ.get("AGENT_RUNTIME_WECOM_OPERATOR_IDS", ""))
    apprise_urls = _csv(os.environ.get("AGENT_RUNTIME_APPRISE_URLS", ""))
    if not bot_id or not secret or not operator_ids:
        raise SystemExit(
            "AGENT_RUNTIME_WECOM_BOT_ID, AGENT_RUNTIME_WECOM_SECRET, "
            "and AGENT_RUNTIME_WECOM_OPERATOR_IDS are required"
        )
    secrets = tuple(item for item in (secret, bot_id, *apprise_urls) if item)
    client = OfficialWeComClient(bot_id=bot_id, secret=secret)
    service = OperatorService(
        args.state_dir.resolve(),
        wecom_client=client,
        notification_service=AppriseNotificationService(apprise_urls, secrets=secrets),
        operator_ids=operator_ids,
        secrets=secrets,
    )
    client.bind(service.wecom)
    stop = threading.Event()

    def poll_loop() -> None:
        while not stop.is_set():
            try:
                service.tick()
            except Exception as exc:
                logger.info("operator tick failed error=%s", redact_error(exc, secrets))
                service.status = "DEGRADED"
            stop.wait(max(0.2, float(args.idle_seconds)))

    thread = threading.Thread(target=poll_loop, name="operator-tick", daemon=True)
    thread.start()
    try:
        logger.info("operator service connecting wecom bot_id=%s", _public_id(bot_id))
        client.run()
    finally:
        stop.set()
    return 0


def _csv(value: str) -> tuple[str, ...]:
    parts = [item.strip() for item in value.replace(";", ",").replace(" ", ",").split(",")]
    return tuple(item for item in parts if item)


def _public_id(value: str) -> str:
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-2:]}"


if __name__ == "__main__":
    raise SystemExit(main())
