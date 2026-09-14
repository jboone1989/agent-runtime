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
from runtime_jina_reader_pack import JinaReaderPack, JinaReaderProvider, PACK_ID, WEB_FETCH


def _client(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json=payload, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_manifest_is_read_only_web_fetch():
    row = JinaReaderPack().manifest()["capabilities"][0]
    assert row["capability_id"] == "web.fetch"
    assert row["side_effect_level"] == "READ_ONLY"
    assert row["required_permissions"] == []


def test_provider_normalizes_and_bounds_reader_content():
    provider = JinaReaderProvider(
        max_chars=20,
        client=_client({"data": {
            "url": "https://example.com/article", "title": "Example", "content": "abcdefghijklmnopqrstuvwxyz"
        }}),
    )
    result = provider.invoke(WEB_FETCH, {"url": "https://example.com/article", "max_chars": 12})
    assert result["ok"] is True
    assert result["canonical_capability_id"] == WEB_FETCH
    assert result["page"] == {
        "url": "https://example.com/article", "title": "Example", "markdown": "abcdefghijkl", "success": True,
    }


def test_provider_rejects_private_credentials_unknown_fields_and_challenges():
    provider = JinaReaderProvider(client=_client({"data": {"content": "unused"}}))
    for url in ("http://127.0.0.1/private", "http://localhost/a", "https://u:p@example.com/a"):
        with pytest.raises(ValueError):
            provider.invoke(WEB_FETCH, {"url": url})
    with pytest.raises(ValueError, match="unknown web.fetch parameter"):
        provider.invoke(WEB_FETCH, {"url": "https://example.com", "headers": {"x": "y"}})
    challenged = JinaReaderProvider(client=_client({"data": {
        "url": "https://example.com", "title": "Just a moment...", "content": "Performing security verification"
    }}))
    with pytest.raises(ValueError, match="access-challenge"):
        challenged.invoke(WEB_FETCH, {"url": "https://example.com"})


def test_pack_rejects_unknown_or_invalid_deployment_config():
    with pytest.raises(ValueError, match="unknown Jina Reader pack config"):
        JinaReaderPack().build_provider({"surprise": True})
    for config in ({"allow_insecure_http": "false"}, {"timeout_seconds": 0}, {"timeout_seconds": float("inf")}, {"api_key": 7}):
        with pytest.raises(ValueError):
            JinaReaderPack().build_provider(config)


def test_installed_entry_point_binds_web_fetch(tmp_path):
    catalog = InstalledCapabilityPackCatalog(trusted_pack_ids={PACK_ID})
    assert PACK_ID in catalog.pack_ids()
    runtime = ApplicationRuntime.bootstrap(tmp_path, subject_id="content-agent", max_side_effect_level="READ_ONLY")
    policy = CapabilityPolicy()
    policy.auto_acquire["read_only_skill"] = True
    manager = CapabilityPackManager(runtime, catalog, policy=policy)
    manifest = AgentManifest(
        agent_type="content-agent", name="Content Agent", version="1", required_capabilities=(WEB_FETCH,),
    )
    report = manager.reconcile_manifest(manifest, preferred_pack_by_capability={WEB_FETCH: PACK_ID})
    assert report.ready is True
    assert report.required[0].status is CapabilityPackBindingStatus.BOUND
    assert runtime.available_capabilities() == (WEB_FETCH,)
    manager.close()


def test_api_key_is_deployment_only_and_not_exposed():
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {
            "url": "https://example.com", "title": "Example", "content": "reader content"
        }}, request=request)
    provider = JinaReaderProvider(api_key="secret-test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.invoke(WEB_FETCH, {"url": "https://example.com"})
    assert seen["authorization"] == "Bearer secret-test-key"
    assert "secret-test-key" not in repr(provider.manifest())
    assert "secret-test-key" not in repr(result)
