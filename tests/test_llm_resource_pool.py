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

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    async def structured(self, prompt, schema, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return {"ok": self.result}

    async def embed(self, text, **kwargs):
        self.calls += 1
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
