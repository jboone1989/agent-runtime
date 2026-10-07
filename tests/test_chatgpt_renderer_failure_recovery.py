import hashlib,json,pytest
from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider
from test_chatgpt_browser_provider import FakeBrowser
@pytest.mark.parametrize("case",["failed","ambiguous","pending","unready","composer_changed","twice_failed"])
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
        "ChatGPT composer changed while awaiting Send readiness" if case=="composer_changed" else
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
        assert reloads==[(p.browser_session_id,{"expected_url":journal["conversation_url"]})]
        if case=="failed":assert result["generation_recovery_of_request_id"]=="failed"

@pytest.mark.parametrize("case", ["settled", "pending", "corrupt", "message_too_long"])
def test_failed_carrier_moves_only_after_reload_and_durable_failure(tmp_path, monkeypatch, case):
    browser = FakeBrowser()
    p = ChatGPTBrowserProvider(browser=browser, conversation_state_path=tmp_path/"conversation.json")
    payload = p._request_text("continuing objective", {})
    archived = p._request_text("failed previous turn", {})
    archive = tmp_path/"failed.json"
    archive.write_text(json.dumps({"status": "GENERATION_FAILED", "outbound": archived}))
    journal = {"schema_version": 1, "session_id": p.session_id,
        "conversation_url": "https://chatgpt.com/c/owned",
        "pending": {"request_id": "ambiguous"} if case == "pending" else None,
        "last_failed_request": {"request_id": "failed", "reason": "GENERATION_FAILED",
            "composer_restored": True, "archive": str(archive),
            "payload_sha256": "bad" if case == "corrupt" else hashlib.sha256(archived.encode()).hexdigest()}}
    p.conversation_state_path.write_text(json.dumps(journal))
    calls = []
    def run(outbound, timeout, state):
        calls.append((outbound, state))
        if len(calls) == 1:
            raise RuntimeError("MESSAGE_TOO_LONG" if case == "message_too_long"
                               else "ChatGPT live composer state could not be read within 30s")
        assert state["conversation_transition"]["reason"] == "GENERATION_FAILED_AFTER_RELOAD"
        assert state["conversation_transition"]["old_url"] == journal["conversation_url"]
        assert json.loads(open(state["conversation_transition"]["archive"]).read())["conversation"] == journal
        return {"response": "continued", "conversation_rolled_over": True}
    monkeypatch.setattr(p, "_run_turn_once", run)
    if case == "settled":
        assert p._retry_reloaded_turn(payload, 5, journal)["response"] == "continued"
        assert [row[0] for row in calls] == [payload, payload]
    else:
        with pytest.raises(RuntimeError):
            p._retry_reloaded_turn(payload, 5, journal)
        assert len(calls) == 1
        assert "conversation_transition" not in p._load_conversation()
