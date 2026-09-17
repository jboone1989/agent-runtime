from datetime import datetime, timezone
from types import SimpleNamespace

from agent_runtime.resource_discovery import ModelTrial, ResourceNeed, ResourceScout, provider_profiles, select_provider

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def test_scout_generates_vendor_neutral_queries_and_persists_untrusted_opportunities(tmp_path):
    calls=[]
    def search(query, limit):
        calls.append(query)
        return SimpleNamespace(receipt_id="receipt:1", payload={"results":[
            {"title":"Free inference API", "url":"https://models.example/free", "snippet":"Free tier; get an API key after signup."},
            {"title":"Duplicate", "url":"https://models.example/free", "snippet":"same"},
        ]})
    scout=ResourceScout(tmp_path, search)
    need=ResourceNeed("llm_inference","reduce financial burn while preserving quality","single paid provider dependency",("public data only",))
    rows=scout.discover(need,now=NOW)
    assert len(rows)==1 and rows[0].claimed_free and rows[0].credential_hint
    assert all("openrouter" not in q.lower() and "gemini" not in q.lower() for q in calls)
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
