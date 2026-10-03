import hashlib,json,pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, _CHATGPT_STATE_EXPRESSION
from test_chatgpt_browser_provider import FakeBrowser

@pytest.mark.parametrize("case", ["fresh", "later", "ambiguous", "stale", "foreign_user"])
def test_assistant_only_receipt_requires_own_send_and_new_server_uuid(tmp_path, case):
    p=ChatGPTBrowserProvider(browser=FakeBrowser(),conversation_state_path=tmp_path/"journal.json",
                            poll_interval_seconds=.1,stable_seconds=.5)
    text=p._request_text("current owned request",{})
    path=tmp_path/"payload.txt";path.write_text(text)
    older="11111111-1111-4111-8111-111111111111"
    newer="22222222-2222-4222-8222-222222222222"
    fresh=case=="fresh"
    journal={"schema_version":1,"session_id":p.session_id,
             "conversation_url":"https://chatgpt.com/c/owned",
             "last_completed":{"url":"https://chatgpt.com/c/previous" if fresh else "https://chatgpt.com/c/owned"},
             "pending":{"request_id":"own","outbound":text,"payload_sha256":hashlib.sha256(text.encode()).hexdigest(),
                        "baseline_user_count":0,"baseline_assistant_count":0 if fresh else 1,
                        "baseline_assistant_id":None if fresh else older,
                        "submission_acknowledged":case!="ambiguous","stage":"WAITING_RESPONSE"}}
    p.conversation_state_path.write_text(json.dumps(journal))
    state={"url":journal["conversation_url"],"composer_selector":"#prompt-textarea","composer_text":"",
           "user_count":1 if case=="foreign_user" else 0,"assistant_count":1,
           "last_user_text":"foreign request" if case=="foreign_user" else "",
           "last_assistant_id":older if case=="stale" else newer,
           "last_assistant_text":"owned answer","assistant_only_rendering":True,"generating":False}
    class Clock:
        now=0
        def monotonic(self):self.now+=.2;return self.now
        def sleep(self,seconds):self.now+=seconds
    def js(expr):
        assert expr==_CHATGPT_STATE_EXPRESSION,"Never resubmit a pending request"
        return state
    namespace={"js":js,"time":Clock()}
    if case in {"fresh","later"}:
        exec(p._transaction_script(path,5).replace("import time\n",""),namespace)
        assert namespace["result"]["response"]=="owned answer"
        assert namespace["result"]["request_id"]=="own"
        assert namespace["result"]["receipt_ownership"]=="ACKNOWLEDGED_SEND_AND_NEW_ASSISTANT_UUID"
        assert p._load_conversation()["pending"] is None
    else:
        with pytest.raises(RuntimeError,match="response exceeded"):
            exec(p._transaction_script(path,5).replace("import time\n",""),namespace)
        assert p._load_conversation()["pending"]["request_id"]=="own"
