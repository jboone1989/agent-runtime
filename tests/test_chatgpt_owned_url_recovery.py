import json,pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider

def test_unbound_accepted_turn_restores_only_its_owned_server_conversation(tmp_path,monkeypatch):
    class Browser:
        def __init__(self):self.calls=[]
        def owned_session_url(self,sid):self.calls.append(("metadata",sid));return "https://chatgpt.com/c/server-saved"
        def replace_owned_session(self,sid,*,expected_url):self.calls.append(("replace",sid,expected_url))
    browser=Browser();p=ChatGPTBrowserProvider(browser=browser,conversation_state_path=tmp_path/"state.json")
    pending={"outbound":"original","submission_acknowledged":True,"request_id":"original"}
    journal={"schema_version":1,"session_id":p.session_id,"pending":pending}
    p.conversation_state_path.write_text(json.dumps(journal));before=p.conversation_state_path.read_bytes();calls=[]
    def turn(payload,timeout,state):
        calls.append(payload)
        if len(calls)==1:raise RuntimeError("Page.enable timed out after 5s")
        assert state==journal and p.conversation_state_path.read_bytes()==before
        return {"response":"owned answer","request_id":"original","recovered":True}
    monkeypatch.setattr(p,"_run_turn_once",turn)
    assert p._run_turn("original",5,journal)["renderer_replaced"]
    assert calls==["original","original"]
    assert browser.calls==[("metadata",p.session_id),("replace",p.session_id,"https://chatgpt.com/c/server-saved")]

def test_owned_url_metadata_does_not_attach_any_renderer_or_read_other_tab_content(tmp_path):
    import contextlib,io
    from types import SimpleNamespace
    from test_browser_harness_integration import _provider
    def runner(argv,**kwargs):
        scope={"list_tabs":lambda **kw:[
            {"targetId":"owned","url":"https://example.com/c/owned","title":"must not be returned"},
            {"targetId":"operator","url":"https://example.com/private","title":"must not be returned"}]}
        out=io.StringIO()
        with contextlib.redirect_stdout(out):exec(kwargs["input"],scope)
        return SimpleNamespace(returncode=0,stdout=out.getvalue(),stderr="")
    browser,_=_provider(tmp_path,runner)
    browser._save_target_id("task","owned")
    assert browser.owned_session_url("task")=="https://example.com/c/owned"
