"""OfficialWeComClient wire format follows the aibot long-connection protocol."""
from __future__ import annotations

import asyncio

from agent_runtime.communication.operator.wecom import OfficialWeComClient


class _FakeSdkClient:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.replies: list[tuple[dict, dict]] = []

    def on(self, event: str):
        def deco(fn):
            self.handlers[event] = fn
            return fn

        return deco

    async def reply(self, frame: dict, body: dict, cmd=None):
        _ = cmd
        self.replies.append((frame, body))
        return {}

    async def send_message(self, chatid: str, body: dict):
        return {}

    def run(self) -> None:
        pass


class _StubGateway:
    def __init__(self, handled: str | None) -> None:
        self.handled = handled
        self.frames: list[dict] = []

    def handle_frame(self, frame: dict):
        self.frames.append(frame)
        return self.handled


def _bound_client(handled: str | None) -> tuple[OfficialWeComClient, _FakeSdkClient, _StubGateway]:
    client = OfficialWeComClient(bot_id="test-bot", secret="test-secret")
    fake = _FakeSdkClient()
    client._client = fake
    gateway = _StubGateway(handled)
    client.bind(gateway)
    return client, fake, gateway


def test_bind_acks_accepted_text_with_passive_markdown() -> None:
    client, fake, gateway = _bound_client(handled="msg_1")
    frame = {"headers": {"req_id": "r1"}, "body": {"msgtype": "text"}}
    asyncio.run(fake.handlers["message.text"](frame))  # type: ignore[operator]
    assert gateway.frames == [frame]
    assert len(fake.replies) == 1
    assert fake.replies[0][0] is frame
    body = fake.replies[0][1]
    assert body["msgtype"] == "markdown"
    assert "处理" in body["markdown"]["content"]


def test_bind_does_not_ack_ignored_messages() -> None:
    _client, fake, gateway = _bound_client(handled=None)
    frame = {"headers": {"req_id": "r2"}, "body": {"msgtype": "text"}}
    asyncio.run(fake.handlers["message.text"](frame))  # type: ignore[operator]
    assert gateway.frames == [frame]
    assert fake.replies == []


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


def test_send_image_uses_media_envelope() -> None:
    client = OfficialWeComClient(bot_id="test-bot", secret="test-secret")
    sent: list[tuple[str, dict]] = []

    async def fake_send(chatid: str, body: dict):
        sent.append((chatid, body))
        return object()

    client._client.send_message = fake_send
    client.send_image("ZhangSan", "media-123")

    assert sent == [
        ("ZhangSan", {"msgtype": "image", "image": {"media_id": "media-123"}})
    ]


def test_upload_image_chunks_and_returns_media_id(tmp_path, monkeypatch) -> None:
    client = OfficialWeComClient(bot_id="test-bot", secret="test-secret")
    png = tmp_path / "poster.png"
    png.write_bytes(b"x" * (512 * 1024 + 10))
    frames: dict[str, list[dict]] = {}

    async def fake_send_reply(req_id: str, body: dict, cmd: str):
        frames.setdefault(cmd, []).append(body)
        if cmd == "aibot_upload_media_init":
            return {"body": {"upload_id": "up-1"}}
        if cmd == "aibot_upload_media_finish":
            return {"body": {"media_id": "m-1"}}
        return {"body": {}}

    monkeypatch.setattr(
        client._client._ws_manager, "send_reply", fake_send_reply
    )
    media_id = client.upload_image(str(png))

    assert media_id == "m-1"
    assert frames["aibot_upload_media_init"][0]["total_chunks"] == 2
    assert frames["aibot_upload_media_init"][0]["type"] == "image"
    assert len(frames["aibot_upload_media_chunk"]) == 2
    assert frames["aibot_upload_media_chunk"][1]["chunk_index"] == 1
    assert frames["aibot_upload_media_chunk"][1]["upload_id"] == "up-1"
    assert frames["aibot_upload_media_finish"][0] == {"upload_id": "up-1"}
