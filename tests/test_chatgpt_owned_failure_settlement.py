import json
import shutil
import subprocess
from pathlib import Path
import pytest
from agent_runtime.llm.chatgpt_browser import (ChatGPTBrowserProvider,_CHATGPT_STATE_EXPRESSION,
    _SEND_EXPRESSION,_RETRY_GENERATION_EXPRESSION)
from test_chatgpt_browser_provider import FakeBrowser

@pytest.mark.parametrize("case",["ambiguous","restored","legacy_retry","transient","native_error"])
def test_failed_generation_settlement_requires_stable_restored_owned_payload(tmp_path,case):
    provider=ChatGPTBrowserProvider(browser=FakeBrowser(),conversation_state_path=tmp_path/"conversation.json",
        poll_interval_seconds=.1,stable_seconds=.5)
    outbound=provider._request_text("request",{})
    pending={"request_id":"owned","outbound":outbound,"payload_sha256":"sha",
        "baseline_user_count":1,"baseline_assistant_count":1,"baseline_assistant_id":"old",
        "submission_acknowledged":True,"stage":"WAITING_RESPONSE","generation_retry_count":1}
    if case=="legacy_retry":
        pending["generation_retry_count"]=0;pending["unknown_error_retry"]={"status":"REQUESTED"}
    journal={"schema_version":1,"session_id":provider.session_id,"conversation_url":"https://chatgpt.com/c/owned","pending":pending}
    provider.conversation_state_path.write_text(json.dumps(journal))
    payload=tmp_path/"request.txt";payload.write_text(outbound)
    clock=[0.];polls=[0]
    class Clock:
        def monotonic(self):clock[0]+=.2;return clock[0]
        def sleep(self,t):clock[0]+=t
    def js(expr):
        assert expr==_CHATGPT_STATE_EXPRESSION,"a recorded retry must never click Retry or Send again"
        polls[0]+=1
        state={"url":journal["conversation_url"],"composer_selector":"#prompt-textarea",
            "composer_text":outbound if case not in {"ambiguous","native_error"} else "",
            "generation_error_kind":"REQUEST_FAILED" if case=="native_error" else None,
            "generating":False,"generation_error":True,"user_count":2,"assistant_count":1,
            "last_assistant_id":"old","last_assistant_text":"old","last_user_text":outbound}
        if case=="transient" and polls[0]>=3:
            state.update(generation_error=False,last_assistant_id="new",assistant_count=2,last_assistant_text="answer")
        return state
    scope={"js":js,"time":Clock()}
    script=provider._transaction_script(payload,5).replace("import time\n","")
    if case=="transient":
        exec(script,scope)
        assert scope["result"]["response"]=="answer"
        assert "last_failed_request" not in json.loads(provider.conversation_state_path.read_text())
    else:
        with pytest.raises(RuntimeError,match="after same-request Retry"):exec(script,scope)
        durable=json.loads(provider.conversation_state_path.read_text())
        if case=="ambiguous":
            assert durable["pending"]==pending and "last_failed_request" not in durable
        else:
            assert durable["pending"] is None
            assert durable["last_failed_request"]["composer_restored"] is (case!="native_error")
            assert durable["last_failed_request"]["generation_failure_confirmed"] is (case=="native_error")
            archive=Path(durable["last_failed_request"]["archive"])
            assert archive.stat().st_mode & 0o777 == 0o600
            assert json.loads(archive.read_text())["outbound"]==outbound

@pytest.mark.parametrize("case",["valid","native_error","unowned","generating","answered","different_url","two_buttons","disabled","hidden"])
def test_retry_action_rechecks_current_page_and_unique_control(case):
    node=shutil.which("node")
    if not node:pytest.skip("Node required to execute real page guard")
    state={"url":"https://chatgpt.com/c/other" if case=="different_url" else "https://chatgpt.com/c/owned",
        "generating":case=="generating","last_user_text":"other" if case=="unowned" else "fingerprint",
        "last_assistant_id":"new" if case=="answered" else "old","assistant_count":1}
    expr=_RETRY_GENERATION_EXPRESSION.replace("LIVE_STATE",json.dumps(state)).replace("EXPECTED_URL",json.dumps("https://chatgpt.com/c/owned")).replace("BASELINE_ID",json.dumps("old")).replace("BASELINE_COUNT","1").replace("REQUEST_MARKER",json.dumps("fingerprint"))
    fixture=r"""
const kind=CASE;let clicks=0;
const button={textContent:'Retry',disabled:kind==='disabled',getAttribute:()=>null,
 getClientRects:()=>kind==='hidden'?[]:[{}],click:()=>clicks++};
const alert={innerText:'Unknown error\nRetry',textContent:'Unknown error\nRetry',
 getClientRects:()=>[{}],querySelectorAll:()=>kind==='two_buttons'?[button,button]:[button]};
if (kind==='native_error') {alert.innerText=alert.textContent='Something went wrong. Please try again\nRetry';}
const user={textContent:'fingerprint',getClientRects:()=>[{}]};
global.document={querySelectorAll:selector=>selector.includes('[role="alert"]')?[alert]:[user]};
const result=EXPRESSION;
console.log(JSON.stringify({result,clicks}));
""".replace("CASE",json.dumps(case)).replace("EXPRESSION",expr)
    got=json.loads(subprocess.check_output([node,"-e",fixture],text=True))
    assert got=={"result":case in {"valid","native_error"},"clicks":int(case in {"valid","native_error"})}
