from __future__ import annotations

import asyncio
import json

from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, _BrowserProfileProcessLock


class FakeBrowser:
    def __init__(self) -> None:
        self.adopted = False
        self.transaction_script = ""
        self.transaction_timeout = 0.0

    def adopt_existing_tab(self, session_id: str, url_prefix: str) -> dict:
        self.adopted = True
        return {"ok": True, "session_id": session_id, "url": url_prefix}

    def run_session_script(
        self,
        session_id: str,
        script: str,
        *,
        timeout_seconds: float,
    ) -> dict:
        _ = session_id
        self.transaction_script = script
        self.transaction_timeout = timeout_seconds
        return {
            "response": "runtime browser answer",
            "url": "https://chatgpt.com/c/test",
            "assistant_message_count": 1,
        }


def test_chatgpt_browser_provider_adopts_existing_tab_and_completes() -> None:
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(
        browser=browser,
        poll_interval_seconds=0.1,
        stable_seconds=0.5,
        response_timeout_seconds=10,
    )

    result = asyncio.run(
        provider.complete(
            "Answer exactly with a short phrase.",
            system="Follow the runtime contract.",
            max_tokens=40,
        )
    )

    assert result == "runtime browser answer"
    assert browser.adopted is True
    assert "System instructions for this request:" in browser.transaction_script
    assert "Follow the runtime contract." in browser.transaction_script
    assert "Answer exactly with a short phrase." in browser.transaction_script
    assert "sent = js(send_expr)" in browser.transaction_script
    assert "ChatGPT Send button is unavailable" in browser.transaction_script
    assert provider.last_metadata["provider"] == "chatgpt-browser"


def test_chatgpt_browser_provider_discovers_runtime_profile(monkeypatch, tmp_path) -> None:
    state = (
        tmp_path
        / ".local"
        / "state"
        / "agent-runtime"
        / "browser"
        / "profiles"
        / "chatgpt"
        / "runtime.json"
    )
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps({"cdp_port": 9600}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_RUNTIME_BROWSER_HARNESS", "/bin/true")
    monkeypatch.delenv("AGENT_RUNTIME_CHATGPT_CDP_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_CHATGPT_PROFILE_STATE", raising=False)

    provider = ChatGPTBrowserProvider.from_environment()

    assert provider.browser.runtime.cdp_url == "http://127.0.0.1:9600"
    assert provider.browser.runtime.executable == "/bin/true"


def test_load_llm_provider_selects_chatgpt_browser(monkeypatch) -> None:
    import agent_runtime.llm as llm

    sentinel = object()
    monkeypatch.setenv("AGENT_RUNTIME_LLM_PROVIDER", "chatgpt-browser")
    monkeypatch.setattr(
        llm.ChatGPTBrowserProvider,
        "from_environment",
        classmethod(lambda cls: sentinel),
    )

    assert llm.load_llm_provider() is sentinel


def test_chatgpt_profile_lock_is_exclusive(tmp_path) -> None:
    path = tmp_path / "chatgpt.lock"
    with _BrowserProfileProcessLock(path, timeout_seconds=1):
        try:
            with _BrowserProfileProcessLock(path, timeout_seconds=0.1):
                raise AssertionError("second lock unexpectedly acquired")
        except TimeoutError:
            pass
    with _BrowserProfileProcessLock(path, timeout_seconds=0.1):
        pass


def test_chatgpt_provider_uses_profile_scoped_process_lock(monkeypatch, tmp_path) -> None:
    state = (
        tmp_path
        / ".local"
        / "state"
        / "agent-runtime"
        / "browser"
        / "profiles"
        / "chatgpt"
        / "runtime.json"
    )
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"cdp_port": 9600}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_RUNTIME_BROWSER_HARNESS", "/bin/true")
    monkeypatch.delenv("AGENT_RUNTIME_CHATGPT_CDP_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_CHATGPT_PROFILE_STATE", raising=False)

    provider = ChatGPTBrowserProvider.from_environment()

    assert provider.process_lock_path.name == "chatgpt-profile-chatgpt.lock"


def test_chatgpt_state_expression_supports_current_semantic_message_units() -> None:
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION

    assert '[data-content-search-unit-key$=":' in _CHATGPT_STATE_EXPRESSION
    assert '[data-chatgpt-selection-message-id]' in _CHATGPT_STATE_EXPRESSION
    assert '[data-message-author-role="' in _CHATGPT_STATE_EXPRESSION


def test_chatgpt_composer_focus_requires_visible_editable_target() -> None:
    from agent_runtime.llm.chatgpt_browser import _FOCUS_COMPOSER_EXPRESSION, _CHATGPT_STATE_EXPRESSION

    assert 'getClientRects' in _FOCUS_COMPOSER_EXPRESSION
    assert '[contenteditable="true"][role="textbox"]' in _FOCUS_COMPOSER_EXPRESSION
    assert 'getClientRects' in _CHATGPT_STATE_EXPRESSION
    assert '[contenteditable="true"][role="textbox"]' in _CHATGPT_STATE_EXPRESSION


def test_chatgpt_transaction_chunks_long_input_and_verifies_normalized_text() -> None:
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(
        browser=browser,
        poll_interval_seconds=0.1,
        stable_seconds=0.5,
        response_timeout_seconds=10,
    )

    asyncio.run(provider.complete("x" * 12000))

    assert "input_chunk_chars = 4000" in browser.transaction_script
    assert "for start in range(0, len(outbound), input_chunk_chars)" in browser.transaction_script
    assert "expected_text = ' '.join(outbound.split())" in browser.transaction_script
    assert "actual_text = ' '.join" in browser.transaction_script
