from datetime import datetime, timezone

from agent_runtime.resource_discovery import ProviderTrialPlan
from agent_runtime.resource_trials import (
    model_trials_from_measurements, run_bounded_provider_trial, trial_target_for_credential,
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
    assert n.model == "meta/llama-3.1-8b-instruct"
    assert o and o.base_url == "https://openrouter.ai/api/v1" and o.model == "openrouter/free"
    assert trial_target_for_credential("UNKNOWN_API_KEY") is None


def test_bounded_trial_uses_only_three_synthetic_requests_and_sanitizes_results():
    calls=[]
    answers=["Mira", "NEGATIVE", "BLUE"]
    def transport(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        return {"choices":[{"message":{"content":answers[len(calls)-1]}}], "usage":{"cost":0,"prompt_tokens":11,"completion_tokens":2}}
    rows=run_bounded_provider_trial(_plan(), api_key="secret-key", now=NOW, transport=transport)
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
    assert all(row.model_id=="openrouter/free" and row.prompt_tokens==11 for row in models)


def test_trial_failure_does_not_expose_error_or_key():
    def transport(url, headers, body, timeout):
        raise RuntimeError("upstream rejected secret-key")
    rows=run_bounded_provider_trial(_plan("NVIDIA_API_KEY"), api_key="secret-key", now=NOW, transport=transport)
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
