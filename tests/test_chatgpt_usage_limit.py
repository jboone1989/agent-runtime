import hashlib, json, subprocess
import pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, ChatGPTBrowserProviderError, _CHATGPT_STATE_EXPRESSION

@pytest.mark.parametrize("accepted", [True, False, None])
def test_native_usage_limit_fails_fast_preserving_original_receipt(tmp_path, accepted):
    from test_chatgpt_browser_provider import conversation_provider, FakeBrowser
    p=conversation_provider(FakeBrowser(),tmp_path)
    outbound=p._request_text("original request", {})
    journal={"schema_version":1,"session_id":p.session_id,"conversation_url":"https://chatgpt.com/c/owned",
             "last_completed":{"request_id":"previous","response":"previous answer"}}
    pending={"request_id":"original","outbound":outbound,"payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
             "submission_acknowledged":accepted,"stage":"WAITING_RESPONSE"}
    if accepted is not None: journal["pending"]=pending
    p.conversation_state_path.write_text(json.dumps(journal))
    path=tmp_path/"payload.txt";path.write_text(outbound)
    reads=[]
    def js(expr):
        assert expr == _CHATGPT_STATE_EXPRESSION, "Limit must not click Retry/Send, focus, collect or change tabs"
        reads.append(expr)
        return {"url":journal["conversation_url"],"usage_limited":True}
    with pytest.raises(RuntimeError,match="CHATGPT_WEB_USAGE_LIMIT"):
        exec(p._transaction_script(path,180),{"js":js})
    observed=p._load_conversation()
    assert len(reads)==1
    assert observed.get("pending")==journal.get("pending")
    assert observed["last_completed"]==journal["last_completed"]
    assert observed["conversation_url"]==journal["conversation_url"]
    assert observed["last_usage_limit"]["pending_preserved"] is (accepted is not None)
    assert observed["last_usage_limit"]["request_id"] == ("original" if accepted is not None else None)

def test_usage_limit_is_not_renderer_failure_or_new_conversation(tmp_path,monkeypatch):
    class Browser:
        def __getattr__(self,name):pytest.fail(f"usage limit may not invoke browser recovery: {name}")
    p=ChatGPTBrowserProvider(browser=Browser(),conversation_state_path=tmp_path/"state.json")
    calls=[]
    def turn(*args):calls.append(args);raise RuntimeError("CHATGPT_WEB_USAGE_LIMIT: native page limit")
    monkeypatch.setattr(p,"_run_turn_once",turn)
    with pytest.raises(RuntimeError,match="CHATGPT_WEB_USAGE_LIMIT"):
        p._run_turn("request",180,{})
    assert len(calls)==1

def test_usage_limit_prevents_scheduled_page_refresh(tmp_path):
    class Browser:
        def evaluate(self,sid,expr):
            assert expr==_CHATGPT_STATE_EXPRESSION
            return {"usage_limited":True}
        def replace_owned_session(self,*args,**kwargs):pytest.fail("must not replace quota-limited page")
    p=ChatGPTBrowserProvider(browser=Browser(),conversation_state_path=tmp_path/"state.json")
    with pytest.raises(ChatGPTBrowserProviderError,match="CHATGPT_WEB_USAGE_LIMIT"):
        p._run_turn_once("request",180,{"conversation_url":"https://chatgpt.com/c/owned","page_turns_since_refresh":8})

@pytest.mark.parametrize("text,visible,expected", [
    ("You've hit your limit. Please try again later.",True,True),
    ("You’ve hit your limit. Please try again later.",True,True),
    ("You've hit your limit. Please try again later.",False,False),
    ("Unknown error Retry",True,False),
    ("You have reached the maximum length for this conversation",True,False),
    ("Quoted text: You've hit your limit. Please try again later.",True,False),
])
def test_usage_limit_requires_exact_visible_native_alert(text,visible,expected):
    script = """
const input=JSON.parse(process.argv[1]);
const alert={innerText:input.text,textContent:input.text,getClientRects:()=>input.visible?[1]:[]};
global.document={querySelectorAll:(selector)=>selector==='[role="alert"]'?[alert]:[]};
global.location={href:'https://chatgpt.com/c/owned'};
const state=eval(process.argv[2]);
process.stdout.write(JSON.stringify(state.usage_limited));
"""
    r=subprocess.run(["node","-e",script,json.dumps({"text":text,"visible":visible}),_CHATGPT_STATE_EXPRESSION],text=True,capture_output=True,check=True)
    assert json.loads(r.stdout) is expected
