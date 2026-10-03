import hashlib
import json
from pathlib import Path
import pytest
from agent_runtime.llm.chatgpt_browser import (ChatGPTBrowserProvider,_CHATGPT_STATE_EXPRESSION,
    _NEW_CHAT_EXPRESSION,_FOCUS_COMPOSER_EXPRESSION,_SEND_EXPRESSION)
from test_chatgpt_browser_provider import FakeBrowser

@pytest.mark.parametrize("interruption", [None,"before_click","after_click"])
@pytest.mark.parametrize("root_url",["https://chatgpt.com/","https://chatgpt.com/?model=gpt-5"])
def test_explicit_capacity_rollover_archives_before_input_and_recovers_interruptions(tmp_path,interruption,root_url):
    p=ChatGPTBrowserProvider(browser=FakeBrowser(),conversation_state_path=tmp_path/"conversation.json",
        poll_interval_seconds=.1,stable_seconds=.5)
    outbound=p._request_text("continue the actual durable Ferro task",{})
    old={"schema_version":1,"session_id":p.session_id,"conversation_url":"https://chatgpt.com/c/exhausted",
        "last_completed":{"request_id":"old-receipt","response":"prior answer","payload_sha256":"prior-hash"},
        "page_turns_since_refresh":3}
    p.conversation_state_path.write_text(json.dumps(old))
    payload=tmp_path/"outbound.txt";payload.write_text(outbound)
    live={"fresh":False,"composer":"unsent old Runtime request fingerprint: "+"0"*64,"sent":False}
    calls={"new":0,"send":0};clock=[0.];fault=[interruption]
    class Clock:
        def monotonic(self):clock[0]+=.2;return clock[0]
        def sleep(self,t):clock[0]+=t
        def time(self):return 12345
    def js(expr):
        if expr==_CHATGPT_STATE_EXPRESSION:
            if live["fresh"] and fault[0]=="after_click":
                fault[0]=None;raise RuntimeError("interrupted after New chat")
            return {"url":("https://chatgpt.com/c/new" if live["sent"] else root_url) if live["fresh"] else old["conversation_url"],
                "composer_selector":"#prompt-textarea","composer_text":live["composer"],"send_ready":True,
                "user_count":int(live["sent"]) if live["fresh"] else 4,
                "assistant_count":int(live["sent"]) if live["fresh"] else 4,
                "last_assistant_id":"new-answer" if live["sent"] else "old-answer",
                "last_assistant_text":"actual answer" if live["sent"] else "old answer",
                "last_user_text":outbound if live["sent"] else "old",
                "generating":False,"conversation_exhausted":not live["fresh"]}
        if expr==_NEW_CHAT_EXPRESSION:
            calls["new"]+=1
            durable=json.loads(p.conversation_state_path.read_text())
            assert durable["conversation_transition"]["stage"]=="NEW_CHAT_REQUESTED"
            archive=Path(durable["conversation_transition"]["archive"])
            assert archive.stat().st_mode & 0o777 == 0o600
            saved=json.loads(archive.read_text())
            assert saved["conversation"]==old
            assert saved["unsent_composer"].startswith("unsent old")
            assert saved["continuing_outbound"]==outbound
            if fault[0]=="before_click":
                fault[0]=None;raise RuntimeError("interrupted before New chat")
            live.update(fresh=True,composer="")
            return True
        if expr==_FOCUS_COMPOSER_EXPRESSION:return True
        if "document.execCommand('insertText'" in expr:
            live["composer"]=outbound;return True
        if expr==_SEND_EXPRESSION:
            calls["send"]+=1
            assert live["composer"]==outbound
            live.update(sent=True,composer="")
            return True
        raise AssertionError("unexpected page action")
    scope={"js":js,"time":Clock()}
    script=p._transaction_script(payload,5).replace("import time\n","")
    if interruption:
        with pytest.raises(RuntimeError,match="interrupted"):exec(script,scope)
        assert calls["send"]==0
        assert json.loads(p.conversation_state_path.read_text())["conversation_transition"]
    exec(script,scope)
    final=json.loads(p.conversation_state_path.read_text())
    assert final["session_id"]==old["session_id"]
    assert final["conversation_url"]=="https://chatgpt.com/c/new"
    assert final["conversation_transition"] is None and len(final["conversation_lineage"])==1
    assert final["conversation_lineage"][0]["old_url"]==old["conversation_url"]
    assert final["conversation_lineage"][0]["new_url"]==final["conversation_url"]
    assert final["last_completed"]["payload_sha256"]==hashlib.sha256(outbound.encode()).hexdigest()
    assert scope["result"]["conversation_rolled_over"] is True and scope["result"]["conversation_lineage_depth"]==1
    assert calls["send"]==1

@pytest.mark.parametrize("case",["pending_answer","unowned_generation","other_conversation"])
def test_rollover_preserves_pending_answers_and_refuses_unowned_pages(tmp_path,case):
    p=ChatGPTBrowserProvider(browser=FakeBrowser(),conversation_state_path=tmp_path/"state.json",
        poll_interval_seconds=.1,stable_seconds=.5)
    outbound=p._request_text("request",{})
    journal={"schema_version":1,"session_id":p.session_id,"conversation_url":"https://chatgpt.com/c/owned"}
    if case=="pending_answer":
        journal["pending"]={"request_id":"accepted","outbound":outbound,"payload_sha256":"sha",
            "baseline_user_count":1,"baseline_assistant_count":1,"baseline_assistant_id":"old",
            "submission_acknowledged":True,"stage":"WAITING_RESPONSE"}
    p.conversation_state_path.write_text(json.dumps(journal));payload=tmp_path/"request";payload.write_text(outbound)
    clock=[0.]
    class Clock:
        def monotonic(self):clock[0]+=.2;return clock[0]
        def sleep(self,t):clock[0]+=t
    def js(expr):
        assert expr==_CHATGPT_STATE_EXPRESSION,"capacity must not replace accepted requests or unowned pages"
        return {"url":"https://chatgpt.com/c/other" if case=="other_conversation" else journal["conversation_url"],
            "composer_selector":"#prompt-textarea","composer_text":"","send_ready":False,
            "generating":case=="unowned_generation","conversation_exhausted":True,
            "user_count":2,"assistant_count":2,"last_user_text":outbound,
            "last_assistant_id":"new","last_assistant_text":"accepted response"}
    scope={"js":js,"time":Clock()}
    script=p._transaction_script(payload,5).replace("import time\n","")
    if case=="pending_answer":
        exec(script,scope)
        assert scope["result"]["request_id"]=="accepted"
        assert scope["result"]["response"]=="accepted response"
    else:
        with pytest.raises(AssertionError):exec(script,scope)
        assert json.loads(p.conversation_state_path.read_text())==journal
    assert not list(tmp_path.glob("*-exhausted-*.json"))

def test_resume_transition_never_navigates_back_to_exhausted_carrier(tmp_path):
    class Browser(FakeBrowser):
        def invoke(self,*args):pytest.fail("must not navigate back to exhausted carrier")
        def adopt_existing_tab(self,*args):pytest.fail("must preserve bound task target")
    browser=Browser()
    p=ChatGPTBrowserProvider(browser=browser,conversation_state_path=tmp_path/"state.json")
    payload=p._request_text("current",{})
    p._run_turn_once(payload,5,{"conversation_url":"https://chatgpt.com/c/exhausted",
        "conversation_transition":{"stage":"NEW_CHAT_REQUESTED"},"page_turns_since_refresh":999})
    assert browser.payload==payload
