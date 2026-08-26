"""Operator contact: WeCom is transport, mailbox/governance remain the source of truth."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.clock import FrozenClock
from agent_runtime.communication.mailbox import RuntimeMailbox
from agent_runtime.communication.operator.approval import (
    ApprovalStateError,
    ApprovalStore,
    compute_action_digest,
)
from agent_runtime.communication.operator.models import (
    CHANNEL_APPRISE,
    CHANNEL_WECOM,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    SOURCE_NOTIFICATION,
)
from agent_runtime.communication.operator.notification import AppriseNotificationService
from agent_runtime.communication.operator.service import OperatorService
from agent_runtime.communication.operator.wecom import FakeWeComClient
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import ContinuousRunner


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class FakeApprise:
    def __init__(self, *, fail: bool = False, return_false: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail
        self.return_false = return_false

    def notify(self, *, title: str, body: str) -> bool:
        if self.fail:
            raise ConnectionError("apprise unavailable")
        if self.return_false:
            return False
        self.calls.append({"title": title, "body": body})
        return True


class ReplyProvider:
    async def complete(self, prompt: str, **kwargs) -> str:
        _ = (prompt, kwargs)
        return "我正在整理当前长期记忆，并等待你的下一句。"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def _service(
    tmp_path,
    *,
    clock: FrozenClock | None = None,
    apprise: FakeApprise | None = None,
    wecom: FakeWeComClient | None = None,
    operator_ids: tuple[str, ...] = ("zhangsan",),
) -> tuple[OperatorService, FakeApprise, FakeWeComClient]:
    apprise = apprise or FakeApprise()
    wecom = wecom or FakeWeComClient()
    service = OperatorService(
        tmp_path,
        wecom_client=wecom,
        notification_service=AppriseNotificationService(transport=apprise),
        operator_ids=operator_ids,
        clock=clock or FrozenClock(NOW),
    )
    return service, apprise, wecom


def _proposal(**overrides) -> ActionProposal:
    data = dict(
        kind="evolution_activate",
        resource="self",
        compute_cost=0.1,
        financial_cost=0.0,
        goal_id=None,
        reason="发现一个候选市场异常，需要进一步验证。",
        payload={"target": "OKX BTC options", "risk": "会访问外部交易 API，但不会提交真实订单。"},
        trigger="INTERNAL",
    )
    data.update(overrides)
    return ActionProposal(**data)


def _autonomy_with_approval() -> AutonomyPolicy:
    autonomy = AutonomyPolicy.default()
    autonomy.allowed_actions.add("evolution_activate")
    autonomy.require_human_approval.add("evolution_activate")
    return autonomy


def test_operator_notification_is_durable(tmp_path) -> None:
    mailbox = RuntimeMailbox(tmp_path)
    note = mailbox.publish_notification(
        agent_id="agent_one",
        cycle=3,
        kind="agent_initiative",
        title="Agent 想主动联系你",
        message="我发现当前权限边界有一个需要你确认的问题。",
    )
    restored = RuntimeMailbox(tmp_path).recent_notifications()
    assert restored == [note]


def test_apprise_fake_adapter_delivers_notification(tmp_path) -> None:
    service, apprise, _wecom = _service(tmp_path)
    note = service.mailbox.publish_notification(
        agent_id="opportunity-agent",
        cycle=1,
        kind="agent_initiative",
        title="Agent 想主动联系你",
        message="发现一个值得进一步确认的问题。",
    )
    service.tick()
    assert apprise.calls
    assert "Agent 想主动联系你" in apprise.calls[0]["title"]
    record = service.deliveries.get(SOURCE_NOTIFICATION, note.notification_id, CHANNEL_APPRISE)
    assert record is not None
    assert record.status == DELIVERY_DELIVERED


def test_apprise_failure_does_not_mark_delivered(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path, apprise=FakeApprise(fail=True))
    note = service.mailbox.publish_notification(
        agent_id="agent_one",
        cycle=1,
        kind="task_failed",
        title="任务失败",
        message="本轮研究没有完成。",
    )
    service.tick()
    record = service.deliveries.get(SOURCE_NOTIFICATION, note.notification_id, CHANNEL_APPRISE)
    assert record is not None
    assert record.status != DELIVERY_DELIVERED
    assert record.status == DELIVERY_FAILED
    assert record.last_error


def test_failed_notification_retries_until_delivered(tmp_path) -> None:
    apprise = FakeApprise(fail=True)
    service, _, _ = _service(tmp_path, apprise=apprise)
    note = service.mailbox.publish_notification(
        agent_id="agent_one",
        cycle=1,
        kind="task_completed",
        title="任务完成",
        message="本轮研究已结束，结果已经保存。",
    )
    service.tick()
    first = service.deliveries.get(SOURCE_NOTIFICATION, note.notification_id, CHANNEL_APPRISE)
    assert first is not None and first.status == DELIVERY_FAILED
    apprise.fail = False
    service.tick()
    second = service.deliveries.get(SOURCE_NOTIFICATION, note.notification_id, CHANNEL_APPRISE)
    assert second is not None
    assert second.status == DELIVERY_DELIVERED
    assert second.attempts == 2
    assert len(apprise.calls) == 1


def test_operator_service_restart_does_not_resend_notification(tmp_path) -> None:
    first, apprise, wecom = _service(tmp_path)
    first.mailbox.publish_notification(
        agent_id="agent_one",
        cycle=1,
        kind="agent_initiative",
        title="Agent 想主动联系你",
        message="需要你确认权限边界。",
    )
    first.tick()
    assert len(apprise.calls) == 1
    restarted, restarted_apprise, _ = _service(tmp_path, apprise=FakeApprise(), wecom=wecom)
    restarted.tick()
    assert restarted_apprise.calls == []


def test_allowed_wecom_user_enqueues_mailbox(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    request_id = service.handle_inbound_text(userid="zhangsan", text="你现在在做什么？")
    assert request_id is not None
    claimed = RuntimeMailbox(tmp_path).claim_next()
    assert claimed is not None
    assert claimed.partner_id == "wecom_zhangsan"
    assert claimed.message == "你现在在做什么？"


def test_non_operator_wecom_user_does_not_enqueue(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    assert service.handle_inbound_text(userid="lisi", text="你好") is None
    assert RuntimeMailbox(tmp_path).pending_count() == 0


def test_mailbox_response_returns_to_originating_wecom_user(tmp_path) -> None:
    service, _apprise, wecom = _service(tmp_path)
    request = service.mailbox.enqueue("你现在在做什么？", partner_id="wecom_zhangsan")
    service.routes.remember(request.request_id, userid="zhangsan")
    service.mailbox.complete(
        request,
        agent_id="agent_one",
        cycle=2,
        response="我正在整理当前长期记忆。",
    )
    service.tick()
    assert wecom.sent_messages
    assert wecom.sent_messages[0]["userid"] == "zhangsan"
    assert "长期记忆" in wecom.sent_messages[0]["text"]


def test_operator_service_restart_does_not_resend_mailbox_response(tmp_path) -> None:
    first, _apprise, wecom = _service(tmp_path)
    request = first.mailbox.enqueue("ping", partner_id="wecom_zhangsan")
    first.routes.remember(request.request_id, userid="zhangsan")
    first.mailbox.complete(request, agent_id="agent_one", cycle=1, response="pong")
    first.tick()
    assert len(wecom.sent_messages) == 1
    restarted, _, same_client = _service(tmp_path, wecom=wecom)
    restarted.tick()
    assert len(same_client.sent_messages) == 1


def test_wecom_conversation_still_enters_autobiographical_memory(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=ReplyProvider(),
        self_awareness_enabled=False,
        agency_threads_enabled=False,
        agency_discoveries_enabled=False,
        agency_experiments_enabled=False,
        agency_selection_enabled=False,
        selection_tracing_enabled=False,
        bounded_choice_enabled=False,
        diagnostic_coverage_enabled=False,
    )
    service, _apprise, wecom = _service(tmp_path)
    service.handle_inbound_text(userid="zhangsan", text="你现在在做什么？")
    status = ContinuousRunner(runtime).run(max_ticks=1)
    assert status.messages_completed == 1
    assert len(runtime.autobiographical_memory.dialogue_index) == 1
    service.tick()
    assert wecom.sent_messages
    assert wecom.sent_messages[-1]["userid"] == "zhangsan"


def test_approval_request_is_created_and_durable(tmp_path) -> None:
    clock = FrozenClock(NOW)
    store = ApprovalStore(tmp_path, clock=clock)
    created = store.create_pending(
        agent_id="trading-agent",
        cycle=4,
        proposal=_proposal(),
    )
    restored = ApprovalStore(tmp_path, clock=clock).get(created.approval_id)
    assert restored is not None
    assert restored.status == "PENDING"
    assert restored.action_digest == compute_action_digest(_proposal())
    assert (tmp_path / "operator" / "approvals" / f"{created.approval_id}.json").exists()


def test_pending_approval_is_sent_as_wecom_card(tmp_path) -> None:
    service, _apprise, wecom = _service(tmp_path)
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.tick()
    assert wecom.sent_cards
    card = wecom.sent_cards[0]["card"]
    keys = {item["key"] for item in card["button_list"]}
    assert keys == {f"approve:{request.approval_id}", f"deny:{request.approval_id}"}
    assert "command" not in str(card["button_list"]).lower()
    assert "shell" not in str(card).lower()


def test_approve_callback_records_decision(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.handle_card_event(userid="zhangsan", event_key=f"approve:{request.approval_id}")
    decision = service.approvals.get(request.approval_id)
    assert decision is not None
    assert decision.status == "APPROVED"
    assert decision.operator_id == "zhangsan"
    assert decision.channel == CHANNEL_WECOM


def test_deny_callback_records_decision(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.handle_card_event(userid="zhangsan", event_key=f"deny:{request.approval_id}")
    assert service.approvals.get(request.approval_id).status == "DENIED"


def test_expired_approval_cannot_be_approved(tmp_path) -> None:
    clock = FrozenClock(NOW)
    store = ApprovalStore(tmp_path, clock=clock, ttl_seconds=30)
    request = store.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
        ttl_seconds=30,
    )
    clock.advance(120)
    expired = store.expire_due()
    assert expired and expired[0].status == "EXPIRED"
    with pytest.raises(ApprovalStateError):
        store.approve(request.approval_id, operator_id="zhangsan", channel="wecom")
    assert store.get(request.approval_id).status == "EXPIRED"


def test_duplicate_approve_callback_is_idempotent(tmp_path) -> None:
    store = ApprovalStore(tmp_path, clock=FrozenClock(NOW))
    request = store.create_pending(agent_id="agent_one", cycle=1, proposal=_proposal())
    first = store.approve(request.approval_id, operator_id="zhangsan", channel="wecom")
    second = store.approve(request.approval_id, operator_id="lisi", channel="wecom")
    assert first.decision == "APPROVED"
    assert second.operator_id == "zhangsan"
    assert store.get(request.approval_id).operator_id == "zhangsan"


def test_non_operator_cannot_approve(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.handle_card_event(userid="lisi", event_key=f"approve:{request.approval_id}")
    assert service.approvals.get(request.approval_id).status == "PENDING"


def test_action_digest_mismatch_cannot_execute(tmp_path) -> None:
    store = ApprovalStore(tmp_path, clock=FrozenClock(NOW))
    policy = ActionPolicy(_autonomy_with_approval())
    policy.bind_approvals(store)
    original = _proposal()
    pending = store.create_pending(agent_id="agent_one", cycle=1, proposal=original)
    store.approve(pending.approval_id, operator_id="zhangsan", channel="wecom")
    mutated = _proposal(payload={"target": "mutated-target", "parameters": {"size": 99}})
    mutated.payload["approval_id"] = pending.approval_id
    result = policy.filter(
        [mutated],
        [],
        ResourceBudget.from_limits(1.0, 0.0),
    )
    assert result.authorized == []
    assert result.denied[0]["reason"] == "action digest mismatch"


def test_transport_failure_never_defaults_to_approved(tmp_path) -> None:
    wecom = FakeWeComClient()
    wecom.fail_send = True
    service, _apprise, _ = _service(tmp_path, wecom=wecom)
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.tick()
    assert service.approvals.get(request.approval_id).status == "PENDING"
    delivery = service.deliveries.get("approval_request", request.approval_id, CHANNEL_WECOM)
    assert delivery is not None
    assert delivery.status != DELIVERY_DELIVERED


def test_runtime_restart_keeps_pending_approval(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        autonomy=_autonomy_with_approval(),
        clock=FrozenClock(NOW),
        self_awareness_enabled=False,
        agency_threads_enabled=False,
        agency_discoveries_enabled=False,
        agency_experiments_enabled=False,
        agency_selection_enabled=False,
        selection_tracing_enabled=False,
        bounded_choice_enabled=False,
        diagnostic_coverage_enabled=False,
    )
    created = runtime.approval_store.create_pending(
        agent_id=runtime.identity.agent_id,
        cycle=runtime.cycle,
        proposal=_proposal(),
    )
    restored = AgentRuntime.restore(
        tmp_path,
        clock=FrozenClock(NOW),
        self_awareness_enabled=False,
        agency_threads_enabled=False,
        agency_discoveries_enabled=False,
        agency_experiments_enabled=False,
        agency_selection_enabled=False,
        selection_tracing_enabled=False,
        bounded_choice_enabled=False,
        diagnostic_coverage_enabled=False,
    )
    found = restored.approval_store.get(created.approval_id)
    assert found is not None
    assert found.status == "PENDING"


def test_operator_service_restart_can_continue_pending_approval(tmp_path) -> None:
    first, _apprise, wecom = _service(tmp_path)
    request = first.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    first.tick()
    assert len(wecom.sent_cards) == 1
    restarted, _, same = _service(tmp_path, wecom=wecom)
    restarted.tick()
    assert len(same.sent_cards) == 1
    restarted.handle_card_event(userid="zhangsan", event_key=f"approve:{request.approval_id}")
    assert restarted.approvals.get(request.approval_id).status == "APPROVED"


def test_matching_digest_reenters_original_policy_path(tmp_path) -> None:
    store = ApprovalStore(tmp_path, clock=FrozenClock(NOW))
    policy = ActionPolicy(_autonomy_with_approval())
    policy.bind_approvals(store)
    proposal = _proposal()
    blocked = policy.filter([proposal], [], ResourceBudget.from_limits(1.0, 0.0))
    assert blocked.denied[0]["reason"] == "requires human approval"
    pending = store.create_pending(agent_id="agent_one", cycle=1, proposal=proposal)
    store.approve(pending.approval_id, operator_id="zhangsan", channel="wecom")
    allowed = policy.filter([proposal], [], ResourceBudget.from_limits(1.0, 0.0))
    assert [item.kind for item in allowed.authorized] == ["evolution_activate"]


def test_wecom_gateway_has_no_executor(tmp_path) -> None:
    service, _apprise, _wecom = _service(tmp_path)
    assert not hasattr(service.wecom, "execute")
    assert not hasattr(service.wecom, "executor")


def test_burst_guard_limits_non_critical_notification_flood(tmp_path) -> None:
    clock = FrozenClock(NOW)
    service, apprise, _wecom = _service(tmp_path, clock=clock)
    service.burst.limit = 2
    for index in range(5):
        service.mailbox.publish_notification(
            agent_id="agent_one",
            cycle=1,
            kind="agent_initiative",
            title=f"notice-{index}",
            message="repeated contact",
        )
    service.tick()
    assert len(apprise.calls) == 2
    undelivered = service.deliveries.undelivered()
    assert len(undelivered) == 3


def test_mailbox_response_attachments_are_uploaded_and_sent(tmp_path) -> None:
    service, _apprise, wecom = _service(tmp_path, operator_ids=("ZhangSan",))
    request = service.mailbox.enqueue("写个题图", partner_id="wecom_ZhangSan")
    service.routes.remember(request.request_id, userid="ZhangSan")
    service.mailbox.complete(
        request,
        agent_id="trading-agent",
        cycle=1,
        response="见海报",
        attachments=["C:/tmp/poster.png"],
    )
    service.tick()
    assert wecom.sent_messages[-1]["text"] == "见海报"
    assert wecom.uploaded == ["C:/tmp/poster.png"]
    assert wecom.sent_images == [
        {"userid": "ZhangSan", "media_id": "media-poster.png"}
    ]


def test_gateway_approves_from_real_aibot_card_event_shape(tmp_path) -> None:
    """The real gateway nests event_key at event.template_card_event.event_key."""
    service, _apprise, _wecom = _service(tmp_path, operator_ids=("ZhangSan",))
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    frame = {
        "msgid": "9a8f67d619ad75e2bcf6d4584385a7be",
        "aibotid": "aibhE2o071PkvxOI3lPB0cnDfyVT73Yp8_G",
        "chattype": "single",
        "from": {"userid": "ZhangSan"},
        "msgtype": "event",
        "event": {
            "eventtype": "template_card_event",
            "template_card_event": {
                "card_type": "button_interaction",
                "event_key": f"approve:{request.approval_id}",
                "task_id": request.approval_id,
            },
        },
    }
    service.wecom.handle_frame(frame)
    record = service.approvals.get(request.approval_id)
    assert record is not None
    assert record.status == "APPROVED"
    assert record.operator_id == "ZhangSan"
    assert record.channel == CHANNEL_WECOM


def test_approval_result_delivery_survives_colon_in_source_id(tmp_path) -> None:
    """source_id `apr_x:APPROVED` must not break the delivery filename on Windows."""
    service, _apprise, wecom = _service(tmp_path, operator_ids=("ZhangSan",))
    request = service.approvals.create_pending(
        agent_id="trading-agent",
        cycle=1,
        proposal=_proposal(),
    )
    service.approvals.approve(
        request.approval_id, operator_id="ZhangSan", channel="wecom"
    )
    service.tick()
    delivery = service.deliveries.get(
        "approval_result", f"{request.approval_id}:APPROVED", CHANNEL_WECOM
    )
    assert delivery is not None
    assert delivery.status == DELIVERY_DELIVERED
    assert wecom.sent_messages
