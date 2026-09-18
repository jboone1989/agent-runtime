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


def test_model_trials_round_trip_extended_evidence_without_secret_fields(tmp_path):
    scout=ResourceScout(tmp_path,lambda q,l,s:{"results":[]})
    trial=ModelTrial(
        "trial:1","resource-opportunity:a","simple_public_text","COMPLETED",
        1.0,125.0,0.0,("provider-response-sha256:abc",),
        provider_lead_id="provider-lead:a",provider_label="Provider A",
        model_id="model/a",started_at=NOW.isoformat(),completed_at=NOW.isoformat(),
        prompt_tokens=12,completion_tokens=4,
    )
    scout.record_model_trial(trial)
    rows=scout.model_trials()
    assert rows==[trial]
    raw=next((tmp_path/"resource_discovery"/"model_trials").glob("*.json")).read_text()
    assert "API_KEY" not in raw and "secret" not in raw.lower()


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


def test_provider_profile_can_require_complete_trial_coverage():
    trials=[
        ModelTrial("t1","provider-a","simple_public_text","COMPLETED",1.0,100,0.0,("e1",)),
        ModelTrial("t2","provider-a","simple_public_text","COMPLETED",0.5,120,0.0,("e2",)),
    ]
    profiles=provider_profiles(trials,minimum_quality=0.75,minimum_trials=3)
    assert len(profiles)==1
    assert profiles[0].trial_count==2
    assert profiles[0].mean_quality==0.75
    assert profiles[0].viable is False
    assert select_provider(profiles,task_class="simple_public_text",quality_floor=0.75) is None


def test_provider_profiles_do_not_mix_models_for_same_provider():
    trials=[
        ModelTrial("a1","provider-a","simple_public_text","COMPLETED",0.0,100,0.0,("a1",),model_id="model-bad"),
        ModelTrial("a2","provider-a","simple_public_text","COMPLETED",0.5,100,0.0,("a2",),model_id="model-bad"),
        ModelTrial("a3","provider-a","simple_public_text","COMPLETED",0.0,100,0.0,("a3",),model_id="model-bad"),
        ModelTrial("b1","provider-a","simple_public_text","COMPLETED",1.0,80,0.0,("b1",),model_id="model-good"),
        ModelTrial("b2","provider-a","simple_public_text","COMPLETED",1.0,90,0.0,("b2",),model_id="model-good"),
        ModelTrial("b3","provider-a","simple_public_text","COMPLETED",1.0,85,0.0,("b3",),model_id="model-good"),
    ]
    profiles=provider_profiles(trials,minimum_quality=0.75,minimum_trials=3)
    assert {(p.model_id,p.trial_count,p.viable) for p in profiles}=={
        ("model-bad",3,False),("model-good",3,True),
    }
    chosen=select_provider(profiles,task_class="simple_public_text",quality_floor=0.75)
    assert chosen is not None and chosen.model_id=="model-good"


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


def test_negated_llm_mentions_do_not_count_as_llm_resource(tmp_path):
    def search(query, limit, source_class):
        return {"results": [{"title":"Zero-cost local model inference", "url":"https://code.example/vision", "snippet":"model inference free API LLM"}]}
    def fetch(url, max_chars):
        return SimpleNamespace(receipt_id="fetch:vision", payload={"ok":True,"page":{"success":True,"url":url,"markdown":"Local CPU inference with YuNet ONNX. No third-party LLM or cloud vision API. Inference cost = $0."}})
    scout=ResourceScout(tmp_path, search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"), now=NOW)
    rows=scout.verify_pending(now=NOW, fetch=fetch, limit=10)
    assert rows[0].status == "REJECTED_WRONG_RESOURCE"


def test_verified_github_resource_expands_provider_leads_from_public_env_example(tmp_path):
    def search(query, limit, source_class):
        return {"results": [{"title":"Free LLM router", "url":"https://github.com/acme/free-router", "snippet":"LLM free API key router"}]}
    pages={
        "https://github.com/acme/free-router":"Free LLM router. Free tier providers, API key required. OpenAI-compatible chat completions.",
        "https://raw.githubusercontent.com/acme/free-router/HEAD/.env.example":"""# Free LLM Provider API Keys\n# Copy this file to .env and fill in the keys you have.\n\n# Provider One — free tier, no card\n# Get key: https://console.one.example/keys\nONE_API_KEY=your-key\n\n# Provider Two — trial credits\n# Get token: https://cloud.two.example/signup\nTWO_API_TOKEN=token\n""",
    }
    def fetch(url, max_chars):
        text=pages.get(url)
        if text is None: return {"ok":False}
        return SimpleNamespace(receipt_id="fetch:"+url.split('/')[2], payload={"ok":True,"page":{"success":True,"url":url,"markdown":text}})
    scout=ResourceScout(tmp_path,search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"),now=NOW)
    scout.verify_pending(now=NOW,fetch=fetch,limit=10)
    leads=scout.expand_provider_leads(now=NOW,fetch=fetch)
    assert {x.credential_env for x in leads}=={"ONE_API_KEY","TWO_API_TOKEN"}
    one=next(x for x in leads if x.credential_env=="ONE_API_KEY")
    assert one.provider_label=="Provider One"
    assert one.signup_url=="https://console.one.example/keys"
    assert one.official_root=="one.example"
    assert one.claimed_free is True
    assert len(scout.provider_leads())==2


def test_non_github_verified_resource_is_not_treated_as_provider_catalogue(tmp_path):
    scout=ResourceScout(tmp_path,lambda q,l,s:{"results":[]})
    assert scout.expand_provider_leads(now=NOW,fetch=lambda u,n: (_ for _ in ()).throw(AssertionError()))==[]


def test_provider_verification_requires_official_domain_and_classifies_recurring_free(tmp_path):
    phase={"verified":False}; verification_queries=[]
    def search(query, limit, source_class):
        if phase["verified"]:
            verification_queries.append(query)
            if "free tier" in query.lower():
                return SimpleNamespace(receipt_id="search:official", payload={"results":[
                    {"title":"Provider One API pricing", "url":"https://docs.one.example/api/pricing", "snippet":"Free tier API rate limits"},
                    {"title":"Community comparison", "url":"https://blog.example/provider-one", "snippet":"claims free"},
                ]})
            return SimpleNamespace(receipt_id="search:other", payload={"results":[]})
        return {"results": [{"title":"Free LLM router", "url":"https://github.com/acme/free-router", "snippet":"LLM free API key router"}]}
    pages={
        "https://github.com/acme/free-router":"Free LLM router. Free tier providers, API key required. OpenAI-compatible chat completions.",
        "https://raw.githubusercontent.com/acme/free-router/HEAD/.env.example":"""# Provider One — free tier\n# Get key: https://console.one.example/keys\nONE_API_KEY=your-key\n""",
        "https://console.one.example/keys":"Provider One API account. Create an API key after signup.",
        "https://docs.one.example/api/pricing":"Provider One API pricing and rate limits. Free tier includes 100 requests/day at no charge. No credit card required. Requests per minute limits apply.",
    }
    def fetch(url, max_chars):
        text=pages.get(url)
        if text is None:
            return {"ok":False}
        return SimpleNamespace(receipt_id="fetch:"+str(abs(hash(url))), payload={"ok":True,"page":{"success":True,"url":url,"markdown":text}})
    scout=ResourceScout(tmp_path,search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"),now=NOW)
    scout.verify_pending(now=NOW,fetch=fetch,limit=10)
    scout.expand_provider_leads(now=NOW,fetch=fetch)
    phase["verified"]=True
    rows=scout.verify_provider_leads(now=NOW,fetch=fetch,limit=10)
    assert len(rows)==1
    row=rows[0]
    assert row.status=="VERIFIED_RECURRING_FREE"
    assert row.free_status=="RECURRING_FREE"
    assert row.payment_method_required is False
    assert row.rate_limit_evidence is True
    assert row.official_urls==("https://console.one.example/keys","https://docs.one.example/api/pricing")
    assert not any("blog.example" in ref for ref in row.evidence_refs)
    assert any("API pricing" in q for q in verification_queries)
    assert any("free tier rate limits API" in q for q in verification_queries)
    assert scout.provider_leads()[0].status=="VERIFIED_RECURRING_FREE"


def test_provider_verification_v1_is_rechecked_by_v2(tmp_path):
    scout=ResourceScout(tmp_path,lambda q,l,s:{"results":[]})
    leads=tmp_path/"resource_discovery"/"provider_leads"; leads.mkdir(parents=True)
    leads.joinpath("x.json").write_text('{"lead_id":"provider-lead:x","source_opportunity_id":"resource-opportunity:x","provider_label":"Provider X","credential_env":"X_API_KEY","signup_url":"https://x.example/keys","official_root":"x.example","discovered_at":"2026-09-17T00:00:00+00:00","source_receipt_id":"r","evidence_refs":[],"status":"OFFICIAL_EVIDENCE_INCONCLUSIVE"}')
    folder=tmp_path/"resource_discovery"/"provider_verifications"; folder.mkdir(parents=True)
    folder.joinpath("x.json").write_text('{"lead_id":"provider-lead:x","status":"OFFICIAL_EVIDENCE_INCONCLUSIVE","observed_at":"2026-09-17T00:00:00+00:00","verification_version":"provider-verification.v1"}')
    rows=scout.verify_provider_leads(now=NOW,fetch=lambda u,n:{"ok":False})
    assert len(rows)==1 and rows[0].verification_version=="provider-verification.v2"


def test_provider_verification_does_not_recheck_current_version(tmp_path):
    scout=ResourceScout(tmp_path,lambda q,l,s:{"results":[]})
    folder=tmp_path/"resource_discovery"/"provider_verifications"
    folder.mkdir(parents=True)
    (folder/"x.json").write_text('{"lead_id":"provider-lead:x","status":"OFFICIAL_EVIDENCE_INCONCLUSIVE","observed_at":"2026-09-17T00:00:00+00:00","verification_version":"provider-verification.v1"}')
    assert scout.provider_verifications()[0].verification_version=="provider-verification.v1"


def test_provider_trial_planner_uses_evidence_not_vendor_name():
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification, plan_provider_trials
    leads=[
        ProviderLead("lead:a","op:a","Alpha","ALPHA_API_KEY","https://alpha.example/keys","alpha.example",NOW.isoformat(),"r"),
        ProviderLead("lead:b","op:b","Beta","BETA_API_KEY","https://beta.example/keys","beta.example",NOW.isoformat(),"r"),
        ProviderLead("lead:c","op:c","Gamma","GAMMA_API_KEY","https://gamma.example/keys","gamma.example",NOW.isoformat(),"r"),
    ]
    rows=[
        ProviderVerification("lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True),
        ProviderVerification("lead:b","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",privacy_caution=True),
        ProviderVerification("lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",payment_method_required=False),
    ]
    plans=plan_provider_trials(leads,rows,available_credentials={"BETA_API_KEY"})
    assert [p.provider_label for p in plans]==["Alpha","Beta","Gamma"]
    assert plans[0].status=="NEEDS_CREDENTIAL"
    assert plans[1].status=="READY_TO_TRIAL"
    assert "privacy_caution" in plans[1].reasons
    assert all(p.data_policy=="PUBLIC_SYNTHETIC_ONLY" and p.max_requests==3 for p in plans)


def test_provider_trial_planner_ignores_unknown_and_paid_only():
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification, plan_provider_trials
    lead=ProviderLead("lead:x","op:x","X","X_API_KEY","https://x.example/keys","x.example",NOW.isoformat(),"r")
    assert plan_provider_trials([lead],[ProviderVerification("lead:x","VERIFIED_PAID_ONLY",NOW.isoformat(),free_status="PAID_ONLY")])==[]


def test_acquisition_friction_deprioritizes_blocked_path_without_vendor_fallback():
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification, ResourceAcquisitionOutcome, plan_provider_trials
    leads=[
        ProviderLead("lead:a","opp","A","A_KEY","https://a.example/key","a.example",NOW.isoformat(),"r"),
        ProviderLead("lead:b","opp","B","B_KEY","https://b.example/key","b.example",NOW.isoformat(),"r"),
    ]
    verified=[
        ProviderVerification("lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True),
        ProviderVerification("lead:b","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE"),
    ]
    blocked=ResourceAcquisitionOutcome(
        "outcome:1","plan:a","lead:a","BLOCKED_EXTERNAL",NOW.isoformat(),
        friction_kind="PHONE_VERIFICATION",source="HUMAN_REPORTED",evidence_refs=("human-report:1",),
    )
    plans=plan_provider_trials(leads,verified,acquisition_outcomes=[blocked])
    assert plans[0].lead_id == "lead:b"
    a=next(x for x in plans if x.lead_id=="lead:a")
    assert a.status == "BLOCKED_BY_FRICTION"
    assert "friction:phone_verification" in a.reasons
    assert "human-report:1" in a.evidence_refs


def test_legacy_no_viable_model_is_treated_as_model_level_replan_not_provider_death():
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification, ResourceAcquisitionOutcome, plan_provider_trials
    leads=[
        ProviderLead("lead:a","opp","A","A_KEY","https://a.example/key","a.example",NOW.isoformat(),"r"),
        ProviderLead("lead:b","opp","B","B_KEY","https://b.example/key","b.example",NOW.isoformat(),"r"),
    ]
    verified=[
        ProviderVerification("lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True),
        ProviderVerification("lead:b","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT"),
    ]
    exhausted=ResourceAcquisitionOutcome(
        "outcome:x","plan:a","lead:a","NO_VIABLE_MODEL",NOW.isoformat(),
        evidence_refs=("trial:1","trial:2","trial:3"),
    )
    plans=plan_provider_trials(
        leads,verified,available_credentials={"A_KEY"},acquisition_outcomes=[exhausted]
    )
    a=next(x for x in plans if x.lead_id=="lead:a")
    assert a.status == "READY_TO_TRIAL"
    assert "model_level_replan_observed" in a.reasons
    assert "trial:1" in a.evidence_refs


def test_only_provider_scope_evidence_can_mark_provider_unavailable():
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification, ResourceAcquisitionOutcome, plan_provider_trials
    lead=ProviderLead("lead:a","opp","A","A_KEY","https://a.example/key","a.example",NOW.isoformat(),"r")
    verified=ProviderVerification("lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE")
    unavailable=ResourceAcquisitionOutcome(
        "outcome:p","plan:a","lead:a","PROVIDER_UNAVAILABLE",NOW.isoformat(),
        evidence_refs=("provider-probe:1",),
    )
    plan=plan_provider_trials(
        [lead],[verified],available_credentials={"A_KEY"},acquisition_outcomes=[unavailable]
    )[0]
    assert plan.status == "PROVIDER_UNAVAILABLE"
    assert "provider_unavailable_observed" in plan.reasons
    assert "provider-probe:1" in plan.evidence_refs


def test_acquisition_friction_becomes_vendor_neutral_discovery_constraints():
    from agent_runtime.resource_discovery import ResourceAcquisitionOutcome, resource_discovery_constraints
    rows=[
        ResourceAcquisitionOutcome("o1","p1","l1","BLOCKED_EXTERNAL",NOW.isoformat(),friction_kind="PHONE_VERIFICATION"),
        ResourceAcquisitionOutcome("o2","p2","l2","BLOCKED_EXTERNAL",NOW.isoformat(),friction_kind="PAYMENT_METHOD"),
    ]
    assert resource_discovery_constraints(rows) == ("without phone verification", "no credit card required")


def test_search_constraints_do_not_leak_internal_policy_text(tmp_path):
    calls=[]
    def search(query, limit, source_class):
        calls.append(query); return {"results": []}
    scout=ResourceScout(tmp_path, search)
    need=ResourceNeed(
        "llm_inference", "reduce cost", "friction",
        constraints=("never send secrets during discovery",),
        search_constraints=("without phone verification",),
    )
    scout.discover(need, now=NOW)
    assert calls
    assert any("without phone verification" in q for q in calls[:3])
    assert all("never send secrets" not in q for q in calls)


def test_verification_fetch_timeout_isolated_to_one_candidate(tmp_path):
    def search(query, limit, source_class):
        return {"results":[
            {"title":"Local LLM inference API", "url":"https://one.example/llm", "snippet":"LLM inference free API"},
            {"title":"Local LLM inference API", "url":"https://two.example/llm", "snippet":"LLM inference free API"},
        ]}
    def fetch(url, max_chars):
        if "one.example" in url:
            raise TimeoutError("upstream timed out")
        return SimpleNamespace(receipt_id="fetch:two", payload={"ok":True,"page":{"success":True,"url":url,"markdown":"Free, open source LLM inference API. Run locally."}})
    scout=ResourceScout(tmp_path, search)
    scout.discover(ResourceNeed("llm_inference","reduce cost","paid dependency"), now=NOW, max_queries=1)
    rows=scout.verify_pending(now=NOW, fetch=fetch, limit=10)
    statuses={row.status for row in rows}
    assert "VERIFICATION_UNAVAILABLE" in statuses
    assert "VERIFIED_CANDIDATE" in statuses
    assert scout.pending_verification_count() == 0

def test_verified_multi_provider_catalogue_expands_untrusted_provider_leads(tmp_path):
    from types import SimpleNamespace
    source='https://catalog.example/free-llm'
    catalogue='''Free LLM API providers. API key access varies by provider.
| Provider | API | Notes |
| Alpha Cloud | [API](https://console.alpha.example/keys) | free tier, no credit card |
| Beta Models | [API](https://beta.example/developers) | free API quota |
'''
    def search(query, limit, source_class):
        return {'results':[{'title':'Free LLM API catalogue','url':source,'snippet':'Free LLM API providers with API key options'}]}
    def fetch(url, max_chars):
        if url == source:
            return SimpleNamespace(receipt_id='fetch:catalogue', payload={'ok':True,'page':{'success':True,'url':url,'markdown':catalogue}})
        return {'ok':False}
    scout=ResourceScout(tmp_path,search)
    scout.discover(ResourceNeed('llm_inference','reduce cost','paid dependency'),now=NOW)
    verified=scout.verify_pending(now=NOW,fetch=fetch,limit=10)
    assert verified[0].status == 'VERIFIED_CANDIDATE'
    assert verified[0].credential_evidence is True
    leads=scout.expand_provider_leads(now=NOW,fetch=fetch)
    assert {row.provider_label for row in leads} == {'Alpha Cloud','Beta Models'}
    assert {row.credential_env for row in leads} == {'ALPHA_CLOUD_API_KEY','BETA_MODELS_API_KEY'}
    assert {row.official_root for row in leads} == {'alpha.example','beta.example'}
    assert all(row.status == 'DISCOVERED' for row in leads)
    assert all(row.claimed_free is True for row in leads)


def test_single_provider_article_is_not_promoted_as_catalogue(tmp_path):
    from types import SimpleNamespace
    source='https://blog.example/cheap-llm'
    article='Cheap LLM API tip: use [Alpha Cloud](https://alpha.example/api) for a free tier API key.'
    def search(query, limit, source_class):
        return {'results':[{'title':'Cheap LLM API tip','url':source,'snippet':'LLM API key free tier'}]}
    def fetch(url, max_chars):
        if url == source:
            return SimpleNamespace(receipt_id='fetch:article', payload={'ok':True,'page':{'success':True,'url':url,'markdown':article}})
        return {'ok':False}
    scout=ResourceScout(tmp_path,search)
    scout.discover(ResourceNeed('llm_inference','reduce cost','paid dependency'),now=NOW)
    scout.verify_pending(now=NOW,fetch=fetch,limit=10)
    assert scout.expand_provider_leads(now=NOW,fetch=fetch) == []


def test_catalogue_claim_never_skips_official_provider_verification(tmp_path):
    from types import SimpleNamespace
    source='https://catalog.example/free-llm'
    catalogue='''Free tier LLM API directory with API keys.
| Provider | API |
| Alpha Cloud | [API](https://alpha.example/keys) |
| Beta Models | [API](https://beta.example/keys) |
'''
    phase={'official':False}
    def search(query, limit, source_class):
        if phase['official']:
            return SimpleNamespace(receipt_id='search:official', payload={'results':[]})
        return {'results':[{'title':'Free LLM API directory','url':source,'snippet':'Free LLM API keys'}]}
    def fetch(url, max_chars):
        if url == source:
            return SimpleNamespace(receipt_id='fetch:catalogue', payload={'ok':True,'page':{'success':True,'url':url,'markdown':catalogue}})
        return {'ok':False}
    scout=ResourceScout(tmp_path,search)
    scout.discover(ResourceNeed('llm_inference','reduce cost','paid dependency'),now=NOW)
    scout.verify_pending(now=NOW,fetch=fetch,limit=10)
    leads=scout.expand_provider_leads(now=NOW,fetch=fetch)
    assert len(leads)==2 and all(row.status=='DISCOVERED' for row in leads)
    phase['official']=True
    rows=scout.verify_provider_leads(now=NOW,fetch=fetch,limit=10)
    assert len(rows)==2
    assert all(row.status=='OFFICIAL_EVIDENCE_UNAVAILABLE' for row in rows)
    assert all(row.free_status=='UNKNOWN' for row in rows)

def test_pending_provider_verification_count_tracks_unverified_leads(tmp_path):
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
    scout=ResourceScout(tmp_path,lambda q,l,s:{'results':[]})
    lead=ProviderLead('lead:x','op:x','X Cloud','X_CLOUD_API_KEY','https://x.example/keys','x.example',NOW.isoformat(),'r')
    scout._write_provider_lead(lead)
    assert scout.pending_provider_verification_count()==1
    scout._write_provider_verification(ProviderVerification(
        'lead:x','OFFICIAL_EVIDENCE_INCONCLUSIVE',NOW.isoformat(),verification_version='provider-verification.v2'
    ))
    assert scout.pending_provider_verification_count()==0

def test_catalogue_table_uses_provider_cell_not_document_link_labels(tmp_path):
    from types import SimpleNamespace
    source = "https://github.com/acme/free-catalog"
    catalogue = """Free LLM API provider catalogue with API key access.
| Provider | Links | Notes |
| Alpha Cloud | [Keys](https://console.alpha.example/keys) [Pricing](https://alpha.example/pricing) | free API |
| Beta Models | [Docs](https://docs.beta.example/api) [Limits](https://beta.example/limits) | free tier |
"""
    def search(query, limit, source_class):
        return {"results": [{"title": "Free LLM API catalogue", "url": source, "snippet": "free LLM API provider catalogue"}]}
    def fetch(url, max_chars):
        if url == source:
            return SimpleNamespace(receipt_id="fetch:catalogue", payload={"ok": True, "page": {"success": True, "url": url, "markdown": catalogue}})
        return {"ok": False}
    scout = ResourceScout(tmp_path, search)
    scout.discover(ResourceNeed("llm_inference", "reduce cost", "paid dependency"), now=NOW)
    scout.verify_pending(now=NOW, fetch=fetch, limit=10)
    leads = scout.expand_provider_leads(now=NOW, fetch=fetch)
    assert {row.provider_label for row in leads} == {"Alpha Cloud", "Beta Models"}
    assert len(leads) == 2

def test_active_provider_view_filters_historical_noise_and_collapses_duplicates(tmp_path):
    from agent_runtime.resource_discovery import ProviderLead, ResourceOpportunity
    scout = ResourceScout(tmp_path, lambda q, l, s: {"results": []})
    scout._write_opportunity(ResourceOpportunity(
        "op:catalog", "llm_inference", "Free LLM directory", "https://catalog.example/list",
        "directory of providers", NOW.isoformat(), "q", "r", True, True,
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:alpha-old", "op:catalog", "Alpha Cloud", "ALPHA_API_KEY",
        "https://alpha.example/keys", "alpha.example", NOW.isoformat(), "r",
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:alpha-new", "op:catalog", "Alpha Cloud.", "ALPHA_API_KEY",
        "https://alpha.example/pricing", "alpha.example", NOW.replace(minute=1).isoformat(), "r",
        status="VERIFIED_RECURRING_FREE",
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:noise", "op:catalog", "pricing page", "PRICING_PAGE_API_KEY",
        "https://beta.example/pricing", "beta.example", NOW.isoformat(), "r",
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:self", "op:catalog", "Hall of Fame", "HALL_OF_FAME_API_KEY",
        "https://catalog.example/hall", "catalog.example", NOW.isoformat(), "r",
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:directory", "op:catalog", "Live at directory.example", "DIRECTORY_API_KEY",
        "https://directory.example/", "directory.example", NOW.isoformat(), "r",
        source_claim="Community-maintained reference: compare providers and submit a provider.",
    ))
    rows = scout.provider_leads()
    assert [row.lead_id for row in rows] == ["lead:alpha-new"]
    assert scout.pending_provider_verification_count() == 1

def test_provider_verification_external_failure_isolated_to_one_provider(tmp_path):
    from agent_runtime.resource_discovery import ProviderLead
    from types import SimpleNamespace
    scout = ResourceScout(tmp_path, lambda q, l, s: {
        "results": [{
            "title": ("Alpha Cloud" if "Alpha" in q else "Beta Cloud") + " API pricing",
            "url": "https://alpha.example/pricing" if "Alpha" in q else "https://beta.example/pricing",
            "snippet": "API pricing free tier rate limits",
        }]
    })
    scout._write_provider_lead(ProviderLead(
        "lead:alpha", "op:a", "Alpha Cloud", "ALPHA_API_KEY",
        "https://alpha.example/keys", "alpha.example", NOW.isoformat(), "r",
    ))
    scout._write_provider_lead(ProviderLead(
        "lead:beta", "op:b", "Beta Cloud", "BETA_API_KEY",
        "https://beta.example/keys", "beta.example", NOW.isoformat(), "r",
    ))
    def fetch(url, max_chars):
        if "alpha.example" in url:
            raise RuntimeError("blocked")
        text = "Beta Cloud API pricing. Free tier includes 100 requests/day. No credit card required. Rate limits apply."
        return SimpleNamespace(receipt_id="fetch:beta", payload={"ok": True, "page": {"success": True, "url": url, "markdown": text}})
    rows = scout.verify_provider_leads(now=NOW, fetch=fetch, limit=10)
    by_id = {row.lead_id: row for row in rows}
    assert by_id["lead:alpha"].status == "OFFICIAL_EVIDENCE_UNAVAILABLE"
    assert any(ref.startswith("fetch-error:RuntimeError:") for ref in by_id["lead:alpha"].evidence_refs)
    assert by_id["lead:beta"].status == "VERIFIED_RECURRING_FREE"

def test_provider_catalogue_fetch_failure_does_not_abort_other_catalogues(tmp_path):
    from agent_runtime.resource_discovery import ResourceOpportunity, ResourceVerification
    from types import SimpleNamespace
    scout = ResourceScout(tmp_path, lambda q, l, s: {"results": []})
    for suffix in ("bad", "good"):
        opp = ResourceOpportunity(
            f"op:{suffix}", "llm_inference", f"{suffix} free LLM catalogue",
            f"https://github.com/acme/{suffix}", "free LLM API key catalogue",
            NOW.isoformat(), "q", "r", True, True, status="VERIFIED_CANDIDATE",
        )
        scout._write_opportunity(opp)
        scout._write_verification(ResourceVerification(
            opp.opportunity_id, "VERIFIED_CANDIDATE", 1.0, NOW.isoformat(),
            verification_url=opp.url, credential_evidence=True,
        ))
    good_page = """Free LLM API key catalogue.
| Provider | API |
| Gamma Cloud | [API](https://gamma.example/keys) |
| Delta Models | [API](https://delta.example/keys) |
"""
    def fetch(url, max_chars):
        if "/bad" in url:
            raise TimeoutError("catalogue timeout")
        if url == "https://github.com/acme/good":
            return SimpleNamespace(receipt_id="fetch:good", payload={"ok": True, "page": {"success": True, "url": url, "markdown": good_page}})
        return {"ok": False}
    rows = scout.expand_provider_leads(now=NOW, fetch=fetch, limit=10)
    assert {row.provider_label for row in rows} == {"Gamma Cloud", "Delta Models"}
    markers = {row.opportunity_id: row for row in scout.provider_catalogue_expansions()}
    assert "op:bad" not in markers
    assert markers["op:good"].status == "EXPANDED"

def test_catalogue_expansion_is_idempotent_for_same_parser_version(tmp_path):
    from agent_runtime.resource_discovery import ResourceOpportunity, ResourceVerification
    source = "https://catalog.example/list"
    page = """Free LLM API key catalogue.
| Provider | API |
| Alpha Cloud | [API](https://alpha.example/keys) |
| Beta Models | [API](https://beta.example/keys) |
"""
    scout = ResourceScout(tmp_path, lambda q, l, s: {"results": []})
    opp = ResourceOpportunity("op:idem", "llm_inference", "Free LLM catalogue", source,
        "free LLM API key catalogue", NOW.isoformat(), "q", "r", True, True, status="VERIFIED_CANDIDATE")
    scout._write_opportunity(opp)
    scout._write_verification(ResourceVerification(
        opp.opportunity_id, "VERIFIED_CANDIDATE", 1.0, NOW.isoformat(),
        verification_url=source, credential_evidence=True))
    calls = []
    def fetch(url, max_chars):
        calls.append(url)
        return SimpleNamespace(receipt_id="fetch:catalog", payload={"ok": True, "page": {"success": True, "url": url, "markdown": page}})
    assert len(scout.expand_provider_leads(now=NOW, fetch=fetch)) == 2
    first_calls = len(calls)
    assert scout.expand_provider_leads(now=NOW, fetch=fetch) == []
    assert len(calls) == first_calls
    marker = scout.provider_catalogue_expansions()[0]
    assert marker.status == "EXPANDED" and len(marker.lead_ids) == 2

def test_catalogue_expansion_migrates_existing_leads_without_fetch(tmp_path):
    from agent_runtime.resource_discovery import ProviderLead, ResourceOpportunity, ResourceVerification
    source = "https://catalog.example/list"
    scout = ResourceScout(tmp_path, lambda q, l, s: {"results": []})
    opp = ResourceOpportunity("op:migrate", "llm_inference", "Free LLM catalogue", source,
        "free LLM API key catalogue", NOW.isoformat(), "q", "r", True, True, status="VERIFIED_CANDIDATE")
    scout._write_opportunity(opp)
    scout._write_verification(ResourceVerification(
        opp.opportunity_id, "VERIFIED_CANDIDATE", 1.0, NOW.isoformat(),
        verification_url=source, credential_evidence=True))
    scout._write_provider_lead(ProviderLead(
        "lead:existing", opp.opportunity_id, "Alpha Cloud", "ALPHA_API_KEY",
        "https://alpha.example/keys", "alpha.example", NOW.isoformat(), "r"))
    def fetch(url, max_chars):
        raise AssertionError("historical catalogue must not be fetched during migration")
    assert scout.expand_provider_leads(now=NOW, fetch=fetch) == []
    marker = scout.provider_catalogue_expansions()[0]
    assert marker.status == "MIGRATED"
    assert marker.lead_ids == ("lead:existing",)

def test_catalogue_parser_version_change_allows_reexpansion(tmp_path):
    from agent_runtime.resource_discovery import (
        ProviderCatalogueExpansion, ResourceOpportunity, ResourceVerification,
        PROVIDER_CATALOGUE_PARSER_VERSION,
    )
    source = "https://catalog.example/list"
    page = """Free LLM API key catalogue.
| Provider | API |
| Alpha Cloud | [API](https://alpha.example/keys) |
| Beta Models | [API](https://beta.example/keys) |
"""
    scout = ResourceScout(tmp_path, lambda q, l, s: {"results": []})
    opp = ResourceOpportunity("op:reparse", "llm_inference", "Free LLM catalogue", source,
        "free LLM API key catalogue", NOW.isoformat(), "q", "r", True, True, status="VERIFIED_CANDIDATE")
    scout._write_opportunity(opp)
    scout._write_verification(ResourceVerification(
        opp.opportunity_id, "VERIFIED_CANDIDATE", 1.0, NOW.isoformat(),
        verification_url=source, credential_evidence=True))
    scout._write_provider_catalogue_expansion(ProviderCatalogueExpansion(
        opp.opportunity_id, source, "provider-catalogue-parser.old",
        NOW.isoformat(), "EXPANDED", ()))
    calls = []
    def fetch(url, max_chars):
        calls.append(url)
        return SimpleNamespace(receipt_id="fetch:new", payload={"ok": True, "page": {"success": True, "url": url, "markdown": page}})
    assert len(scout.expand_provider_leads(now=NOW, fetch=fetch)) == 2
    assert calls
    assert scout.provider_catalogue_expansions()[0].parser_version == PROVIDER_CATALOGUE_PARSER_VERSION
