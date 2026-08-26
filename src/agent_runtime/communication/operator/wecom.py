"""Enterprise WeCom is a mouth and ear. It never executes actions."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..mailbox import RuntimeMailbox
from .approval import ApprovalStateError, ApprovalStore
from .models import CHANNEL_WECOM, ApprovalRequest
from ._persist import atomic_json, read_json

logger = logging.getLogger(__name__)


class WeComTransport(Protocol):
    def send_text(self, userid: str, text: str) -> None: ...

    def send_card(self, userid: str, card: dict[str, Any]) -> None: ...


@dataclass
class FakeWeComClient:
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    sent_cards: list[dict[str, Any]] = field(default_factory=list)
    fail_send: bool = False

    def send_text(self, userid: str, text: str) -> None:
        if self.fail_send:
            raise ConnectionError("wecom unavailable")
        self.sent_messages.append({"userid": userid, "text": text})

    def send_card(self, userid: str, card: dict[str, Any]) -> None:
        if self.fail_send:
            raise ConnectionError("wecom unavailable")
        self.sent_cards.append({"userid": userid, "card": card})


class ConversationRoutes:
    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "operator" / "routes"
        self.root.mkdir(parents=True, exist_ok=True)

    def remember(self, request_id: str, *, userid: str, chatid: str | None = None) -> None:
        atomic_json(
            self.root / f"{request_id}.json",
            {"request_id": request_id, "userid": userid, "chatid": chatid},
        )

    def userid_for(self, request_id: str) -> str | None:
        path = self.root / f"{request_id}.json"
        try:
            value = read_json(path)
        except FileNotFoundError:
            return None
        userid = value.get("userid")
        return None if userid is None else str(userid)


class WeComOperatorGateway:
    def __init__(
        self,
        *,
        client: WeComTransport,
        mailbox: RuntimeMailbox,
        approvals: ApprovalStore,
        operator_ids: set[str],
        routes: ConversationRoutes,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.client = client
        self.mailbox = mailbox
        self.approvals = approvals
        self.operator_ids = frozenset(operator_ids)
        self.routes = routes
        self._secrets = secrets

    def is_allowed(self, userid: str) -> bool:
        return userid in self.operator_ids

    def partner_id(self, userid: str) -> str:
        return f"wecom_{userid}"

    def handle_inbound_text(
        self,
        *,
        userid: str,
        text: str,
        chattype: str = "single",
    ) -> str | None:
        if chattype == "group":
            logger.info("ignoring wecom group message from operator_id=%s", userid)
            return None
        if not self.is_allowed(userid):
            logger.info("ignoring wecom message from non-operator userid")
            return None
        request = self.mailbox.enqueue(text, partner_id=self.partner_id(userid))
        self.routes.remember(request.request_id, userid=userid)
        return request.request_id

    def handle_card_event(
        self,
        *,
        userid: str,
        event_key: str,
        chattype: str = "single",
    ) -> None:
        if chattype == "group":
            return
        if not self.is_allowed(userid):
            logger.info("ignoring wecom approval from non-operator userid")
            return
        action, _, approval_id = event_key.partition(":")
        if not approval_id or action not in {"approve", "deny"}:
            logger.info("ignoring opaque wecom card key without approval id")
            return
        try:
            if action == "approve":
                self.approvals.approve(
                    approval_id,
                    operator_id=userid,
                    channel=CHANNEL_WECOM,
                )
            else:
                self.approvals.deny(
                    approval_id,
                    operator_id=userid,
                    channel=CHANNEL_WECOM,
                )
        except (KeyError, ApprovalStateError) as exc:
            logger.info(
                "wecom approval callback ignored approval_id=%s error=%s",
                approval_id,
                type(exc).__name__,
            )

    def handle_frame(self, frame: dict[str, Any]) -> None:
        body = _body(frame)
        userid = _userid(body)
        if not userid:
            return
        chattype = str(body.get("chattype") or "single")
        msgtype = str(body.get("msgtype") or "")
        event_key = _event_key(body)
        if event_key:
            self.handle_card_event(userid=userid, event_key=event_key, chattype=chattype)
            return
        if msgtype == "text" or "text" in body:
            content = str((body.get("text") or {}).get("content") or "").strip()
            if content:
                self.handle_inbound_text(userid=userid, text=content, chattype=chattype)

    def send_text(self, userid: str, text: str) -> None:
        self.client.send_text(userid, text)

    def send_approval_card(self, userid: str, request: ApprovalRequest) -> None:
        self.client.send_card(userid, build_approval_card(request))

    def userid_from_partner(self, partner_id: str) -> str | None:
        if partner_id.startswith("wecom_"):
            userid = partner_id[len("wecom_") :]
            if userid:
                return userid
        return None


class OfficialWeComClient:
    """Thin wrapper around wecom-aibot-python-sdk. Protocol stays in the SDK."""

    def __init__(self, *, bot_id: str, secret: str) -> None:
        try:
            from aibot import WSClient, WSClientOptions
        except ImportError as exc:
            raise RuntimeError(
                "operator extra missing; pip install -e '.[operator]'"
            ) from exc
        self._client = WSClient(
            WSClientOptions(
                bot_id=bot_id,
                secret=secret,
                max_reconnect_attempts=-1,
                logger=_SafeLogger(),
            )
        )

    def bind(self, gateway: WeComOperatorGateway) -> None:
        @self._client.on("message.text")
        async def on_text(frame: dict[str, Any]) -> None:
            gateway.handle_frame(frame)

        @self._client.on("event.template_card_event")
        async def on_card(frame: dict[str, Any]) -> None:
            gateway.handle_frame(frame)

    def run(self) -> None:
        self._client.run()

    def send_text(self, userid: str, text: str) -> None:
        # aibot_send_msg only accepts markdown/template_card; plain text gets
        # errcode 40008 from the gateway, so text replies ride the markdown type.
        self._submit(
            self._client.send_message(
                userid,
                {"msgtype": "markdown", "markdown": {"content": text}},
            )
        )

    def send_card(self, userid: str, card: dict[str, Any]) -> None:
        self._submit(
            self._client.send_message(
                userid,
                {"msgtype": "template_card", "template_card": card},
            )
        )

    def _submit(self, coro: Any) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.result(timeout=15)


def build_approval_card(request: ApprovalRequest) -> dict[str, Any]:
    snapshot = request.action_snapshot or {}
    payload = dict(snapshot.get("payload") or {})
    target = str(payload.get("target") or snapshot.get("resource") or "")
    risk = str(payload.get("risk") or "受治理策略约束，需人工确认后才会执行。")
    return {
        "card_type": "button_interaction",
        "source": {"desc": "Agent Runtime"},
        "main_title": {
            "title": "⚠️ 请求授权",
            "desc": request.agent_id,
        },
        "sub_title_text": f"操作 {request.action_kind}",
        "horizontal_content_list": [
            {"keyname": "原因", "value": request.reason[:200]},
            {"keyname": "目标", "value": (target or "未指定")[:200]},
            {"keyname": "风险", "value": risk[:200]},
            {"keyname": "有效期", "value": request.expires_at},
        ],
        "button_list": [
            {"text": "批准", "style": 1, "key": f"approve:{request.approval_id}"},
            {"text": "拒绝", "style": 2, "key": f"deny:{request.approval_id}"},
        ],
        "task_id": request.approval_id,
    }


def _body(frame: dict[str, Any]) -> dict[str, Any]:
    body = frame.get("body")
    return body if isinstance(body, dict) else frame


def _userid(body: dict[str, Any]) -> str | None:
    from_ = body.get("from")
    if isinstance(from_, dict) and from_.get("userid"):
        return str(from_["userid"])
    if body.get("userid"):
        return str(body["userid"])
    return None


def _event_key(body: dict[str, Any]) -> str:
    event = body.get("event")
    if isinstance(event, dict):
        for key in ("event_key", "key"):
            if event.get(key):
                return str(event[key])
        keys = event.get("keys")
        if isinstance(keys, dict) and keys.get("key"):
            return str(keys["key"])
        # Real aibot gateway shape: event.template_card_event.event_key
        template = event.get("template_card_event")
        if isinstance(template, dict) and template.get("event_key"):
            return str(template["event_key"])
    template = body.get("template_card_event")
    if isinstance(template, dict) and template.get("event_key"):
        return str(template["event_key"])
    if body.get("event_key"):
        return str(body["event_key"])
    return ""


class _SafeLogger:
    def debug(self, message: str, *args: Any) -> None:
        logger.debug(_safe_log(message, args))

    def info(self, message: str, *args: Any) -> None:
        logger.info(_safe_log(message, args))

    def warn(self, message: str, *args: Any) -> None:
        logger.warning(_safe_log(message, args))

    def error(self, message: str, *args: Any) -> None:
        logger.error(_safe_log(message, args))


def _safe_log(message: str, args: tuple[Any, ...]) -> str:
    text = message % args if args else message
    lowered = text.lower()
    if "secret" in lowered or "token" in lowered or "access_token" in lowered:
        return "[redacted operator log line]"
    return text
