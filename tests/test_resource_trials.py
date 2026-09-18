from datetime import datetime, timezone

import pytest

from agent_runtime.resource_discovery import ProviderTrialPlan
from agent_runtime.resource_trials import (
    ProviderTrialRequestError, ProviderTrialTarget, model_trials_from_measurements,
    resolve_trial_target, run_bounded_provider_trial, trial_target_for_credential,
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
    assert n.max_tokens == 64
    assert o and o.base_url == "https://openrouter.ai/api/v1" and o.model == ""
    assert trial_target_for_credential("UNKNOWN_API_KEY") is None


def test_nvidia_target_prefers_current_hosted_general_model_over_smaller_legacy_row(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    calls=[]
    def catalog(url, headers, timeout):
        calls.append((url, headers, timeout))
        return {"data":[
            {"id":"meta/llama-3.2-11b-vision-instruct"},
            {"id":"deepseek-ai/deepseek-coder-6.7b-instruct"},
            {"id":"mistralai/mistral-7b-instruct-v0.3"},
            {"id":"ibm/granite-3.0-3b-a800m-instruct"},
            {"id":"openai/gpt-oss-20b"},
            {"id":"nvidia/llama-3.1-nemoguard-8b-content-safety"},
        ]}
    target=resolve_trial_target("NVIDIA_API_KEY",api_key="secret",catalog_transport=catalog)
    assert target is not None
    assert target.model=="openai/gpt-oss-20b"
    assert calls[0][0].endswith("/models")
    assert calls[0][1]["Authorization"]=="Bearer secret"
    assert "secret" not in repr(target)


def test_nvidia_target_skips_previously_incompatible_catalogue_models(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"openai/gpt-oss-20b"},
        {"id":"z-ai/glm-5.3-flash"},
        {"id":"ibm/granite-3.0-3b-a800m-instruct"},
    ]}
    target=resolve_trial_target(
        "NVIDIA_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        exclude_models={"openai/gpt-oss-20b"},
    )
    assert target is not None
    assert target.model=="z-ai/glm-5.3-flash"


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


def test_openrouter_prefers_light_general_model_over_specialized_or_heavy(monkeypatch):
    monkeypatch.delenv("OPENROUTER_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"inclusionai/ling-flash-fin:free","name":"Ling Flash Fin","description":"finance-focused model with 5B active parameters","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"poolside/laguna-xs:free","name":"Laguna XS","description":"latest coding agent model with 3B active parameters","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"nvidia/nemotron-lightning:free","name":"Nemotron Lightning","description":"high-throughput agentic workload model with 3B active parameters out of 30B total","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"nvidia/nemotron-super-120b:free","name":"Nemotron Super","description":"120B model with 12B active parameters for complex multi-agent applications","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
    ]}
    target=resolve_trial_target("OPENROUTER_API_KEY",api_key="secret",catalog_transport=lambda *args: rows)
    assert target is not None
    assert target.model=="nvidia/nemotron-lightning:free"


def test_openrouter_target_skips_rate_limited_model_and_chooses_next(monkeypatch):
    monkeypatch.delenv("OPENROUTER_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"liquid/lfm-2.5-2.6b:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"mistral/small-7b:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
    ]}
    target=resolve_trial_target(
        "OPENROUTER_API_KEY", api_key="secret", catalog_transport=lambda *args: rows,
        exclude_models={"liquid/lfm-2.5-2.6b:free"},
    )
    assert target is not None
    assert target.model=="mistral/small-7b:free"


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


def test_nvidia_bounded_trial_uses_reasoning_compatible_token_budget():
    calls=[]
    answers=["Mira","NEGATIVE","BLUE"]
    def transport(url, headers, body, timeout):
        calls.append(body)
        return {"choices":[{"message":{"content":answers[len(calls)-1]}}]}
    target=ProviderTrialTarget(
        "NVIDIA_API_KEY",
        "https://integrate.api.nvidia.com/v1",
        "openai/gpt-oss-20b",
        False,
        64,
    )
    rows=run_bounded_provider_trial(
        _plan("NVIDIA_API_KEY"),
        api_key="secret-key",
        now=NOW,
        transport=transport,
        target=target,
    )
    assert len(rows)==3
    assert all(row.status=="COMPLETED" and row.quality_score==1.0 for row in rows)
    assert all(body["max_tokens"]==64 for body in calls)


def test_nvidia_high_capability_selection_prefers_opportunity_model(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"openai/gpt-oss-20b"},
        {"id":"z-ai/glm-5.3-flash"},
        {"id":"nvidia/nemotron-3-super-120b-a12b"},
    ]}
    target=resolve_trial_target(
        "NVIDIA_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        selection_mode="HIGH_CAPABILITY",
    )
    assert target is not None
    assert target.model=="nvidia/nemotron-3-super-120b-a12b"
    assert target.max_tokens>=128


def test_nvidia_high_capability_selection_moves_on_after_cooldown_exclusion(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"nvidia/nemotron-3-super-120b-a12b"},
        {"id":"z-ai/glm-5.3"},
        {"id":"openai/gpt-oss-20b"},
    ]}
    target=resolve_trial_target(
        "NVIDIA_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        exclude_models={"nvidia/nemotron-3-super-120b-a12b"},
        selection_mode="HIGH_CAPABILITY",
    )
    assert target is not None
    assert target.model=="z-ai/glm-5.3"


def test_high_capability_resolution_can_use_adopter_ranker(monkeypatch):
    monkeypatch.delenv("NVIDIA_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"openai/gpt-oss-20b"},
        {"id":"nvidia/nemotron-3-super-120b-a12b"},
        {"id":"moonshotai/kimi-k3"},
    ]}
    seen={}
    def ranker(ids, excluded):
        seen["ids"]=tuple(ids)
        seen["excluded"]=excluded
        return "moonshotai/kimi-k3" if "moonshotai/kimi-k3" not in excluded else "nvidia/nemotron-3-super-120b-a12b"
    target=resolve_trial_target(
        "NVIDIA_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        exclude_models={"openai/gpt-oss-20b"},
        selection_mode="HIGH_CAPABILITY",
        model_ranker=ranker,
    )
    assert target is not None
    assert target.model=="moonshotai/kimi-k3"
    assert "moonshotai/kimi-k3" in seen["ids"]
    assert seen["excluded"]==frozenset({"openai/gpt-oss-20b"})


def test_post_json_preserves_timeout_as_sanitized_timeout_code(monkeypatch):
    import agent_runtime.resource_trials as module

    def boom(*args, **kwargs):
        raise TimeoutError("socket timed out with secret-looking detail")

    monkeypatch.setattr(module.urllib.request, "urlopen", boom)
    with pytest.raises(ProviderTrialRequestError) as exc:
        module._post_json(
            "https://example.invalid/v1/chat/completions",
            {"Authorization": "Bearer secret"},
            {"model": "m"},
            1.0,
        )
    assert exc.value.code == "TIMEOUT"
    assert "secret-looking" not in str(exc.value)


def test_get_json_preserves_non_timeout_url_error_as_network_error(monkeypatch):
    import agent_runtime.resource_trials as module

    def boom(*args, **kwargs):
        raise module.urllib.error.URLError("dns failure detail")

    monkeypatch.setattr(module.urllib.request, "urlopen", boom)
    with pytest.raises(ProviderTrialRequestError) as exc:
        module._get_json(
            "https://example.invalid/v1/models",
            {"Authorization": "Bearer secret"},
            1.0,
        )
    assert exc.value.code == "NETWORK_ERROR"
    assert "dns failure" not in str(exc.value)


def test_cerebras_target_resolves_with_external_reputation_ranker(monkeypatch):
    monkeypatch.delenv("CEREBRAS_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"llama3.1-8b"},
        {"id":"gpt-oss-120b"},
        {"id":"zai-glm-4.7"},
    ]}
    seen={}
    def ranker(ids, excluded):
        seen["ids"]=tuple(ids)
        return "gpt-oss-120b"
    target=resolve_trial_target(
        "CEREBRAS_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        selection_mode="HIGH_CAPABILITY",
        model_ranker=ranker,
    )
    assert target is not None
    assert target.base_url=="https://api.cerebras.ai/v1"
    assert target.model=="gpt-oss-120b"
    assert target.max_tokens>=128
    assert "gpt-oss-120b" in seen["ids"]


def test_openrouter_high_capability_ranker_only_sees_zero_price_text_models(monkeypatch):
    monkeypatch.delenv("OPENROUTER_TRIAL_MODEL", raising=False)
    rows={"data":[
        {"id":"openrouter/free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"deepseek/deepseek-v4-flash:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
        {"id":"nvidia/nemotron-3-ultra:free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["text","image"],"output_modalities":["text"]}},
        {"id":"paid/frontier","pricing":{"prompt":"0.1","completion":"0.2"},"architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
    ]}
    seen={}
    def ranker(ids, excluded):
        seen["ids"]=tuple(ids)
        return "deepseek/deepseek-v4-flash:free"
    target=resolve_trial_target(
        "OPENROUTER_API_KEY",
        api_key="secret",
        catalog_transport=lambda *args: rows,
        selection_mode="HIGH_CAPABILITY",
        model_ranker=ranker,
    )
    assert target is not None
    assert target.model=="deepseek/deepseek-v4-flash:free"
    assert set(seen["ids"])=={
        "deepseek/deepseek-v4-flash:free",
        "nvidia/nemotron-3-ultra:free",
    }


def test_trial_target_includes_cerebras_protocol_metadata(monkeypatch):
    monkeypatch.delenv("CEREBRAS_TRIAL_MODEL", raising=False)
    target=trial_target_for_credential("CEREBRAS_API_KEY")
    assert target is not None
    assert target.base_url=="https://api.cerebras.ai/v1"
    assert target.model==""
