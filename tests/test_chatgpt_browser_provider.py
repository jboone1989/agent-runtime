from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, _BrowserProfileProcessLock


class FakeBrowser:
    def __init__(self) -> None:
        self.adopted = False
        self.transaction_script = ""
        self.transaction_timeout = 0.0
        self.payload = ""
        self.payload_path = None

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
        line = next(line for line in script.splitlines() if line.startswith("outbound = Path("))
        self.payload_path = Path(json.loads(line.split("Path(", 1)[1].split(").read_text", 1)[0]))
        self.payload = self.payload_path.read_text(encoding="utf-8")
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
    assert "System instructions for this request:" in browser.payload
    assert "Follow the runtime contract." in browser.payload
    assert "Answer exactly with a short phrase." in browser.payload
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


def test_chatgpt_transaction_chunks_prosemirror_execcommand_and_verifies_each_prefix() -> None:
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(
        browser=browser,
        poll_interval_seconds=0.1,
        stable_seconds=0.5,
        response_timeout_seconds=10,
    )

    asyncio.run(provider.complete("x" * 12000))

    assert "input_chunk_chars = 4000" in browser.transaction_script
    assert "document.execCommand('insertText', false," in browser.transaction_script
    assert "inserted = js(insert_expr)" in browser.transaction_script
    assert "expected_prefix = ' '.join(outbound[:end].split())" in browser.transaction_script
    assert "ChatGPT composer chunk verification failed" in browser.transaction_script
    assert "expected_text = ' '.join(outbound.split())" in browser.transaction_script
    assert "actual_text = ' '.join" in browser.transaction_script
    assert "expected_codes=" in browser.transaction_script
    assert "actual_codes=" in browser.transaction_script


def test_chatgpt_composer_text_rebuilds_prosemirror_blocks_without_render_spacing() -> None:
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION

    assert "[...composer.childNodes]" in _CHATGPT_STATE_EXPRESSION
    assert ".map((node) => node.textContent || '')" in _CHATGPT_STATE_EXPRESSION
    assert ".join('\\n')" in _CHATGPT_STATE_EXPRESSION


def test_large_cognition_request_is_preserved_outside_script_budget(tmp_path):
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(browser=browser, process_lock_path=tmp_path/"profile.lock")
    prompt = "长请求引号\\\"和换行\n" * 20000
    asyncio.run(provider.complete(prompt))
    assert browser.payload == prompt.strip()
    assert len(browser.transaction_script) < 131072
    assert not browser.payload_path.exists()


def test_transaction_waits_for_empty_fresh_chat_and_new_answer(tmp_path):
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(browser=browser, poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path/"request.txt"
    payload.write_text("hello")
    script = provider._transaction_script(payload, 5)
    initial = {"url":"https://chatgpt.com/c/old","user_count":9,"assistant_count":9,"composer_selector":"#prompt-textarea","composer_text":"","generating":False}
    stale_root = {**initial,"url":"https://chatgpt.com/"}
    fresh = {**stale_root,"user_count":0,"assistant_count":0}
    typed = {**fresh,"composer_text":"hello"}
    response = {**fresh,"url":"https://chatgpt.com/c/new","user_count":1,"assistant_count":1,"last_assistant_text":"current answer"}
    states = iter([initial,stale_root,fresh,typed,typed,typed,response,response,response])
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += 0.3;return now[0]
        def sleep(self, interval): now[0] += interval
    def js(expr):
        if expr.startswith("(() =>") and "composer_selector" in expr:
            return next(states)
        return True
    # Replace only the clock import; all generated transaction logic executes.
    namespace = {"js":js,"time":Clock()}
    exec(script.replace("import time\n",""),namespace)
    assert namespace["result"]["response"] == "current answer"
    assert namespace["baseline_user_count"] == 0


def test_page_state_excludes_hidden_retained_messages_and_duplicate_units():
    import shutil
    import subprocess
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("Node is required to execute the page-state expression")
    fixture = """
    function message(key, text, visible) {
      return {innerText:text,textContent:text,getClientRects:()=>visible?[{}]:[],
        getAttribute:name=>name==='data-content-search-unit-key'?key:null,
        querySelector:()=>null};
    }
    const users=[message('old:user','old',false),message('current:user','new',true),message('current:user','new',true)];
    const assistants=[message('old:assistant','stale answer',false),message('current:assistant','current answer',true)];
    global.location={href:'https://chatgpt.com/c/current'};
    global.document={querySelectorAll:selector=>{
      if(selector.startsWith('[data-content-search-unit-key'))
        return selector.includes(':user')?users:assistants;
      return [];
    }};
    """
    result = subprocess.run([node,"-e",fixture+"console.log(JSON.stringify("+_CHATGPT_STATE_EXPRESSION+"))"],capture_output=True,text=True,check=True)
    state=json.loads(result.stdout)
    assert state["user_count"] == 1
    assert state["assistant_count"] == 1
    assert state["last_assistant_text"] == "current answer"
