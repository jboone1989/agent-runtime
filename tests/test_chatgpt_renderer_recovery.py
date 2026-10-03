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
    assert browser.reloads==[("chatgpt-cognition","https://chatgpt.com/c/owned")]
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
