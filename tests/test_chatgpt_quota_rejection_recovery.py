import hashlib,json
from pathlib import Path
import pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider,_CHATGPT_STATE_EXPRESSION
from test_chatgpt_browser_provider import FakeBrowser

@pytest.mark.parametrize("case",["rejected","answered","generating","foreign_user","foreign_quota","no_probe","stale_probe","still_limited"])
def test_only_freshly_checked_exact_quota_rejected_turn_can_settle(tmp_path,case):
    p=ChatGPTBrowserProvider(browser=FakeBrowser(),conversation_state_path=tmp_path/"conversation.json",
        poll_interval_seconds=.1,stable_seconds=.5)
    outbound=p._request_text("request",{})
    pending={"request_id":"owned","outbound":outbound,"payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
        "baseline_user_count":1,"baseline_assistant_count":1,"baseline_assistant_id":"old",
        "submission_acknowledged":True,"stage":"WAITING_RESPONSE"}
    url="https://chatgpt.com/c/owned"
    journal={"schema_version":1,"session_id":p.session_id,"conversation_url":url,"pending":pending,
        "last_usage_limit":{"status":"CHATGPT_WEB_USAGE_LIMIT","request_id":"foreign" if case=="foreign_quota" else "owned",
            "url":url,"observed_at":100},
        "usage_limit_probe":{"request_id":"owned","payload_sha256":pending["payload_sha256"],"url":url,
            "refreshed_at":50 if case=="stale_probe" else 101}}
    if case=="no_probe":journal.pop("usage_limit_probe")
    p.conversation_state_path.write_text(json.dumps(journal))
    payload=tmp_path/"payload";payload.write_text(outbound);clock=[0.]
    class Clock:
        def monotonic(self):clock[0]+=.2;return clock[0]
        def sleep(self,seconds):clock[0]+=seconds
        def time(self):return 102
    def js(expr):
        assert expr==_CHATGPT_STATE_EXPRESSION,"settlement must not press Retry or Send"
        answered=case=="answered"
        return {"url":url,"composer_selector":"editor","composer_text":"","user_count":2,
            "assistant_count":2 if answered else 1,"last_assistant_id":"new" if answered else "old",
            "last_assistant_text":"answer" if answered else "old answer",
            "last_user_text":"someone else's request" if case=="foreign_user" else outbound,
            "assistant_after_last_user":answered,"generating":case=="generating","usage_limited":case=="still_limited"}
    scope={"js":js,"time":Clock()}
    script=p._transaction_script(payload,5).replace("import time\n","")
    if case=="answered":
        exec(script,scope)
        assert scope["result"]["response"]=="answer" and p._load_conversation()["pending"] is None
        assert "last_failed_request" not in p._load_conversation()
    else:
        with pytest.raises(RuntimeError,match="archived without resubmitting" if case=="rejected" else "CHATGPT_WEB_USAGE_LIMIT" if case=="still_limited" else "response exceeded"):
            exec(script,scope)
        after=p._load_conversation()
        if case=="rejected":
            assert after["pending"] is None
            failure=after["last_failed_request"]
            assert failure["failure_cause"]=="NATIVE_USAGE_LIMIT_REJECTION"
            assert failure["generation_failure_confirmed"] is True
            archive=Path(failure["archive"])
            assert archive.stat().st_mode & 0o777 == 0o600
            assert json.loads(archive.read_text())["outbound"]==outbound
        else:
            assert after["pending"]==pending
            assert "last_failed_request" not in after

@pytest.mark.parametrize("case",["due","recent","generating","unowned_page"])
def test_quota_recheck_refreshes_only_owned_idle_carrier_once_per_cooldown(tmp_path,monkeypatch,case):
    class Browser:
        def __init__(self):self.reloads=[];self.scripts=[]
        def invoke(self,cap,args):assert args["url"]=="https://chatgpt.com/c/owned" and args["reuse_current"]
        def evaluate(self,sid,expr):
            assert expr==_CHATGPT_STATE_EXPRESSION
            return {"url":"https://chatgpt.com/c/other" if case=="unowned_page" else "https://chatgpt.com/c/owned",
                "generating":case=="generating"}
        def reload_owned_session(self,sid,*,expected_url):self.reloads.append(expected_url)
        def run_session_script(self,sid,script,**kwargs):
            self.scripts.append(script)
            return {"response":"answer"}
    b=Browser();p=ChatGPTBrowserProvider(browser=b,process_lock_path=tmp_path/"lock",conversation_state_path=tmp_path/"journal")
    monkeypatch.setattr("agent_runtime.llm.chatgpt_browser.time.time",lambda:1000)
    journal={"conversation_url":"https://chatgpt.com/c/owned","pending":{"request_id":"owned","outbound":"request","submission_acknowledged":True},
        "last_usage_limit":{"status":"CHATGPT_WEB_USAGE_LIMIT","request_id":"owned","url":"https://chatgpt.com/c/owned","observed_at":990 if case=="recent" else 600}}
    p._run_turn_once("request",5,journal)
    assert b.reloads==(["https://chatgpt.com/c/owned"] if case=="due" else [])
    assert ("usage_limit_probed_at = 1000" in b.scripts[0]) is (case=="due")
