from datetime import datetime, timezone
from types import SimpleNamespace

from agent_runtime.resource_discovery import ModelTrial, ResourceNeed, ResourceScout, provider_profiles, select_provider

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def test_scout_generates_vendor_neutral_queries_and_persists_untrusted_opportunities(tmp_path):
    calls=[]
    def search(query, limit, source_class):
        calls.append((query, source_class))
        return SimpleNamespace(receipt_id="receipt:1", payload={"results":[
            {"title":"Free inference API", "url":"https://models.example/free", "snippet":"Free tier; get an API key after signup."},
            {"title":"Duplicate", "url":"https://models.example/free", "snippet":"same"},
        ]})
    scout=ResourceScout(tmp_path, search)
    need=ResourceNeed("llm_inference","reduce financial burn while preserving quality","single paid provider dependency",("public data only",))
    rows=scout.discover(need,now=NOW)
    assert len(rows)==1 and rows[0].claimed_free and rows[0].credential_hint
    assert all("openrouter" not in q.lower() and "gemini" not in q.lower() for q, _ in calls)
    assert {source for _, source in calls} == {"general", "technical"}
    assert scout.opportunities()[0].status=="DISCOVERED"


def test_router_uses_trials_not_free_marketing_claims():
    trials=[
        ModelTrial("t1","free-a","classify","COMPLETED",0.90,100,0.0,("e1",)),
        ModelTrial("t2","free-a","classify","COMPLETED",0.80,120,0.0,("e2",)),
        ModelTrial("t3","paid-b","classify","COMPLETED",0.98,80,0.02,("e3",)),
        ModelTrial("t4","bad-free","classify","COMPLETED",0.40,50,0.0,("e4",)),
    ]
    profiles=provider_profiles(trials,minimum_quality=0.75)
    chosen=select_provider(profiles,task_class="classify",quality_floor=0.8)
    assert chosen is not None and chosen.opportunity_id=="free-a"
    assert select_provider(profiles,task_class="deep-reasoning",quality_floor=0.8) is None


def test_zero_general_results_adapts_to_technical_source_class(tmp_path):
    calls=[]
    def search(query, limit, source_class):
        calls.append((query, source_class))
        if source_class == "general":
            return {"results": []}
        return {"results": [{"title":"Free model API list", "url":"https://code.example/llm", "snippet":"free API options"}]}
    scout=ResourceScout(tmp_path, search)
    need=ResourceNeed("llm_inference","reduce cost","paid dependency")
    rows=scout.discover(need,now=NOW)
    assert rows and rows[0].source_class == "technical"
    assert calls[0][1] == "general" and any(source == "technical" for _, source in calls)
    run=next((tmp_path/"resource_discovery"/"runs").glob("*.json"))
    import json
    payload=json.loads(run.read_text())
    assert payload["schema_version"] == "runtime-resource-discovery.v2"
    assert any(item["result_count"] == 0 for item in payload["search_attempts"])


def test_verification_rejects_noise_and_promotes_only_fetched_savings_evidence(tmp_path):
    def search(query, limit, source_class):
        return {"results": [
            {"title":"Canvas API", "url":"https://docs.example/canvas", "snippet":"browser graphics"},
            {"title":"Local LLM inference", "url":"https://code.example/local-llm", "snippet":"open source LLM inference"},
        ]}
    def fetch(url, max_chars):
        if "local-llm" in url:
            return SimpleNamespace(receipt_id="fetch:1", payload={"ok":True,"page":{"success":True,"url":url,"markdown":"Local LLM server. Free, open source OpenAI alternative. Run locally with docker run. No API key needed for local use."}})
        raise AssertionError("irrelevant candidate must not be fetched")
    scout=ResourceScout(tmp_path, search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"), now=NOW)
    rows=scout.verify_pending(now=NOW, fetch=fetch, limit=10)
    statuses={r.status for r in rows}
    assert "REJECTED_IRRELEVANT" in statuses
    verified=next(r for r in rows if r.status=="VERIFIED_CANDIDATE")
    assert verified.free_evidence and verified.local_evidence
    assert scout.verifications()
    current={x.url:x.status for x in scout.opportunities()}
    assert current["https://code.example/local-llm"]=="VERIFIED_CANDIDATE"


def test_fetched_page_must_still_be_llm_relevant(tmp_path):
    def search(query, limit, source_class):
        return {"results": [{"title":"Free model API", "url":"https://code.example/video", "snippet":"model inference free API"}]}
    def fetch(url, max_chars):
        return SimpleNamespace(receipt_id="fetch:video", payload={"ok":True,"page":{"success":True,"url":url,"markdown":"Free video face generation API with image models and OpenCV utilities."}})
    scout=ResourceScout(tmp_path, search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"), now=NOW)
    rows=scout.verify_pending(now=NOW, fetch=fetch, limit=10)
    assert rows[0].status == "REJECTED_WRONG_RESOURCE"
    assert not any(row.status == "VERIFIED_CANDIDATE" for row in rows)


def test_legacy_verification_without_version_is_treated_as_v1(tmp_path):
    folder=tmp_path/"resource_discovery"/"verifications"
    folder.mkdir(parents=True)
    (folder/"legacy.json").write_text('{"opportunity_id":"resource-opportunity:legacy","status":"VERIFIED_CANDIDATE","relevance_score":0.9,"observed_at":"2026-09-17T00:00:00+00:00","evidence_refs":[]}')
    scout=ResourceScout(tmp_path, lambda q,l,s:{"results":[]})
    rows=scout.verifications()
    assert rows[0].verification_version == "resource-verification.v1"
