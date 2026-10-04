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

    assert "input_chunk_chars = 4096" in browser.transaction_script
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
    body, fingerprint = browser.payload.rsplit("\n\nRuntime request fingerprint: ", 1)
    assert body == prompt.strip()
    import hashlib
    assert fingerprint == hashlib.sha256(body.encode()).hexdigest()
    assert len(browser.transaction_script) < 131072
    assert not browser.payload_path.exists()


@pytest.mark.parametrize("rendered_user_count", [0, 1])
def test_transaction_waits_for_empty_fresh_chat_and_new_answer(tmp_path, rendered_user_count):
    browser = FakeBrowser()
    provider = ChatGPTBrowserProvider(browser=browser, poll_interval_seconds=0.1, stable_seconds=0.5)
    payload = tmp_path/"request.txt"
    payload.write_text("hello")
    script = provider._transaction_script(payload, 5)
    initial = {"url":"https://chatgpt.com/c/old","user_count":9,"assistant_count":9,"composer_selector":"#prompt-textarea","composer_text":"","generating":False,"send_ready":True}
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
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False,"send_ready":True}
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
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False,"send_ready":True}
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
            chunk = json.loads(expr.split("false, ", 1)[1].split(" );}, 0)", 1)[0])
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
    fresh = {"url":"https://chatgpt.com/","user_count":0,"assistant_count":0,"composer_selector":"#prompt-textarea","composer_text":"","generating":False,"send_ready":True}
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
                self.buffer += json.loads(expression.split("false, ", 1)[1].split(" );}, 0)", 1)[0])
                return True
            if expression == _SEND_EXPRESSION:
                self.sends += 1
                self.url = "https://chatgpt.com/c/durable-test"
                self.pending_answer = (f"answer-{self.sends}", f"reply to {self.buffer.split(chr(10)+chr(10)+'Runtime request fingerprint: ', 1)[0]}")
                self.buffer = ""
                if self.fail_after_send:
                    raise RuntimeError("browser process disconnected after submission")
                return True
            if expression == _CHATGPT_STATE_EXPRESSION:
                if getattr(self, "state_timeout_remaining", 0):
                    self.state_timeout_remaining -= 1
                    now[0] += 5
                    raise RuntimeError("Runtime.evaluate timed out")
                if self.pending_answer and not self.generate_forever:
                    self.answer_id, self.answer = self.pending_answer
                    self.pending_answer = None
                    self.count = 1
                return {"url": self.url, "user_count": self.count,
                        "assistant_count": self.count, "last_assistant_id": self.answer_id,
                        "last_assistant_text": self.answer,
                        "composer_selector": "#prompt-textarea", "composer_text": self.buffer,
                        "generating": bool(self.pending_answer), "send_ready": bool(self.buffer)}
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



def test_virtualized_visible_count_drop_accepts_owned_assistant_after_user(tmp_path):
    import hashlib
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    provider=conversation_provider(FakeBrowser(),tmp_path)
    outbound=provider._request_text("virtualized response",{})
    payload=tmp_path/"virtualized.txt";payload.write_text(outbound)
    journal={"schema_version":1,"session_id":provider.session_id,
        "conversation_url":"https://chatgpt.com/c/durable-test",
        "pending":{"request_id":"virtualized-request","outbound":outbound,
            "payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
            "baseline_user_count":5,"baseline_assistant_count":5,
            "baseline_assistant_id":"recycled-wrapper","stage":"WAITING_RESPONSE",
            "submission_acknowledged":True}}
    provider.conversation_state_path.write_text(json.dumps(journal))
    now=[0.0]
    class Clock:
        def monotonic(self): now[0]+=.3;return now[0]
        def sleep(self,seconds): now[0]+=seconds
    def js(expr):
        if expr==_CHATGPT_STATE_EXPRESSION:
            return {"url":journal["conversation_url"],"user_count":5,
                "assistant_count":4,"last_assistant_id":"recycled-wrapper",
                "last_assistant_text":"owned new answer","last_user_text":outbound,
                "assistant_after_last_user":True,"generating":False,
                "composer_selector":"#prompt-textarea","composer_text":"","send_ready":False}
        return True
    scope={"js":js,"time":Clock()}
    exec(provider._transaction_script(payload,5).replace("import time\n",""),scope)
    assert scope["result"]["response"]=="owned new answer"
    assert scope["result"]["request_id"]=="virtualized-request"
    assert json.loads(provider.conversation_state_path.read_text())["pending"] is None

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


def test_queued_insert_reselects_editor_after_react_replaces_focused_node(tmp_path):
    import shutil, subprocess
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the composer insertion expression")
    provider = conversation_provider(FakeBrowser(), tmp_path)
    payload = tmp_path / "input.txt"
    payload.write_text("hello")
    captured = []
    def js(expression):
        if expression == _CHATGPT_STATE_EXPRESSION:
            return {"url": "https://chatgpt.com/", "composer_selector": "main [contenteditable]"}
        if "document.execCommand('insertText'" in expression:
            captured.append(expression)
            return False
        return True
    with pytest.raises(AssertionError, match="insertText was rejected"):
        exec(provider._transaction_script(payload, 5), {"js": js})
    fixture = """
    let current={isContentEditable:true,getClientRects:()=>[{}],focus:()=>{}};
    let focused=false, scheduled=null, inserted='';
    global.document={activeElement:{tagName:'BODY'},querySelectorAll:()=>[current],
      execCommand:(command,unused,text)=>{inserted=text}};
    global.setTimeout=fn=>{scheduled=fn};
    """
    body = fixture + "const result=" + captured[0] + """;
    current={isContentEditable:true,getClientRects:()=>[{}],focus:()=>{focused=true}};
    scheduled(); console.log(JSON.stringify({result,focused,inserted}));
    """
    result = subprocess.run([node, "-e", body], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"result": True, "focused": True, "inserted": "hello"}

def test_unbound_request_prefers_a_conversation_over_loading_homepage():
    class Browser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.prefixes = []
        def adopt_existing_tab(self, session_id, url_prefix):
            self.prefixes.append(url_prefix)
            return super().adopt_existing_tab(session_id, url_prefix)
    browser = Browser()
    provider = ChatGPTBrowserProvider(browser=browser, response_timeout_seconds=10)
    assert asyncio.run(provider.complete("Recover a fresh request")) == "runtime browser answer"
    assert browser.prefixes == ["https://chatgpt.com/c/"]


def test_unbound_request_can_start_when_no_conversation_exists():
    class Browser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.prefixes = []
        def adopt_existing_tab(self, session_id, url_prefix):
            self.prefixes.append(url_prefix)
            if url_prefix.endswith("/c/"):
                raise RuntimeError("no existing browser tab matches requested URL")
            return super().adopt_existing_tab(session_id, url_prefix)
    browser = Browser()
    provider = ChatGPTBrowserProvider(browser=browser, response_timeout_seconds=10)
    assert asyncio.run(provider.complete("First conversation")) == "runtime browser answer"
    assert browser.prefixes == ["https://chatgpt.com/c/", "https://chatgpt.com/"]


def test_unbound_attach_timeout_is_not_hidden_by_adopting_another_tab():
    class Browser(FakeBrowser):
        def adopt_existing_tab(self, session_id, url_prefix):
            raise RuntimeError("Runtime.evaluate timed out")
    browser = Browser()
    provider = ChatGPTBrowserProvider(browser=browser, response_timeout_seconds=10)
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    with pytest.raises(ChatGPTBrowserProviderError, match="Runtime.evaluate timed out"):
        asyncio.run(provider.complete("Keep the failed attachment visible"))
    assert browser.transaction_script == ""


@pytest.mark.parametrize("payload_present", [False, True])
def test_reload_position_id_cannot_complete_an_unmatched_pending_payload(tmp_path, payload_present):
    import hashlib
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    provider = conversation_provider(FakeBrowser(), tmp_path)
    payload = tmp_path / "pending.txt"
    payload.write_text("pending request")
    journal = {"schema_version": 1, "session_id": provider.session_id,
               "conversation_url": "https://chatgpt.com/c/durable-test",
               "pending": {"request_id": "pending-test", "outbound": "pending request",
                           "payload_sha256": hashlib.sha256(b"pending request").hexdigest(),
                           "baseline_user_count": 5, "baseline_assistant_count": 5,
                           "baseline_assistant_id": "fallback-turn-18:1:assistant",
                           "submission_acknowledged": True, "stage": "WAITING_RESPONSE"}}
    provider.conversation_state_path.write_text(json.dumps(journal))
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += .3; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        if expr == _SEND_EXPRESSION:
            raise AssertionError("pending request must never be resent")
        if expr == _CHATGPT_STATE_EXPRESSION:
            return {"url": journal["conversation_url"], "user_count": 5, "assistant_count": 5,
                    "last_assistant_id": "fallback-turn-4:1:assistant",
                    "last_assistant_text": "An old answer after reload",
                    "last_user_text": "pending request" if payload_present else "previous request",
                    "generating": False, "composer_selector": "#prompt-textarea"}
        return True
    scope = {"js": js, "time": Clock()}
    if payload_present:
        exec(provider._transaction_script(payload, 5).replace("import time"+chr(10), ""), scope)
        assert scope["result"]["response"] == "An old answer after reload"
        assert json.loads(provider.conversation_state_path.read_text())["pending"] is None
    else:
        with pytest.raises(RuntimeError, match="response exceeded"):
            exec(provider._transaction_script(payload, 5).replace("import time"+chr(10), ""), scope)
        assert json.loads(provider.conversation_state_path.read_text())["pending"]["request_id"] == "pending-test"


def test_work_markdown_changes_do_not_hide_the_request_fingerprint(tmp_path):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    provider = conversation_provider(FakeBrowser(), tmp_path)
    outbound = provider._request_text('[{"url":"https://x.com/reader/status/123"}]', {})
    marker = outbound.split("Runtime request fingerprint: ", 1)[1]
    payload = tmp_path / "request.txt"
    payload.write_text(outbound)
    now = [0.0]
    sent = [False]
    class Clock:
        def monotonic(self): now[0] += .3; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        from agent_runtime.llm.chatgpt_browser import _SEND_EXPRESSION
        if "document.execCommand('insertText'" in expr:
            return True
        if expr == _SEND_EXPRESSION:
            sent[0] = True
            return True
        if expr == _CHATGPT_STATE_EXPRESSION:
            return {"url":"https://chatgpt.com/c/marker-test" if sent[0] else "https://chatgpt.com/",
                    "user_count":int(sent[0]), "assistant_count":int(sent[0]),
                    "composer_selector":"#prompt-textarea", "composer_text":outbound,
                    "send_ready":True, "generating":False,
                    "last_user_text":'Rendered inline link card Runtime request fingerprint: '+marker if sent[0] else '',
                    "last_assistant_text":"Bound response" if sent[0] else ""}
        return True
    scope = {"js":js,"time":Clock()}
    exec(provider._transaction_script(payload, 5).replace("import time"+chr(10), ""), scope)
    assert scope["result"]["response"] == "Bound response"

class RefreshConversationBrowser(ConversationBrowser):
    def __init__(self):
        super().__init__()
        self.navigation_requests = []
        self.replacements = []
        self.unowned_generation = False
    def invoke(self, capability_id, payload):
        self.navigation_requests.append(dict(payload))
        return super().invoke(capability_id, payload)
    def evaluate(self, session_id, expression):
        return {"url":self.url, "generating":self.unowned_generation}
    def replace_owned_session(self, session_id, *, expected_url):
        self.replacements.append((session_id, expected_url))
        self.url = expected_url
        return {"status":"OWNED_RENDERER_REPLACED","url":expected_url}

def refreshed_provider(browser, tmp_path):
    return ChatGPTBrowserProvider(browser=browser, process_lock_path=tmp_path/"profile.lock",
        poll_interval_seconds=.1, stable_seconds=.5, response_timeout_seconds=5, refresh_after_turns=2)

def test_idle_page_refresh_preserves_conversation_and_next_response(tmp_path):
    browser=RefreshConversationBrowser()
    provider=refreshed_provider(browser,tmp_path)
    assert asyncio.run(provider.complete("first"))=="reply to first"
    assert asyncio.run(provider.complete("second"))=="reply to second"
    assert asyncio.run(refreshed_provider(browser,tmp_path).complete("third"))=="reply to third"
    assert [r["reuse_current"] for r in browser.navigation_requests]==[True]
    assert browser.replacements==[("chatgpt-cognition","https://chatgpt.com/c/durable-test")]
    assert browser.new_chats==0
    assert browser.sends==3
    journal=json.loads(provider.conversation_state_path.read_text())
    assert journal["conversation_url"]=="https://chatgpt.com/c/durable-test"
    assert journal["page_turns_since_refresh"]==1
    assert journal["page_refresh_count"]==1
    assert journal["last_page_refresh"]["after_request_id"]
    assert journal["pending"] is None

def test_pending_response_at_refresh_boundary_is_recovered_without_refresh(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser=RefreshConversationBrowser();provider=refreshed_provider(browser,tmp_path)
    asyncio.run(provider.complete("first"));asyncio.run(provider.complete("second"))
    browser.generate_forever=True
    with pytest.raises(ChatGPTBrowserProviderError):
        asyncio.run(provider.complete("third"))
    # The failed third request was created after an idle refresh. A later
    # resource threshold must not refresh its in-flight browser page.
    journal=json.loads(provider.conversation_state_path.read_text())
    journal["page_turns_since_refresh"]=99
    provider.conversation_state_path.write_text(json.dumps(journal))
    browser.generate_forever=False
    assert asyncio.run(refreshed_provider(browser,tmp_path).complete("third"))=="reply to third"
    assert browser.navigation_requests[-1]["reuse_current"] is True
    assert len(browser.replacements)==1
    assert browser.sends==3
    assert json.loads(provider.conversation_state_path.read_text())["page_refresh_count"]==1

def test_unowned_generation_is_preserved_at_idle_refresh_boundary(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser=RefreshConversationBrowser();provider=refreshed_provider(browser,tmp_path)
    asyncio.run(provider.complete("first"));asyncio.run(provider.complete("second"))
    browser.unowned_generation=True
    with pytest.raises(ChatGPTBrowserProviderError,match="unowned generation"):
        asyncio.run(provider.complete("third"))
    assert len(browser.navigation_requests)==1
    assert browser.replacements==[]
    assert browser.sends==2
    assert json.loads(provider.conversation_state_path.read_text())["page_turns_since_refresh"]==2

def test_old_completed_journal_refreshes_once_without_resetting_identity(tmp_path):
    browser=RefreshConversationBrowser();provider=refreshed_provider(browser,tmp_path)
    asyncio.run(provider.complete("first"))
    journal=json.loads(provider.conversation_state_path.read_text())
    journal.pop("page_turns_since_refresh")
    provider.conversation_state_path.write_text(json.dumps(journal))
    assert asyncio.run(refreshed_provider(browser,tmp_path).complete("second"))=="reply to second"
    assert browser.replacements==[("chatgpt-cognition","https://chatgpt.com/c/durable-test")]
    assert json.loads(provider.conversation_state_path.read_text())["conversation_url"]==journal["conversation_url"]
    assert browser.new_chats==0

def test_invalid_refresh_counter_is_visible_and_does_not_submit(tmp_path):
    from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProviderError
    browser=RefreshConversationBrowser();provider=refreshed_provider(browser,tmp_path)
    asyncio.run(provider.complete("first"))
    journal=json.loads(provider.conversation_state_path.read_text())
    journal["page_turns_since_refresh"]="bad"
    provider.conversation_state_path.write_text(json.dumps(journal))
    with pytest.raises(ChatGPTBrowserProviderError,match="page-turn counter"):
        asyncio.run(provider.complete("second"))
    assert browser.sends==1

@pytest.mark.parametrize("state_timeouts", [2, None])
def test_restoring_composer_retries_only_read_timeouts_before_submitting(tmp_path, state_timeouts):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    provider=conversation_provider(FakeBrowser(),tmp_path)
    payload=tmp_path/"request.txt";payload.write_text("do not submit until ready")
    now=[0.0];reads=[0];other_calls=[]
    class Clock:
        def monotonic(self): return now[0]
        def sleep(self, seconds): now[0]+=seconds
    def js(expr):
        if expr==_CHATGPT_STATE_EXPRESSION:
            reads[0]+=1
            now[0]+=5
            if state_timeouts is None or reads[0]<=state_timeouts:
                raise RuntimeError("Runtime.evaluate timed out")
            # Authentication failures after restoration must also stay visible.
            return {"login_required":True}
        other_calls.append(expr)
        raise AssertionError("must not touch composer before readiness")
    scope={"js":js,"time":Clock()}
    error=RuntimeError if state_timeouts is None else AssertionError
    message="state could not be read within 30s" if state_timeouts is None else "not authenticated"
    with pytest.raises(error,match=message):
        exec(provider._transaction_script(payload,5,page_refreshed=True).replace("import time"+chr(10),""),scope)
    assert reads[0]>=3
    assert other_calls==[]
    assert not provider.conversation_state_path.exists()

def test_post_refresh_state_timeout_recovers_next_answer_without_duplicate_send(tmp_path):
    browser=RefreshConversationBrowser();provider=refreshed_provider(browser,tmp_path)
    asyncio.run(provider.complete("first"));asyncio.run(provider.complete("second"))
    browser.state_timeout_remaining=2
    assert asyncio.run(refreshed_provider(browser,tmp_path).complete("third"))=="reply to third"
    assert browser.replacements == [
        ("chatgpt-cognition", "https://chatgpt.com/c/durable-test")
    ]
    assert all(
        request["reuse_current"] is True
        for request in browser.navigation_requests
    )
    assert browser.sends==3
    assert browser.new_chats==0
    journal=json.loads(provider.conversation_state_path.read_text())
    assert journal["pending"] is None
    assert journal["page_refresh_count"]==1


@pytest.mark.parametrize("acknowledged,delivered", [(False,False),(False,True),(True,False)])
def test_unsent_pending_turn_is_restored_only_with_unchanged_live_baseline(tmp_path, acknowledged, delivered):
    import hashlib
    from agent_runtime.llm.chatgpt_browser import (
        _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION, _FOCUS_COMPOSER_EXPRESSION,
    )
    provider=conversation_provider(FakeBrowser(),tmp_path)
    outbound="restore this exact pending request"
    payload=tmp_path/"pending.txt";payload.write_text(outbound)
    journal={"schema_version":1,"session_id":provider.session_id,
        "conversation_url":"https://chatgpt.com/c/durable-test",
        "pending":{"request_id":"original-request","outbound":outbound,
            "payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
            "baseline_user_count":5,"baseline_assistant_count":5,
            "baseline_assistant_id":"original-answer","stage":"WAITING_RESPONSE",
            "submission_acknowledged":acknowledged}}
    provider.conversation_state_path.write_text(json.dumps(journal))
    now=[0.0];sends=[0];buffer=["partial stale draft"];accepted=[delivered]
    class Clock:
        def monotonic(self): now[0]+=.3;return now[0]
        def sleep(self, seconds): now[0]+=seconds
    def js(expr):
        if expr==_FOCUS_COMPOSER_EXPRESSION:
            buffer[0]="";return True
        if "document.execCommand('insertText'" in expr:
            buffer[0]+=json.loads(expr.split("false, ",1)[1].split(" );}, 0)",1)[0])
            return True
        if expr==_SEND_EXPRESSION:
            assert buffer[0]==outbound
            sends[0]+=1;accepted[0]=True;buffer[0]="";return True
        if expr==_CHATGPT_STATE_EXPRESSION:
            return {"url":journal["conversation_url"],"user_count":5+int(accepted[0]),
                "assistant_count":5+int(accepted[0]),
                "last_assistant_id":"new-answer" if accepted[0] else "original-answer",
                "last_assistant_text":"pending reply" if accepted[0] else "old reply",
                "last_user_text":outbound if accepted[0] else "previous request",
                "composer_selector":"#prompt-textarea","composer_text":buffer[0],
                "send_ready":True,"generating":False}
        return True
    scope={"js":js,"time":Clock()}
    if acknowledged and not delivered:
        with pytest.raises(RuntimeError,match="response exceeded"):
            exec(provider._transaction_script(payload,5).replace("import time"+chr(10),""),scope)
        assert sends[0]==0
        assert json.loads(provider.conversation_state_path.read_text())["pending"]["request_id"]=="original-request"
    else:
        exec(provider._transaction_script(payload,5).replace("import time"+chr(10),""),scope)
        assert sends[0]==int(not delivered)
        assert scope["result"]["request_id"]=="original-request"
        assert scope["result"]["response"]=="pending reply"
        assert json.loads(provider.conversation_state_path.read_text())["pending"] is None

def test_completed_turn_receipt_survives_caller_disconnect_without_resend(tmp_path):
    import hashlib
    browser = ConversationBrowser()
    provider = conversation_provider(browser, tmp_path)
    outbound = provider._request_text("same recovered request", {})
    provider.conversation_state_path.parent.mkdir(parents=True, exist_ok=True)
    provider.conversation_state_path.write_text(json.dumps({
        "schema_version": 1, "session_id": provider.session_id,
        "conversation_url": "https://chatgpt.com/c/preserved",
        "pending": None,
        "last_completed": {"response": "durable answer",
            "url": "https://chatgpt.com/c/preserved", "request_id": "accepted-once",
            "payload_sha256": hashlib.sha256(outbound.encode()).hexdigest()}
    }))
    assert asyncio.run(provider.complete("same recovered request")) == "durable answer"
    assert provider.last_metadata["recovered"] is True
    assert browser.sends == 0

@pytest.mark.parametrize("owned,generating,rejected", [(True,False,True),(False,False,False),(True,True,False)])
def test_explicit_oversize_rejection_requires_owned_request_and_no_generation(tmp_path, owned, generating, rejected):
    import hashlib
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    provider = conversation_provider(ConversationBrowser(), tmp_path)
    payload = tmp_path / "payload.txt"
    text = provider._request_text("owned oversize request", {})
    payload.write_text(text)
    journal = provider.conversation_state_path
    journal.write_text(json.dumps({"schema_version":1,"session_id":provider.session_id,
        "conversation_url":"https://chatgpt.com/c/rejection",
        "pending":{"request_id":"owned-rejected","outbound":text,
            "payload_sha256":hashlib.sha256(text.encode()).hexdigest(),
            "baseline_user_count":1,"baseline_assistant_count":1,
            "baseline_assistant_id":"older-answer","submission_acknowledged":True,
            "stage":"WAITING_RESPONSE"}}))
    state = {"url":"https://chatgpt.com/c/rejection","composer_selector":"#prompt-textarea",
             "composer_text":"","user_count":1,"assistant_count":0,
             "last_user_text":text if owned else "another request",
             "last_assistant_text":"","message_too_long":True,"generating":generating}
    now = [0.0]
    class Clock:
        def monotonic(self): now[0] += 0.1; return now[0]
        def sleep(self, seconds): now[0] += seconds
    def js(expr):
        assert expr == _CHATGPT_STATE_EXPRESSION, "Recovery must never insert or submit"
        return state
    with pytest.raises(RuntimeError, match="MESSAGE_TOO_LONG" if rejected else "response exceeded"):
        exec(provider._transaction_script(payload, 5).replace("import time\n",""),
             {"js":js,"time":Clock()})
    after = json.loads(journal.read_text())
    assert bool(after["pending"]) is not rejected
    archives = list(tmp_path.glob("*rejected-owned-rejected.json"))
    assert bool(archives) is rejected
    if rejected:
        assert json.loads(archives[0].read_text())["outbound"] == text


@pytest.mark.parametrize("gc_fails", [False, True])
def test_page_gc_runs_at_safe_boundaries_and_cannot_lose_durable_answer(tmp_path, gc_fails):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    provider = ChatGPTBrowserProvider(browser=FakeBrowser(), process_lock_path=tmp_path/'profile.lock',
        conversation_state_path=tmp_path/'journal.json', poll_interval_seconds=.1, stable_seconds=.5)
    payload = tmp_path/'request.txt'; payload.write_text('hello')
    fresh = dict(url='https://chatgpt.com/', user_count=0, assistant_count=0,
        composer_selector='#prompt-textarea', composer_text='', generating=False, send_ready=True)
    typed = {**fresh, 'composer_text':'hello'}
    response = {**fresh, 'url':'https://chatgpt.com/c/new', 'assistant_count':1,
        'last_assistant_text':'durable answer'}
    states = iter([fresh, typed, typed, typed, response, response, response])
    now = [0.0]; sends=[]; gc=[]
    class Clock:
        def monotonic(self): now[0]+=.3; return now[0]
        def sleep(self, seconds): now[0]+=seconds
    def js(expr):
        if expr == _CHATGPT_STATE_EXPRESSION: return next(states)
        if expr == _SEND_EXPRESSION: sends.append(True)
        return True
    def cdp(method, **params):
        assert method == 'HeapProfiler.collectGarbage'
        assert params['_response_timeout'] == 10.0
        if gc:
            durable = json.loads(provider.conversation_state_path.read_text())
            assert durable['pending'] is None
            assert durable['last_completed']['response'] == 'durable answer'
        gc.append(method)
        if gc_fails: raise TimeoutError('collection unavailable')
    scope = {'js':js, 'cdp':cdp, 'time':Clock()}
    exec(provider._transaction_script(payload,5).replace('import time\n',''),scope)
    assert len(gc) == 2 and len(sends) == 1
    assert scope['result']['response'] == 'durable answer'
    assert scope['result']['page_garbage_collected'] is (not gc_fails)


def test_page_gc_does_not_interrupt_unowned_generation(tmp_path):
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
    provider = ChatGPTBrowserProvider(browser=FakeBrowser(), process_lock_path=tmp_path/'profile.lock',
        conversation_state_path=tmp_path/'journal.json')
    payload=tmp_path/'request.txt';payload.write_text('hello')
    state=dict(url='https://chatgpt.com/',user_count=0,assistant_count=0,
        composer_selector='#prompt-textarea',composer_text='',generating=True,send_ready=False)
    calls=[]
    with pytest.raises(AssertionError, match='unowned generation'):
        exec(provider._transaction_script(payload,5),{
            'js':lambda expression:state, 'cdp':lambda *args,**kwargs:calls.append(args)})
    assert calls == []
