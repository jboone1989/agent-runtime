"""The local mailbox preserves identity, delivery state, and conversation evidence."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.communication.mailbox import RuntimeMailbox
from agent_runtime.communication.server import build_server
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import ContinuousRunner, _contact_decision, _memory_decision


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


class ReplyProvider:
    async def complete(self, prompt: str, **kwargs) -> str:
        _ = (prompt, kwargs)
        return "我收到了。这条回复属于同一持久身份，并会进入共同经历。"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def test_mailbox_request_claim_and_response_survive_new_client(tmp_path) -> None:
    sender = RuntimeMailbox(tmp_path)
    request = sender.enqueue("你记得我吗？", partner_id="operator")
    assert sender.pending_count() == 1

    receiver = RuntimeMailbox(tmp_path)
    claimed = receiver.claim_next()
    assert claimed == request
    assert receiver.pending_count() == 0
    receiver.complete(
        claimed,
        agent_id="agent_one",
        cycle=7,
        response="记得。",
    )

    result = RuntimeMailbox(tmp_path).response(request.request_id)
    assert result is not None
    assert result.status == "COMPLETED"
    assert result.agent_id == "agent_one"
    assert result.response == "记得。"


def test_runner_processes_mailbox_through_persistent_conversation(tmp_path) -> None:
    provider = ReplyProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    mailbox = RuntimeMailbox(tmp_path)
    request = mailbox.enqueue("这是我们的第一次直连消息。")

    status = ContinuousRunner(runtime).run(max_ticks=1)
    result = mailbox.response(request.request_id)

    assert status.messages_completed == 1
    assert status.message_errors == 0
    assert result is not None
    assert result.agent_id == runtime.identity.agent_id
    assert result.response is not None and result.response.startswith("我收到了")
    assert len(runtime.autobiographical_memory.dialogue_index) == 1
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert len(restored.autobiographical_memory.dialogue_index) == 1


def test_proactive_contact_requires_explicit_reflection_marker() -> None:
    assert _contact_decision("普通反思，没有决定") is None
    assert _contact_decision("CONTACT_OPERATOR: NO") is None
    assert (
        _contact_decision("结论\nCONTACT_OPERATOR: YES | 我发现授权边界存在矛盾，需要与你协商。")
        == "我发现授权边界存在矛盾，需要与你协商。"
    )


def test_self_memory_decision_requires_valid_bounded_json_marker() -> None:
    assert _memory_decision("MEMORY_DECISION: not-json") is None
    assert (
        _memory_decision(
            'MEMORY_DECISION: {"preserve":true,"summary":"","reason":"",'
            '"contest_ids":[],"retract_ids":[]}'
        )
        is None
    )
    decision = _memory_decision(
        'MEMORY_DECISION: {"preserve":true,"summary":"保留边界意识",'
        '"reason":"约束未来行动","contest_ids":["not-an-id"],"retract_ids":[]}'
    )
    assert decision is not None
    assert decision.preserve is True
    assert decision.summary == "保留边界意识"
    assert decision.contest_ids == []


def test_local_chat_server_requires_token_and_enqueues_message(tmp_path) -> None:
    server = build_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=3)
        assert denied.value.code == 403

        request = urllib.request.Request(
            f"http://{host}:{port}/api/messages",
            data=json.dumps({"message": "直连测试"}).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-agent-chat-token": server.access_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        assert payload["request"]["message"] == "直连测试"
        assert RuntimeMailbox(tmp_path).pending_count() == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
