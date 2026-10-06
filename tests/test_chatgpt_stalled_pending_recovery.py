import hashlib,json,pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider

@pytest.mark.parametrize("failure", ["Page.enable timed out after 5s", 'ChatGPT Web response exceeded 180s: {"generating": false, "poll_timeout_count": 0}'])
def test_dead_owned_renderer_reconciles_original_pending_without_resubmission(tmp_path,monkeypatch,failure):
    class Browser:
        def __init__(self): self.replacements=[]
        def replace_owned_session(self,sid,*,expected_url): self.replacements.append((sid,expected_url))
    browser=Browser()
    p=ChatGPTBrowserProvider(browser=browser,conversation_state_path=tmp_path/"state.json")
    outbound=p._request_text("original request",{})
    pending={"outbound":outbound,"payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
        "request_id":"original","submission_acknowledged":True,"submitted_user_id":"owned"}
    journal={"schema_version":1,"session_id":p.session_id,
        "conversation_url":"https://chatgpt.com/c/original","pending":pending}
    p.conversation_state_path.write_text(json.dumps(journal))
    before=p.conversation_state_path.read_bytes()
    calls=[]
    def turn(payload,timeout,state):
        calls.append((payload,state))
        if len(calls)==1: raise RuntimeError(failure)
        assert state["pending"]==pending
        assert p.conversation_state_path.read_bytes()==before
        return {"response":"server-stored answer","request_id":"original","recovered":True}
    monkeypatch.setattr(p,"_run_turn_once",turn)
    result=p._run_turn(outbound,5,journal)
    assert result["renderer_replaced"] and result["request_id"]=="original"
    assert calls==[(outbound,journal),(outbound,journal)]
    assert browser.replacements==[(p.session_id,journal["conversation_url"])]

@pytest.mark.parametrize("case", ["ambiguous","unbound","different_payload","second_failure"])
def test_pending_recovery_preserves_ambiguous_requests_and_propagates_second_failure(tmp_path,monkeypatch,case):
    class Browser:
        def __init__(self):self.replacements=0
        def replace_owned_session(self,*args,**kwargs):self.replacements+=1
    browser=Browser();p=ChatGPTBrowserProvider(browser=browser,conversation_state_path=tmp_path/"state.json")
    pending={"outbound":"other" if case=="different_payload" else "request",
        "submission_acknowledged":case!="ambiguous","request_id":"accepted"}
    journal={"schema_version":1,"session_id":p.session_id,"pending":pending,
        "conversation_url":None if case=="unbound" else "https://chatgpt.com/c/original"}
    p.conversation_state_path.write_text(json.dumps(journal));before=p.conversation_state_path.read_bytes();calls=[]
    def turn(*args):calls.append(args);raise RuntimeError("Page.enable timed out after 5s")
    monkeypatch.setattr(p,"_run_turn_once",turn)
    with pytest.raises(RuntimeError,match="Page.enable timed out"):p._run_turn("request",5,journal)
    assert browser.replacements==int(case=="second_failure")
    assert len(calls)==(2 if case=="second_failure" else 1)
    assert p.conversation_state_path.read_bytes()==before

@pytest.mark.parametrize("owned", [True, False])
def test_reindexed_user_wrappers_use_exact_payload_and_do_not_adopt_recycled_id(tmp_path, owned):
    from test_chatgpt_browser_provider import conversation_provider, FakeBrowser
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    p=conversation_provider(FakeBrowser(),tmp_path);outbound=p._request_text("owned request",{})
    pending={"outbound":outbound,"payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
        "request_id":"original","submission_acknowledged":True,"submitted_user_id":"fallback-turn-7:0:user",
        "baseline_user_count":5,"baseline_assistant_count":5,"baseline_assistant_id":"old"}
    journal={"schema_version":1,"session_id":p.session_id,"conversation_url":"https://chatgpt.com/c/owned","pending":pending}
    p.conversation_state_path.write_text(json.dumps(journal))
    payload=tmp_path/"payload.txt";payload.write_text(outbound);clock=[0.]
    class Clock:
        def monotonic(self):clock[0]+=.3;return clock[0]
        def sleep(self,s):clock[0]+=s
    def js(expr):
        if expr==_SEND_EXPRESSION:pytest.fail("accepted request must not resend")
        if expr==_CHATGPT_STATE_EXPRESSION:return {"url":journal["conversation_url"],"composer_selector":"editor",
            "composer_text":"","user_count":2,"assistant_count":2,"last_assistant_id":"new",
            "last_assistant_text":"owned answer","last_user_id":"fallback-turn-5:0:user" if owned else "fallback-turn-7:0:user",
            "last_user_text":outbound if owned else "another user's request","assistant_after_last_user":True,"generating":False}
        return True
    scope={"js":js,"time":Clock()}
    script=p._transaction_script(payload,5).replace("import time\\n","")
    if owned:
        exec(script,scope)
        assert scope["result"]["response"]=="owned answer" and scope["result"]["request_id"]=="original"
    else:
        with pytest.raises(RuntimeError,match="response exceeded"):exec(script,scope)
        assert p._load_conversation()["pending"]==pending
