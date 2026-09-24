from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_runtime.llm.resource_pool import (
    LLMResourceSpec,
    ResourcePoolProvider,
    load_resource_pool_provider_from_environment,
)


NOW = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)


class FakeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class FakeProvider:
    def __init__(self, result="ok", error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0
        self.kwargs = []

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        if self.error:
            raise self.error
        return self.result

    async def structured(self, prompt, schema, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        if self.error:
            raise self.error
        return {"ok": self.result}

    async def embed(self, text, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        if self.error:
            raise self.error
        return [1.0]


def free_resource(
    *,
    resource_id="openrouter-free",
    quality=0.75,
    success=2 / 3,
    expires_at=None,
    quota_remaining=None,
):
    return LLMResourceSpec(
        resource_id=resource_id,
        provider_id="openrouter",
        provider_label="OpenRouter",
        model="openrouter/free",
        credential_env="OPENROUTER_API_KEY",
        protocol="openai_chat",
        base_url="https://openrouter.ai/api/v1",
        lifecycle="RECURRING_FREE",
        pricing_class="FREE",
        quality_score=quality,
        success_rate=success,
        mean_latency_ms=2200,
        expires_at=expires_at,
        quota_remaining=quota_remaining,
        evidence_refs=("trial:openrouter-free",),
    )


def paid_resource():
    return LLMResourceSpec(
        resource_id="deepseek-stable",
        provider_id="deepseek",
        provider_label="DeepSeek",
        model="deepseek-flash",
        credential_env="VIRTUALBRAIN_LLM_API_KEY",
        protocol="deepseek_anthropic",
        base_url="https://api.deepseek.com/anthropic",
        lifecycle="STABLE_PAID",
        pricing_class="PAID",
        reserve=True,
        quality_score=0.95,
        success_rate=0.99,
        evidence_refs=("configured:stable-reserve",),
    )


def _pool(tmp_path, monkeypatch, resources, providers, *, policy=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "paid-secret")
    policy_path = tmp_path / "policy.json"
    if policy is not None:
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return ResourcePoolProvider(
        resources,
        policy_path=policy_path if policy is not None else None,
        state_dir=tmp_path / "runtime",
        provider_factory=lambda spec: providers[spec.resource_id],
        now=lambda: NOW,
    )


def test_viable_free_resource_is_used_before_paid_reserve(tmp_path, monkeypatch):
    free = FakeProvider("free")
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    assert asyncio.run(pool.complete("hello")) == "free"
    assert free.calls == 1
    assert paid.calls == 0
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["selected_resource_id"] == "openrouter-free"
    assert latest["attempted_resource_ids"] == ["openrouter-free"]
    raw = (tmp_path / "runtime" / "latest.json").read_text()
    assert "free-secret" not in raw
    assert "paid-secret" not in raw
    assert "hello" not in raw


def test_rate_limited_free_resource_fails_over_to_paid_and_enters_cooldown(tmp_path, monkeypatch):
    free = FakeProvider(error=FakeError("HTTP_429"))
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    assert asyncio.run(pool.complete("first")) == "paid"
    assert free.calls == 1 and paid.calls == 1
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["selected_resource_id"] == "deepseek-stable"
    assert latest["attempted_resource_ids"] == ["openrouter-free", "deepseek-stable"]

    assert asyncio.run(pool.complete("second")) == "paid"
    assert free.calls == 1
    assert paid.calls == 2
    health = json.loads((tmp_path / "runtime" / "health.json").read_text())
    assert health["resources"]["openrouter-free"]["last_error_code"] == "HTTP_429"
    assert health["resources"]["openrouter-free"]["cooldown_until"] is not None


def test_low_quality_free_resource_is_not_used_just_because_it_is_free(tmp_path, monkeypatch):
    free = FakeProvider("bad-free")
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(quality=0.40), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    assert asyncio.run(pool.complete("quality matters")) == "paid"
    assert free.calls == 0
    assert paid.calls == 1


@pytest.mark.parametrize(
    "resource",
    [
        free_resource(expires_at=(NOW - timedelta(seconds=1)).isoformat()),
        free_resource(quota_remaining=0),
    ],
)
def test_expired_or_exhausted_free_resource_is_skipped(resource, tmp_path, monkeypatch):
    free = FakeProvider("free")
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [resource, paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    assert asyncio.run(pool.complete("fallback")) == "paid"
    assert free.calls == 0
    assert paid.calls == 1


def test_vb_pressure_policy_is_recorded_in_routing_decision(tmp_path, monkeypatch):
    free = FakeProvider("free")
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
        policy={
            "routing_mode": "FREE_FIRST_RESERVE_PAID",
            "pressure": "CONSERVE",
            "prefer_free": True,
            "minimum_quality": 0.7,
            "mind_tick": 91,
            "evidence_refs": ["vb-resource-state:91"],
        },
    )

    assert asyncio.run(pool.complete("policy")) == "free"
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["pressure"] == "CONSERVE"
    assert latest["mind_tick"] == 91
    assert "vb-resource-state:91" in latest["evidence_refs"]


def test_manifest_loader_uses_environment_names_not_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-value")
    manifest = tmp_path / "resources.json"
    manifest.write_text(json.dumps({
        "schema_version": "llm-resource-pool.v1",
        "resources": [free_resource().to_payload()],
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_LLM_RESOURCE_POOL_PATH", str(manifest))
    monkeypatch.setenv("AGENT_LLM_ROUTING_STATE_DIR", str(tmp_path / "state"))

    provider = load_resource_pool_provider_from_environment()
    assert isinstance(provider, ResourcePoolProvider)
    assert provider.resources[0].credential_env == "OPENROUTER_API_KEY"
    assert "secret-value" not in manifest.read_text()


def test_structured_call_uses_same_resource_selection(tmp_path, monkeypatch):
    free = FakeProvider("free")
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )
    result = asyncio.run(pool.structured("json", {"type": "object"}))
    assert result == {"ok": "free"}
    assert free.calls == 1
    assert paid.calls == 0


def test_provider_repr_never_exposes_api_keys():
    from agent_runtime.llm.deepseek import DeepSeekAnthropicProvider
    from agent_runtime.llm.openai_compatible import OpenAICompatibleChatProvider

    deepseek = DeepSeekAnthropicProvider(api_key="deep-secret", model="m")
    openai = OpenAICompatibleChatProvider(
        api_key="open-secret", model="m", base_url="https://example.test/v1"
    )
    assert "deep-secret" not in repr(deepseek)
    assert "open-secret" not in repr(openai)


def test_malformed_pool_manifest_falls_back_to_stable_provider(tmp_path, monkeypatch):
    from agent_runtime.llm import DeepSeekAnthropicProvider, load_llm_provider

    manifest = tmp_path / "resources.json"
    manifest.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("AGENT_LLM_RESOURCE_POOL_PATH", str(manifest))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "stable-secret")
    monkeypatch.setenv("DEEPSEEK_MODEL", "stable-model")

    provider = load_llm_provider()
    assert isinstance(provider, DeepSeekAnthropicProvider)
    assert provider.model == "stable-model"


def test_pool_hot_reloads_new_manifest_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-secret")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "paid-secret")
    manifest = tmp_path / "resources.json"
    manifest.write_text(json.dumps({
        "schema_version": "llm-resource-pool.v1",
        "resources": [paid_resource().to_payload()],
    }), encoding="utf-8")
    providers = {
        "openrouter-free": FakeProvider("free"),
        "deepseek-stable": FakeProvider("paid"),
    }
    pool = ResourcePoolProvider(
        [paid_resource()],
        manifest_path=manifest,
        state_dir=tmp_path / "runtime",
        provider_factory=lambda spec: providers[spec.resource_id],
        now=lambda: NOW,
    )
    assert asyncio.run(pool.complete("before")) == "paid"

    manifest.write_text(json.dumps({
        "schema_version": "llm-resource-pool.v1",
        "resources": [free_resource().to_payload(), paid_resource().to_payload()],
    }), encoding="utf-8")
    stat = manifest.stat()
    # Ensure mtime moves even on coarse/fast filesystems.
    import os as _os
    _os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert asyncio.run(pool.complete("after")) == "free"


def test_known_request_quota_is_consumed_before_provider_limit_error(tmp_path, monkeypatch):
    free = FakeProvider("free")
    paid = FakeProvider("paid")
    resource = free_resource(quota_remaining=1)
    pool = _pool(
        tmp_path,
        monkeypatch,
        [resource, paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    assert asyncio.run(pool.complete("first")) == "free"
    assert asyncio.run(pool.complete("second")) == "paid"
    assert free.calls == 1
    assert paid.calls == 1
    health = json.loads((tmp_path / "runtime" / "health.json").read_text())
    assert health["resources"]["openrouter-free"]["consumed_quota"] == 1.0


def windfall_resource():
    return LLMResourceSpec(
        resource_id="nvidia-frontier",
        provider_id="nvidia",
        provider_label="NVIDIA NIM",
        model="nvidia/nemotron-3-super-120b-a12b",
        credential_env="NVIDIA_API_KEY",
        protocol="openai_chat",
        base_url="https://integrate.api.nvidia.com/v1",
        lifecycle="RECURRING_FREE",
        pricing_class="FREE",
        quality_score=0.80,
        success_rate=0.80,
        mean_latency_ms=10000,
        task_classes=("general_text", "self_upgrade", "code_audit"),
        capability_tier="FRONTIER",
        capability_score=0.97,
        capability_tags=("reasoning", "coding", "planning"),
        windfall_eligible=True,
        evidence_refs=("trial:nvidia-frontier",),
    )


def test_high_value_task_prefers_verified_free_capability_windfall(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-secret")
    normal = free_resource(quality=0.95, success=0.95)
    frontier = windfall_resource()
    providers = {
        "openrouter-free": FakeProvider("normal"),
        "nvidia-frontier": FakeProvider("frontier"),
        "deepseek-stable": FakeProvider("paid"),
    }
    pool = _pool(
        tmp_path,
        monkeypatch,
        [normal, frontier, paid_resource()],
        providers,
        policy={
            "pressure": "CONSERVE",
            "prefer_free": True,
            "minimum_quality": 0.70,
            "windfall_active": True,
            "windfall_bonus": 0.30,
            "high_value_task_classes": ["self_upgrade", "code_audit"],
        },
    )

    assert asyncio.run(
        pool.complete("improve yourself", _routing_task_class="self_upgrade")
    ) == "frontier"
    assert providers["nvidia-frontier"].calls == 1
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["selected_resource_id"] == "nvidia-frontier"
    assert "capability=FRONTIER:0.970" in latest["reason"]
    assert "windfall=1" in latest["reason"]


def test_normal_task_does_not_get_windfall_bonus(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-secret")
    normal = free_resource(quality=0.95, success=0.95)
    frontier = windfall_resource()
    providers = {
        "openrouter-free": FakeProvider("normal"),
        "nvidia-frontier": FakeProvider("frontier"),
        "deepseek-stable": FakeProvider("paid"),
    }
    pool = _pool(
        tmp_path,
        monkeypatch,
        [normal, frontier, paid_resource()],
        providers,
        policy={
            "pressure": "CONSERVE",
            "prefer_free": True,
            "minimum_quality": 0.70,
            "windfall_active": True,
            "windfall_bonus": 0.30,
        },
    )

    assert asyncio.run(pool.complete("ordinary work")) == "normal"
    assert providers["openrouter-free"].calls == 1
    assert providers["nvidia-frontier"].calls == 0


def test_structured_free_first_policy_uses_affordable_paid_reserve_second(
    tmp_path, monkeypatch
):
    free_specs = [
        free_resource(resource_id=f"free-{index}", quality=1.0, success=1.0)
        for index in range(6)
    ]
    providers = {
        spec.resource_id: FakeProvider(error=FakeError("INVALID_STRUCTURED_JSON"))
        for spec in free_specs
    }
    paid = FakeProvider("paid")
    providers["deepseek-stable"] = paid
    pool = _pool(
        tmp_path,
        monkeypatch,
        [*free_specs, paid_resource()],
        providers,
        policy={
            "routing_mode": "FREE_FIRST_RESERVE_PAID",
            "pressure": "NORMAL",
            "prefer_free": True,
            "minimum_quality": 0.7,
            "max_failovers": 4,
            "paid_available_funds": 93.31,
            "paid_reserve_floor": 20.0,
        },
    )

    result = asyncio.run(
        pool.structured(
            "plan",
            {"type": "object"},
            _attempt_timeout_seconds=15.0,
            _reserve_timeout_seconds=40.0,
        )
    )

    assert result == {"ok": "paid"}
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["attempted_resource_ids"] == [
        "free-0",
        "deepseek-stable",
    ]
    assert paid.calls == 1
    assert providers["free-0"].kwargs[-1]["timeout_seconds"] == 15.0
    assert paid.kwargs[-1]["timeout_seconds"] == 40.0
    assert sum(
        provider.calls
        for key, provider in providers.items()
        if key.startswith("free-")
    ) == 1


def test_paid_reserve_slot_is_not_forced_below_budget_floor(tmp_path, monkeypatch):
    free_specs = [
        free_resource(resource_id=f"free-{index}", quality=1.0, success=1.0)
        for index in range(6)
    ]
    providers = {
        spec.resource_id: FakeProvider(error=FakeError("REQUEST_FAILED"))
        for spec in free_specs
    }
    paid = FakeProvider("paid")
    providers["deepseek-stable"] = paid
    pool = _pool(
        tmp_path,
        monkeypatch,
        [*free_specs, paid_resource()],
        providers,
        policy={
            "routing_mode": "FREE_FIRST_RESERVE_PAID",
            "pressure": "CONSERVE",
            "prefer_free": True,
            "minimum_quality": 0.7,
            "max_failovers": 4,
            "paid_available_funds": 20.0,
            "paid_reserve_floor": 20.0,
        },
    )

    with pytest.raises(Exception, match="all eligible LLM resources failed"):
        asyncio.run(pool.structured("plan", {"type": "object"}))

    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert "deepseek-stable" not in latest["attempted_resource_ids"]
    assert paid.calls == 0


def test_unaffordable_paid_reserve_is_not_viable_when_free_routes_are_unavailable(
    tmp_path, monkeypatch
):
    paid = FakeProvider("paid")
    pool = _pool(
        tmp_path,
        monkeypatch,
        [paid_resource()],
        {"deepseek-stable": paid},
        policy={
            "routing_mode": "FREE_FIRST_RESERVE_PAID",
            "pressure": "CRITICAL",
            "prefer_free": True,
            "minimum_quality": 0.7,
            "max_failovers": 2,
            "paid_available_funds": 0.0,
            "paid_reserve_floor": 20.0,
        },
    )

    with pytest.raises(Exception, match="no viable LLM resource"):
        asyncio.run(pool.complete("mission discovery"))

    assert paid.calls == 0
