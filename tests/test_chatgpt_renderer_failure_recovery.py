import hashlib,json,pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider
from test_chatgpt_browser_provider import FakeBrowser
@pytest.mark.parametrize("case",["failed","ambiguous","pending","unready","twice_failed"])
def test_recovery_only_for_proven_failed_or_unsent_turn(tmp_path,monkeypatch,case):
    browser=FakeBrowser();reloads=[]
    browser.reload_owned_session=lambda sid,**kw:reloads.append((sid,kw))
    p=ChatGPTBrowserProvider(browser=browser,conversation_state_path=tmp_path/"conversation.json")
    outbound=p._request_text("task",{});archive=tmp_path/"failed.json"
    archive.write_text(json.dumps({"status":"GENERATION_FAILED","outbound":outbound}))
    journal={"schema_version":1,"session_id":p.session_id,"conversation_url":"https://chatgpt.com/c/owned",
        "pending":{"request_id":"pending"} if case=="pending" else None,
        "last_failed_request":{"request_id":"failed","payload_sha256":hashlib.sha256(outbound.encode()).hexdigest(),
            "composer_restored":case!="ambiguous","archive":str(archive)}}
    p.conversation_state_path.write_text(json.dumps(journal));calls=[]
    message=("ChatGPT live composer did not become ready within 30s" if case=="unready" else
        "ChatGPT generation failed with Unknown error after same-request Retry; exact restored payload archived without resubmitting")
    def run(payload,timeout,state):
        calls.append(payload)
        if len(calls)==1 or case=="twice_failed":raise RuntimeError(message)
        return {"response":"answer"}
    monkeypatch.setattr(p,"_run_turn_once",run)
    if case in {"ambiguous","pending","twice_failed"}:
        with pytest.raises(RuntimeError,match="ChatGPT"):p._run_turn(outbound,5,journal)
        assert len(calls)==(2 if case=="twice_failed" else 1)
        assert len(reloads)==int(case=="twice_failed")
    else:
        result=p._run_turn(outbound,5,journal)
        assert result["response"]=="answer" and result["renderer_reloaded"]
        assert calls==[outbound,outbound]
        assert reloads==[(p.session_id,{"expected_url":journal["conversation_url"]})]
        if case=="failed":assert result["generation_recovery_of_request_id"]=="failed"
