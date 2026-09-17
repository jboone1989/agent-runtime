from datetime import datetime, timezone

from agent_runtime.resource_discovery import ProviderTrialPlan
from agent_runtime.resource_trials import (
    ProviderTrialTarget, model_trials_from_measurements, resolve_trial_target,
    run_bounded_provider_trial, trial_target_for_credential,
)

NOW=datetime(2026,9,17,15,tzinfo=timezone.utc)


def _plan(env="OPENROUTER_API_KEY"):
    return ProviderTrialPlan(
        plan_id="plan:1", lead_id="lead:1", provider_label="Provider",
        credential_env=env, status="READY_TO_TRIAL", priority_score=0.5,
        evidence_refs=("evidence:1",),
    )


def test_trial_targets_are_protocol_metadata_not_selection_scores(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_TRIAL_MODEL", raising=False)
    n=trial_target_for_credential("NVIDIA_API_KEY")
    o=trial_target_for_credential("OPENROUTER_API_KEY")
    assert n and n.base_url == "https://integrate.api.nvidia.com/v1"
    assert n.model == ""
    assert o and o.base_url == "https://openrouter.ai/api/v1" and o.model == ""
    assert trial_target_for_credential("UNKNOWN_API_KEY") is None


def test_nvidia_target_resolves_current_small_general_instruction_model(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    calls=[]
    def catalog(url, headers, timeout):
        calls.append((url, headers, timeout))
        return {"data":[
            {"id":"meta/llama-3.2-11b-vision-instruct"},
            {"id":"deepseek-ai/deepseek-coder-6.7b-instruct"},
            {"id":"mistralai/mistral-7b-instruct-v0.3"},
            {"id":"ibm/granite-3.0-3b-a800m-instruct"},
            {"id":"nvidia/llama-3.1-nemoguard-8b-content-safety"},
        ]}
    target=resolve_trial_target("NVIDIA_API_KEY",api_key="secret",catalog_transport=catalog)
    assert target is not None
    assert target.model=="ibm/granite-3.0-3b-a800m-instruct"
    assert calls[0][0].endswith("/models")
    assert calls[0][1]["Authorization"]=="Bearer secret"
    assert "secret" not in repr(target)


def test_openrouter_target_resolves_concrete_zero_price_text_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"openrouter/free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text","image"],"output_modalities":["text"]}},
        {"id":"cohere/north-mini-code:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"z-ai/glm-5.2:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"liquid/lfm-2.5-2.6b:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"paid/model-1b","pricing":{"prompt":"0.1","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
    ]}
    target=resolve_trial_target("OPENROUTER_API_KEY",api_key="secret",catalog_transport=lambda *args: rows)
    assert target is not None
    assert target.model=="liquid/lfm-2.5-2.6b:free"
    assert target.include_usage is True


def test_bounded_trial_uses_only_three_synthetic_requests_and_sanitizes_results():
    calls=[]
    answers=["Mira", "NEGATIVE", "BLUE"]
    def transport(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        return {"choices":[{"message":{"content":answers[len(calls)-1]}}], "usage":{"cost":0,"prompt_tokens":11,"completion_tokens":2}}
    target=ProviderTrialTarget("OPENROUTER_API_KEY","https://openrouter.ai/api/v1","test/free",True)
    rows=run_bounded_provider_trial(_plan(), api_key="secret-key", now=NOW, transport=transport, target=target)
    assert len(rows)==3 and len(calls)==3
    assert all(row.status=="COMPLETED" and row.quality_score==1.0 for row in rows)
    assert all(row.observed_cost==0.0 for row in rows)
    assert all(row.prompt_tokens==11 and row.completion_tokens==2 for row in rows)
    assert all("secret-key" not in str(row.to_payload()) for row in rows)
    assert all(call[2]["max_tokens"]==32 and call[2]["temperature"]==0 for call in calls)
    assert all(call[2]["usage"]=={"include":True} for call in calls)
    models=model_trials_from_measurements(rows)
    assert len(models)==3 and all(row.opportunity_id=="lead:1" for row in models)
    assert all(row.provider_lead_id=="lead:1" and row.provider_label=="Provider" for row in models)
    assert all(row.model_id=="test/free" and row.prompt_tokens==11 for row in models)


def test_trial_failure_does_not_expose_error_or_key():
    def transport(url, headers, body, timeout):
        raise RuntimeError("upstream rejected secret-key")
    target=ProviderTrialTarget("OPENROUTER_API_KEY","https://openrouter.ai/api/v1","test/free",True)
    rows=run_bounded_provider_trial(_plan(), api_key="secret-key", now=NOW, transport=transport, target=target)
    assert len(rows)==3 and all(row.status=="FAILED" for row in rows)
    assert all(row.error_type=="RuntimeError" for row in rows)
    assert all("secret-key" not in str(row.to_payload()) for row in rows)


def test_trial_rejects_nonready_plan():
    plan=_plan(); plan=ProviderTrialPlan(**{**plan.__dict__, "status":"NEEDS_CREDENTIAL"})
    try:
        run_bounded_provider_trial(plan, api_key="x", now=NOW, transport=lambda *args: {})
    except ValueError as exc:
        assert "not ready" in str(exc)
    else:
        raise AssertionError("expected ValueError")
