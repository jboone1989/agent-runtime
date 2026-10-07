import json
import pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider

@pytest.mark.parametrize("pending", [None, {"request_id":"accepted", "outbound":"original accepted request",
    "payload_sha256":"original-sha", "submission_acknowledged":True}])
def test_renderer_timeout_restores_owned_tab_and_current_receipt(tmp_path, monkeypatch, pending):
    class Browser:
        def __init__(self): self.reloads=[]
        def reload_owned_session(self, session_id, *, expected_url):
            self.reloads.append((session_id,expected_url))
    browser=Browser()
    path=tmp_path/"conversation.json"
    journal={"schema_version":1,"session_id":"chatgpt-cognition",
        "conversation_url":"https://chatgpt.com/c/owned","pending":pending}
    path.write_text(json.dumps(journal))
    provider=ChatGPTBrowserProvider(browser=browser, conversation_state_path=path)
    calls=[]
    def turn(outbound,timeout,receipt):
        calls.append((outbound,receipt))
        if len(calls)==1: raise RuntimeError("Runtime.evaluate timed out after 5s")
        return {"response":"recovered answer"}
    monkeypatch.setattr(provider,"_run_turn_once",turn)
    result=provider._run_turn("same request",10,journal)
    assert result["renderer_reloaded"] is True
    expected_session = "chatgpt-cognition" if pending else "chatgpt-shared"
    assert browser.reloads==[(expected_session,"https://chatgpt.com/c/owned")]
    assert calls[1]==("same request",journal)
    assert json.loads(path.read_text())==journal

@pytest.mark.parametrize("error", ["ChatGPT Web response exceeded 180s", "ChatGPT sign-in required",
    "X authenticated identity mismatch", "Runtime.evaluate failed: syntax error"])
def test_unrelated_failures_do_not_reload(tmp_path, monkeypatch, error):
    class Browser:
        def reload_owned_session(self,*args,**kwargs): pytest.fail("unrelated failure must not reload")
    provider=ChatGPTBrowserProvider(browser=Browser(), conversation_state_path=tmp_path/"state.json")
    def fail(*args): raise RuntimeError(error)
    monkeypatch.setattr(provider,"_run_turn_once",fail)
    with pytest.raises(RuntimeError,match=error): provider._run_turn("request",10,{})

def test_second_renderer_timeout_propagates_without_reload_loop(tmp_path, monkeypatch):
    class Browser:
        def __init__(self): self.reloads=0
        def reload_owned_session(self,*args,**kwargs): self.reloads+=1
    browser=Browser()
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"schema_version":1,"session_id":"chatgpt-cognition",
        "conversation_url":"https://chatgpt.com/c/owned"}))
    provider=ChatGPTBrowserProvider(browser=browser,conversation_state_path=path)
    def fail(*args): raise RuntimeError("Runtime.evaluate timed out")
    monkeypatch.setattr(provider,"_run_turn_once",fail)
    with pytest.raises(RuntimeError,match="Runtime.evaluate timed out"): provider._run_turn("request",10,{})
    assert browser.reloads==1

@pytest.mark.parametrize("observed_url", ["https://example.com/c/owned","https://example.com/c/operator",None])
def test_owned_reload_uses_cdp_metadata_without_renderer_js_or_new_tabs(tmp_path, observed_url):
    import contextlib,io
    from types import SimpleNamespace
    from test_browser_harness_integration import _provider
    calls=[]
    def runner(argv,**kwargs):
        def cdp(method,**params):
            calls.append(method)
            assert method=="Page.reload" and params=={"ignoreCache":False}
            return {}
        scope={"list_tabs":lambda **kw: [] if observed_url is None else [
                {"targetId":"saved","url":observed_url}],
            "switch_tab":lambda target: calls.append(("switch",target)), "cdp":cdp}
        out=io.StringIO()
        with contextlib.redirect_stdout(out): exec(kwargs["input"],scope)
        return SimpleNamespace(returncode=0,stdout=out.getvalue(),stderr="")
    browser,_=_provider(tmp_path,runner)
    browser._save_target_id("owned","saved")
    if observed_url=="https://example.com/c/owned":
        result=browser.reload_owned_session("owned",expected_url=observed_url)
        assert result["target_id"]=="saved" and result["status"]=="OWNED_RENDERER_RELOADED"
        assert calls==[("switch","saved"),"Page.reload"]
    else:
        with pytest.raises(RuntimeError,match="no longer matches"):
            browser.reload_owned_session("owned",expected_url="https://example.com/c/owned")
        assert calls==[("switch","saved")]

def test_owned_renderer_replacement_moves_binding_then_retires_old_target(tmp_path):
    import contextlib,io
    from types import SimpleNamespace
    from test_browser_harness_integration import _provider
    calls=[]
    state={"current":"saved","targets":{"saved":"https://example.com/c/owned"}}
    def runner(argv,**kwargs):
        def cdp(method,**params):
            calls.append((method,dict(params)))
            if method=="Target.closeTarget":
                state["targets"].pop(params["targetId"],None)
                return {"success":True}
            if method=="Page.navigate":
                state["targets"][state["current"]]=params["url"]
                return {"frameId":"frame"}
            raise AssertionError(method)
        def switch_tab(target):
            state["current"]=target
            calls.append(("switch",target))
        def new_tab():
            state["targets"]["fresh"]="about:blank"
            return "fresh"
        def js(_expression):
            return {"url":state["targets"][state["current"]],
                    "title":"Owned","dom_ready":True}
        scope={
            "list_tabs":lambda **kw:[
                {"targetId":target,"url":url}
                for target,url in state["targets"].items()
            ],
            "new_tab":new_tab,
            "switch_tab":switch_tab,
            "cdp":cdp,
            "js":js,
        }
        out=io.StringIO()
        with contextlib.redirect_stdout(out): exec(kwargs["input"],scope)
        return SimpleNamespace(returncode=0,stdout=out.getvalue(),stderr="")
    browser,_=_provider(tmp_path,runner)
    browser._save_target_id("owned","saved")
    result=browser.replace_owned_session(
        "owned",expected_url="https://example.com/c/owned"
    )
    assert result["target_id"]=="fresh"
    assert result["replaced_target_id"]=="saved"
    assert browser._load_target_id("owned")=="fresh"
    assert calls.index(("switch","fresh")) < calls.index(
        ("Target.closeTarget",{"targetId":"saved"})
    )
    assert calls.index(
        ("Target.closeTarget",{"targetId":"saved"})
    ) < calls.index(
        ("Page.navigate",{"url":"https://example.com/c/owned","_response_timeout":20.0})
    )

@pytest.mark.parametrize("previous_retries, owned", [(0,True),(1,True),(0,False)])
def test_failed_owned_generation_retries_same_message_once(tmp_path, previous_retries, owned):
    import hashlib
    from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION, _SEND_EXPRESSION
    from test_chatgpt_browser_provider import conversation_provider, FakeBrowser
    provider=conversation_provider(FakeBrowser(),tmp_path)
    outbound=provider._request_text("same original request",{})
    marker=outbound.split("Runtime request fingerprint: ")[1]
    payload=tmp_path/"request.txt";payload.write_text(outbound)
    pending={"request_id":"same-id","outbound":outbound,"payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
        "baseline_user_count":1,"baseline_assistant_count":1,"baseline_assistant_id":"old",
        "submission_acknowledged":True,"stage":"WAITING_RESPONSE","generation_retry_count":previous_retries}
    journal={"schema_version":1,"session_id":provider.session_id,"conversation_url":"https://chatgpt.com/c/owned","pending":pending}
    provider.conversation_state_path.write_text(json.dumps(journal))
    calls=[];clock=[0.0]
    class Clock:
        def monotonic(self): clock[0]+=.3;return clock[0]
        def sleep(self,seconds): clock[0]+=seconds
    def js(expr):
        if expr==_SEND_EXPRESSION: pytest.fail("Retry must not submit a new user message")
        if expr==_CHATGPT_STATE_EXPRESSION:
            return {"url":journal["conversation_url"],"user_count":2,"assistant_count":1,
                "composer_selector":"#prompt-textarea","composer_text":outbound if previous_retries else "",
                "last_user_text":outbound if owned else "unrelated operator message",
                "last_assistant_id":"new" if calls else "old","last_assistant_text":"recovered" if calls else "old answer",
                "generating":False,"generation_error":not calls}
        if "unknown error" in expr:
            durable=json.loads(provider.conversation_state_path.read_text())
            assert durable["pending"]["generation_retry_count"]==1
            assert durable["pending"]["request_id"]=="same-id"
            assert marker in expr
            calls.append(expr);return True
        return True
    scope={"js":js,"time":Clock()}
    script=provider._transaction_script(payload,5).replace("import time\n","")
    if not owned:
        with pytest.raises(RuntimeError,match="response exceeded"): exec(script,scope)
        assert calls==[]
        assert json.loads(provider.conversation_state_path.read_text())["pending"]==pending
    elif previous_retries:
        with pytest.raises(RuntimeError,match="after same-request Retry"): exec(script,scope)
        assert calls==[]
        durable=json.loads(provider.conversation_state_path.read_text())
        assert durable["pending"] is None
        failed=durable["last_failed_request"]
        assert failed["request_id"]=="same-id" and failed["reason"]=="GENERATION_FAILED"
        archive=json.loads(__import__("pathlib").Path(failed["archive"]).read_text())
        assert archive["outbound"]==outbound and archive["status"]=="GENERATION_FAILED"
        assert __import__("pathlib").Path(failed["archive"]).stat().st_mode & 0o777 == 0o600
    else:
        exec(script,scope)
        assert scope["result"]["request_id"]=="same-id" and scope["result"]["response"]=="recovered"
        assert len(calls)==1


def test_missing_owned_target_rebinds_same_conversation_and_preserves_pending(tmp_path, monkeypatch):
    from agent_runtime.integrations.browser import BROWSER_NAVIGATE

    pending = {
        "request_id": "accepted",
        "outbound": "original accepted request",
        "payload_sha256": "original-sha",
        "browser_session_id": "chatgpt-shared",
        "submission_acknowledged": True,
        "stage": "WAITING_RESPONSE",
    }
    journal = {
        "schema_version": 1,
        "session_id": "chatgpt-cognition",
        "conversation_url": "https://chatgpt.com/c/owned",
        "pending": pending,
    }
    path = tmp_path / "conversation.json"
    path.write_text(json.dumps(journal))

    class Browser:
        def __init__(self):
            self.navigations = []
        def invoke(self, capability_id, payload):
            self.navigations.append((capability_id, dict(payload)))
            return {"ok": True, "target_rebound": True}

    browser = Browser()
    provider = ChatGPTBrowserProvider(browser=browser, conversation_state_path=path)
    calls = []

    def turn(outbound, timeout, receipt):
        calls.append((outbound, receipt))
        if len(calls) == 1:
            raise RuntimeError("{'code': -32602, 'message': 'No target with given id found'}")
        return {"response": "recovered answer"}

    monkeypatch.setattr(provider, "_run_turn_once", turn)
    result = provider._run_turn("original accepted request", 10, journal)

    assert result["response"] == "recovered answer"
    assert result["target_rebound"] is True
    assert browser.navigations == [(
        BROWSER_NAVIGATE,
        {
            "session_id": "chatgpt-shared",
            "url": "https://chatgpt.com/c/owned",
            "reuse_current": True,
        },
    )]
    assert calls[1] == ("original accepted request", journal)
    assert json.loads(path.read_text()) == journal


def test_conversation_load_failure_reloads_same_owned_conversation_once(tmp_path, monkeypatch):
    class Browser:
        def __init__(self): self.reloads=[]
        def reload_owned_session(self, session_id, *, expected_url):
            self.reloads.append((session_id, expected_url))
    browser=Browser()
    path=tmp_path/"conversation.json"
    journal={"schema_version":1,"session_id":"chatgpt-cognition",
        "conversation_url":"https://chatgpt.com/c/owned"}
    path.write_text(json.dumps(journal))
    provider=ChatGPTBrowserProvider(browser=browser,conversation_state_path=path)
    calls=[]
    def turn(*args,**kwargs):
        calls.append((args,kwargs))
        if len(calls)==1:
            raise RuntimeError("ChatGPT conversation failed to load after Retry")
        return {"response":"recovered","url":journal["conversation_url"]}
    monkeypatch.setattr(provider,"_run_turn_once",turn)
    result=provider._run_turn("request",10,journal)
    assert result["response"]=="recovered"
    assert result["renderer_reloaded"] is True
    assert browser.reloads==[(provider.browser_session_id,journal["conversation_url"])]
    assert len(calls)==2


@pytest.mark.parametrize("case,expected",[
    ("valid",True),("missing_error",False),("two_retries",False),
    ("disabled",False),("hidden",False),
])
def test_conversation_load_retry_requires_one_visible_control(case,expected):
    import shutil,subprocess
    from agent_runtime.llm.chatgpt_browser import _RETRY_CONVERSATION_LOAD_EXPRESSION
    node=shutil.which("node")
    if not node: pytest.skip("Node required")
    fixture=r"""
const kind=JSON.parse(process.argv[1]);let clicks=0;
const retry={innerText:'Retry',textContent:'Retry',disabled:kind==='disabled',
 getAttribute:(name)=>name==='aria-disabled'?'false':null,
 getClientRects:()=>kind==='hidden'?[]:[{}],click:()=>clicks++};
const surface={innerText:kind==='missing_error'?'Other error':'Could not load this ChatGPT conversation\\nRetry',
 textContent:kind==='missing_error'?'Other error':'Could not load this ChatGPT conversation\\nRetry',
 getClientRects:()=>[{}],querySelectorAll:()=>kind==='two_retries'?[retry,retry]:[retry]};
global.document={querySelectorAll:(selector)=>selector==='main,[role="main"]'?[surface]:[]};
const result=eval(process.argv[2]);
process.stdout.write(JSON.stringify({result,clicks}));
"""
    got=json.loads(subprocess.check_output(
        [node,"-e",fixture,json.dumps(case),_RETRY_CONVERSATION_LOAD_EXPRESSION],text=True))
    assert got=={"result":expected,"clicks":int(expected)}