"""OfficialWeComClient wire format follows the aibot long-connection protocol."""
from __future__ import annotations

from agent_runtime.communication.operator.wecom import OfficialWeComClient


def test_send_text_uses_markdown_envelope_not_text() -> None:
    """aibot_send_msg accepts only markdown/template_card; text gets errcode 40008."""
    client = OfficialWeComClient(bot_id="test-bot", secret="test-secret")
    sent: list[tuple[str, dict]] = []

    async def fake_send(chatid: str, body: dict):
        sent.append((chatid, body))
        return object()

    client._client.send_message = fake_send
    client.send_text("ZhangSan", "你好，Agent")

    assert sent == [
        ("ZhangSan", {"msgtype": "markdown", "markdown": {"content": "你好，Agent"}})
    ]
