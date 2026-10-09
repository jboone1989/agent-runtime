import json, hashlib
import pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider

def test_unresponsive_local_carrier_is_retired_and_settled(tmp_path, monkeypatch):
    class Browser:
        def owned_session_url(self, sid):
            return "https://chatgpt.com/c/local-chatgpt%3Afailed"
        def retire_unresponsive_owned_session(self, sid, *, expected_url):
            self.retired = (sid, expected_url)
    browser = Browser()
    path = tmp_path / "conversation.json"
    provider = ChatGPTBrowserProvider(browser=browser, conversation_state_path=path)
    outbound = "original request"
    pending = {"request_id": "1"*32, "outbound": outbound,
        "payload_sha256": hashlib.sha256(outbound.encode()).hexdigest(),
        "submission_acknowledged": True, "browser_session_id": "owned"}
    journal = {"schema_version": 1, "session_id": provider.session_id, "pending": pending}
    path.write_text(json.dumps(journal))
    def fail(*args):
        raise RuntimeError("Page.enable timed out after 15s")
    monkeypatch.setattr(provider, "_run_turn_once", fail)
    with pytest.raises(RuntimeError, match="retired unresponsive local carrier"):
        provider._run_turn(outbound, 10, journal)
    settled = json.loads(path.read_text())
    assert settled["pending"] is None
    assert browser.retired[0] == "owned"
    archive = json.loads(__import__("pathlib").Path(settled["last_unresolved_request"]["archive"]).read_text())
    assert archive["outbound"] == outbound
    assert "response" not in archive

def test_server_conversation_is_not_retired(tmp_path, monkeypatch):
    class Browser:
        def owned_session_url(self, sid):
            return "https://chatgpt.com/c/server-owned"
        def retire_unresponsive_owned_session(self, *args, **kwargs):
            pytest.fail("server conversation must not be retired as ephemeral")
        def replace_owned_session(self, *args, **kwargs):
            raise RuntimeError("bounded replacement failed")
    path = tmp_path / "conversation.json"
    provider = ChatGPTBrowserProvider(browser=Browser(), conversation_state_path=path)
    pending = {"request_id": "2"*32, "outbound": "request", "payload_sha256": "hash",
        "submission_acknowledged": True}
    journal = {"schema_version": 1, "session_id": provider.session_id, "pending": pending}
    path.write_text(json.dumps(journal))
    def fail(*args):
        raise RuntimeError("Page.enable timed out")
    monkeypatch.setattr(provider, "_run_turn_once", fail)
    with pytest.raises(RuntimeError, match="bounded replacement failed"):
        provider._run_turn("request", 10, journal)
    assert json.loads(path.read_text())["pending"] == pending
