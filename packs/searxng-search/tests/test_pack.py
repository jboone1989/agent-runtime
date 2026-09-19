from __future__ import annotations

import httpx
import pytest

from agent_runtime import ApplicationRuntime
from agent_runtime.agents import AgentManifest
from agent_runtime.capabilities import (
    CapabilityPackBindingStatus,
    CapabilityPackManager,
    CapabilityPolicy,
    InstalledCapabilityPackCatalog,
)
from runtime_searxng_pack import (
    PACK_ID,
    SearXNGSearchPack,
    SearXNGSearchProvider,
    WEB_SEARCH,
)


def test_pack_manifest_is_provider_neutral_and_read_only():
    manifest = SearXNGSearchPack().manifest()
    assert manifest["pack_id"] == PACK_ID
    assert manifest["provider"] == "searxng"
    assert manifest["license"] == "MIT"
    assert manifest["capabilities"] == [{
        "capability_id": "web.search",
        "description": "Search the public web through SearXNG.",
        "kind": "SERVICE",
        "side_effect_level": "READ_ONLY",
        "required_permissions": [],
        "maintained": True,
        "task_fit": 0.95,
    }]


def test_pack_rejects_unknown_deployment_config():
    with pytest.raises(ValueError, match="unknown SearXNG pack config"):
        SearXNGSearchPack().build_provider({"surprise": True})


def test_provider_filters_non_public_result_urls_and_bounds_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "agent runtime"
        return httpx.Response(200, json={"results": [
            {
                "title": "Public result",
                "url": "https://example.com/article",
                "content": "useful snippet",
                "engine": "fixture",
                "category": "general",
                "score": 2.0,
            },
            {
                "title": "Private result",
                "url": "http://127.0.0.1/private",
                "content": "must be filtered",
            },
        ]}, request=request)

    client = httpx.Client(
        base_url="http://127.0.0.1:8080",
        transport=httpx.MockTransport(handler),
    )
    provider = SearXNGSearchProvider(client=client)
    result = provider.invoke(WEB_SEARCH, {"query": "agent runtime", "limit": 2})
    assert result["ok"] is True
    assert result["result_count"] == 1
    assert result["results"][0]["url"] == "https://example.com/article"


def test_remote_plain_http_endpoint_is_rejected_by_default():
    with pytest.raises(ValueError, match="requires https"):
        SearXNGSearchProvider(base_url="http://example.com")


def test_installed_entry_point_binds_through_capability_pack_manager(tmp_path):
    catalog = InstalledCapabilityPackCatalog(trusted_pack_ids={PACK_ID})
    assert catalog.pack_ids() == (PACK_ID,)
    descriptor = catalog.descriptor(PACK_ID)
    assert descriptor.capability(WEB_SEARCH) is not None

    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="content-agent",
        max_side_effect_level="READ_ONLY",
    )
    policy = CapabilityPolicy()
    policy.auto_acquire["read_only_skill"] = True
    manager = CapabilityPackManager(runtime, catalog, policy=policy)
    manifest = AgentManifest(
        agent_type="content-agent",
        name="Content Agent",
        version="1",
        required_capabilities=(WEB_SEARCH,),
    )
    report = manager.reconcile_manifest(
        manifest,
        preferred_pack_by_capability={WEB_SEARCH: PACK_ID},
        config_by_pack={PACK_ID: {"base_url": "http://127.0.0.1:8080"}},
    )
    assert report.ready is True
    assert report.required[0].status is CapabilityPackBindingStatus.BOUND
    assert runtime.available_capabilities() == (WEB_SEARCH,)
    manager.close()


@pytest.mark.parametrize("config", [
    {"allow_insecure_http": "false"},
    {"timeout_seconds": 0},
    {"timeout_seconds": float("inf")},
])
def test_pack_rejects_invalid_deployment_config_types(config):
    with pytest.raises(ValueError):
        SearXNGSearchPack().build_provider(config)


def test_pack_uses_runtime_resilient_search_implementation():
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("engines"):
            return httpx.Response(200, json={"results": [{
                "title": "Official pricing",
                "url": "https://example.com/pricing",
                "content": "free tier pricing",
                "engine": "bing",
            }], "unresponsive_engines": []}, request=request)
        return httpx.Response(200, json={
            "results": [],
            "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
        }, request=request)
    client = httpx.Client(base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(handler))
    provider = SearXNGSearchProvider(client=client)
    result = provider.invoke(WEB_SEARCH, {"query": "provider pricing"})
    assert result["result_count"] == 1
    assert len(seen) == 2
    assert seen[1].url.params["engines"] == "bing,yep"
    assert result["search_attempts"][1]["mode"] == "fallback"


def test_pack_accepts_bounded_engine_policy_config():
    provider = SearXNGSearchPack().build_provider({
        "primary_engines": ["google", "duckduckgo web", "mwmbl"],
        "fallback_engines": ["google"],
    })
    assert provider.primary_engines == ("google", "duckduckgo web", "mwmbl")
    assert provider.fallback_engines == ("google",)


@pytest.mark.parametrize("config", [
    {"primary_engines": "google"},
    {"fallback_engines": "google"},
    {"primary_engines": [""]},
    {"fallback_engines": ["x"] * 9},
])
def test_pack_rejects_invalid_engine_policy_config(config):
    with pytest.raises(ValueError):
        SearXNGSearchPack().build_provider(config)
