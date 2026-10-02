from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

import pytest

from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, _BrowserProfileProcessLock


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))


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

    def invoke(self, capability_id, payload):
        return {"ok": True, "url": payload["url"]}

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


@pytest.mark.parametrize("rendered_user_count", [0, 1])
def test_transaction_waits_for_empty_fresh_chat_and_new_answer(tmp_path, rendered_user_count):
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(browser=browser, poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path/"request.txt"
    payload.write_text("hello")
    script = provider._transaction_script(payload, 5)
    initial = {"url":"https://chatgpt.com/c/old","user_count":9,"assistant_count":9,"composer_selector":"#prompt-textarea","composer_text":"","generating":False}
    stale_root = {**initial,"url":"https://chatgpt.com/"}
    fresh = {**stale_root,"user_count":0,"assistant_count":0}
    typed = {**fresh,"composer_text":"hello"}
    response = {**fresh,"url":"https://chatgpt.com/c/new","user_count":rendered_user_count,"assistant_count":1,"last_assistant_text":"current answer"}
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

@pytest.mark.parametrize("send_timeout", [False, True])
def test_busy_submission_collects_answer_without_resending(tmp_path, send_timeout):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    provider = ChatGPTBrowserProvider(browser=FakeBrowser(), poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path / "request.txt"
    payload.write_text("hello")
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False}
    typed = {**fresh,"composer_text":"hello"}
    response = {**fresh,"url":"https://chatgpt.com/c/new","assistant_count":1,"last_assistant_text":"new answer"}
    counts = {"state":0,"send":0}
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += 0.3; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        if expr == _SEND_EXPRESSION:
            counts["send"] += 1
            if send_timeout:
                raise RuntimeError("Runtime.evaluate timed out; expression: send")
            return True
        if expr == _CHATGPT_STATE_EXPRESSION:
            counts["state"] += 1
            if counts["state"] == 1: return fresh
            if counts["state"] <= 3: return typed
            if counts["state"] == 4:
                raise RuntimeError("Runtime.evaluate timed out; expression: state")
            return response
        return True
    scope = {"js":js,"time":Clock()}
    exec(provider._transaction_script(payload, 5).replace("import time\n",""), scope)
    assert scope["result"]["response"] == "new answer"
    assert counts["send"] == 1
    assert scope["poll_timeout_count"] == 1


def test_submit_schedules_click_before_expensive_page_render():
    import shutil, subprocess
    from agent_runtime.llm.chatgpt_browser import _SEND_EXPRESSION
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the submit expression")
    fixture = """
    let clicked=0, scheduled=null;
    const button={getClientRects:()=>[{}],disabled:false,getAttribute:name=>name==='aria-label'?'Send':null,click:()=>{clicked++}};
    global.document={querySelectorAll:()=>[button]};
    global.setTimeout=(fn)=>{scheduled=fn};
    """
    body = fixture + "const result=" + _SEND_EXPRESSION + "; const before=clicked; scheduled(); console.log(JSON.stringify({result,before,after:clicked}));"
    result = subprocess.run([node,"-e",body],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout) == {"result":True,"before":0,"after":1}


@pytest.mark.parametrize("insert_timeout", [False, True])
def test_busy_insert_is_verified_without_retyping_or_early_send(tmp_path, insert_timeout):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    provider = ChatGPTBrowserProvider(browser=FakeBrowser(), poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path / "request.txt"
    text = "a" * 8500
    payload.write_text(text)
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False}
    buffer = [""]
    counts = {"insert":0,"send":0}
    busy = [False]
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += 0.3; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        if "document.execCommand('insertText'" in expr:
            counts["insert"] += 1
            chunk = json.loads(expr.split("false, ", 1)[1].split(" ), 0)", 1)[0])
            buffer[0] += chunk
            busy[0] = True
            if insert_timeout:
                raise RuntimeError("Runtime.evaluate timed out; expression: insertText")
            return True
        if expr == _SEND_EXPRESSION:
            assert buffer[0] == text
            counts["send"] += 1
            return True
        if expr == _CHATGPT_STATE_EXPRESSION:
            if busy[0]:
                busy[0] = False
                raise RuntimeError("Runtime.evaluate timed out; expression: state")
            if counts["send"]:
                return {**fresh,"assistant_count":1,"last_assistant_text":"answer","url":"https://chatgpt.com/c/new"}
            return {**fresh,"composer_text":buffer[0]}
        return True
    scope = {"js":js,"time":Clock()}
    exec(provider._transaction_script(payload, 5).replace("import time\n",""), scope)
    assert counts == {"insert":3,"send":1}
    assert scope["result"]["response"] == "answer"


def test_missing_insert_never_sends_unverified_request(tmp_path):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    provider = ChatGPTBrowserProvider(browser=FakeBrowser(), poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path / "request.txt"
    payload.write_text("hello")
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False}
    counts = {"insert":0,"send":0}
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += 1; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        if "document.execCommand('insertText'" in expr:
            counts["insert"] += 1
            raise RuntimeError("Runtime.evaluate timed out; expression: insertText")
        if expr == _SEND_EXPRESSION:
            counts["send"] += 1
            raise AssertionError("unverified text was submitted")
        if expr == _CHATGPT_STATE_EXPRESSION:
            return fresh
        return True
    with pytest.raises(RuntimeError, match="composer chunk verification failed"):
        exec(provider._transaction_script(payload, 5).replace("import time\n",""), {"js":js,"time":Clock()})
    assert counts == {"insert":1,"send":0}


class ConversationBrowser(FakeBrowser):
    """Execute the real transaction against a page that changes across turns."""

    def __init__(self):
        super().__init__()
        self.url = "https://chatgpt.com/"
        self.answer_id = ""
        self.answer = ""
        self.count = 0
        self.buffer = ""
        self.sends = 0
        self.new_chats = 0
        self.navigations = []
        self.pending_answer = None
        self.generate_forever = False
        self.fail_after_send = False

    def invoke(self, capability_id, payload):
        self.navigations.append(payload["url"])
        self.url = payload["url"]
        # Simulate long-conversation DOM virtualization: visible counts are
        # constant even though a new message receives a new stable ID.
        self.count = min(self.count, 1)
        return {"ok": True, "url": self.url}

    def run_session_script(self, session_id, script, *, timeout_seconds):
        from agent_runtime.llm.chatgpt_browser import (
            _CHATGPT_STATE_EXPRESSION, _NEW_CHAT_EXPRESSION, _SEND_EXPRESSION,
        )
        now = [0.0]
        class Clock:
            def monotonic(self):
                now[0] += 0.3
                return now[0]
            def sleep(self, seconds):
                now[0] += seconds
        def js(expression):
            if expression == _NEW_CHAT_EXPRESSION:
                self.new_chats += 1
                self.url = "https://chatgpt.com/"
                self.answer_id = ""
                self.answer = ""
                self.count = 0
                return True
            if "document.execCommand('insertText'" in expression:
                self.buffer += json.loads(expression.split("false, ", 1)[1].split(" ), 0)", 1)[0])
                return True
            if expression == _SEND_EXPRESSION:
                self.sends += 1
                self.url = "https://chatgpt.com/c/durable-test"
                self.pending_answer = (f"answer-{self.sends}", f"reply to {self.buffer}")
                self.buffer = ""
                if self.fail_after_send:
                    raise RuntimeError("browser process disconnected after submission")
                return True
            if expression == _CHATGPT_STATE_EXPRESSION:
                if self.pending_answer and not self.generate_forever:
                    self.answer_id, self.answer = self.pending_answer
                    self.pending_answer = None
                    self.count = 1
                return {"url": self.url, "user_count": self.count,
                        "assistant_count": self.count, "last_assistant_id": self.answer_id,
                        "last_assistant_text": self.answer,
                        "composer_selector": "#prompt-textarea", "composer_text": self.buffer,
                        "generating": bool(self.pending_answer)}
            return True
        scope = {"js": js, "time": Clock()}
        try:
            exec(script.replace("import time\n", ""), scope)
        except AssertionError as exc:
            # Browser Harness surfaces failed script assertions as RuntimeError.
            raise RuntimeError(str(exc)) from exc
        return scope["result"]


def conversation_provider(browser, tmp_path):
    return ChatGPTBrowserProvider(browser=browser, process_lock_path=tmp_path / "profile.lock",
                                  poll_interval_seconds=0.1, stable_seconds=0.5,
                                  response_timeout_seconds=5)


def test_restart_restores_same_conversation_and_reads_new_message_with_constant_count(tmp_path):
    browser = ConversationBrowser()
    first = conversation_provider(browser, tmp_path)
    assert asyncio.run(first.complete("first")) == "reply to first"
    url = first.last_metadata["url"]
    browser.url = "https://chatgpt.com/c/unrelated"
    restarted = conversation_provider(browser, tmp_path)
    assert asyncio.run(restarted.complete("second")) == "reply to second"
    assert restarted.last_metadata["url"] == url
    assert browser.navigations == [url]
    assert browser.new_chats == 0
    assert browser.sends == 2


def test_response_timeout_survives_restart_and_does_not_resend(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser = ConversationBrowser()
    browser.generate_forever = True
    first = conversation_provider(browser, tmp_path)
    with pytest.raises(ChatGPTBrowserProviderError, match="response exceeded"):
        asyncio.run(first.complete("first"))
    journal = json.loads(first.conversation_state_path.read_text())
    assert journal["conversation_url"] == "https://chatgpt.com/c/durable-test"
    assert journal["pending"]["stage"] == "WAITING_RESPONSE"
    browser.generate_forever = False
    restarted = conversation_provider(browser, tmp_path)
    assert asyncio.run(restarted.complete("first")) == "reply to first"
    assert restarted.last_metadata["recovered"] is True
    assert browser.sends == 1
    assert json.loads(first.conversation_state_path.read_text())["pending"] is None


def test_new_turn_recovers_pending_response_before_sending(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser = ConversationBrowser()
    browser.generate_forever = True
    provider = conversation_provider(browser, tmp_path)
    with pytest.raises(ChatGPTBrowserProviderError):
        asyncio.run(provider.complete("first"))
    browser.generate_forever = False
    assert asyncio.run(conversation_provider(browser, tmp_path).complete("second")) == "reply to second"
    assert browser.sends == 2
    assert browser.new_chats == 0


def test_wrong_bound_conversation_never_submits(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser = ConversationBrowser()
    provider = conversation_provider(browser, tmp_path)
    asyncio.run(provider.complete("first"))
    def ignore_navigation(capability_id, payload):
        browser.url = "https://chatgpt.com/c/other"
    browser.invoke = ignore_navigation
    with pytest.raises(ChatGPTBrowserProviderError, match="failed to restore"):
        asyncio.run(provider.complete("second"))
    assert browser.sends == 1


def test_initial_binding_starts_once_then_preserves_history(tmp_path):
    browser = ConversationBrowser()
    browser.url = "https://chatgpt.com/c/unrelated"
    browser.count = 9
    provider = conversation_provider(browser, tmp_path)
    asyncio.run(provider.complete("first"))
    asyncio.run(provider.complete("second"))
    assert browser.new_chats == 1
    assert provider.last_metadata["conversation_id"] == "durable-test"


def test_process_disconnect_after_send_is_recovered_without_duplicate_submission(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser = ConversationBrowser()
    browser.fail_after_send = True
    provider = conversation_provider(browser, tmp_path)
    with pytest.raises(ChatGPTBrowserProviderError, match="disconnected"):
        asyncio.run(provider.complete("first"))
    state = json.loads(provider.conversation_state_path.read_text())
    assert state["pending"]["stage"] == "SUBMITTING"
    assert not state.get("conversation_url")
    browser.fail_after_send = False
    assert asyncio.run(conversation_provider(browser, tmp_path).complete("first")) == "reply to first"
    assert browser.sends == 1


def test_repeated_timeout_retains_original_pending_turn(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser = ConversationBrowser()
    browser.generate_forever = True
    for _ in range(2):
        with pytest.raises(ChatGPTBrowserProviderError, match="response exceeded"):
            asyncio.run(conversation_provider(browser, tmp_path).complete("first"))
    assert browser.sends == 1
    browser.generate_forever = False
    assert asyncio.run(conversation_provider(browser, tmp_path).complete("first")) == "reply to first"
