from __future__ import annotations

import json

import httpx
import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.invocation import ReadCapabilityGateway
from agent_runtime.capabilities.model import (
    CapabilityLifecycleState,
    CapabilityRequirement,
    SideEffectLevel,
)
from agent_runtime.integrations.web import (
    OBSERVATION_HTTP,
    WEB_CRAWL,
    WEB_FETCH,
    Crawl4AIHTTPProvider,
    WebFetchPolicy,
    register_web_provider,
)


def _client(handler):
    return httpx.Client(
        base_url="http://crawl4ai.test",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-token"},
    )


def test_manifest_registers_fetch_crawl_and_observation_http() -> None:
    provider = Crawl4AIHTTPProvider(
        client=_client(lambda request: httpx.Response(200, json={"status": "ok"}))
    )
    inventory = CapabilityInventory()
    adapter = register_web_provider(provider, inventory)

    assert inventory.get(WEB_FETCH).state is CapabilityLifecycleState.ACTIVE
    assert inventory.get(WEB_CRAWL).side_effects is SideEffectLevel.READ_ONLY
    assert inventory.get(OBSERVATION_HTTP).state is CapabilityLifecycleState.ACTIVE
    assert inventory.get(OBSERVATION_HTTP).provider == "crawl4ai"
    assert adapter.manifest()["provider"] == "crawl4ai"


def test_fetch_uses_bounded_plain_crawl_request_and_normalizes_markdown() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/crawl":
            body = json.loads(request.content)
            assert body == {"urls": ["https://example.com/article"]}
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/article",
                            "success": True,
                            "status_code": 200,
                            "markdown": {"fit_markdown": "hello world"},
                            "links": {"internal": [{"href": "/next"}]},
                            "metadata": {"title": "Example"},
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    provider = Crawl4AIHTTPProvider(client=_client(handler))
    result = provider.invoke(WEB_FETCH, {"url": "https://example.com/article"})

    assert result["ok"] is True
    assert result["capability_id"] == WEB_FETCH
    assert result["canonical_capability_id"] == WEB_FETCH
    assert result["page"]["markdown"] == "hello world"
    assert result["page"]["links"] == ["/next"]
    assert seen[0].headers["authorization"] == "Bearer test-token"


def test_read_gateway_invokes_observation_http_through_same_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com",
                        "success": True,
                        "markdown": "ok",
                    }
                ]
            },
        )

    inventory = CapabilityInventory()
    adapter = register_web_provider(
        Crawl4AIHTTPProvider(client=_client(handler)), inventory
    )
    gateway = ReadCapabilityGateway(inventory, adapter)

    result = gateway.invoke(
        CapabilityRequirement(capability_id=OBSERVATION_HTTP),
        {"url": "https://example.com"},
    )

    assert result.accepted is True
    assert result.result["capability_id"] == OBSERVATION_HTTP
    assert result.result["canonical_capability_id"] == WEB_FETCH
    assert result.result["page"]["markdown"] == "ok"


def test_crawl_performs_runtime_bfs_with_depth_and_page_budget() -> None:
    pages = {
        "https://example.com/": {
            "markdown": "root",
            "links": {
                "internal": [{"href": "/a"}, {"href": "/b"}],
                "external": [{"href": "https://other.example/x"}],
            },
        },
        "https://example.com/a": {
            "markdown": "a",
            "links": {"internal": [{"href": "/c"}]},
        },
        "https://example.com/b": {"markdown": "b", "links": {}},
        "https://example.com/c": {"markdown": "c", "links": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        url = body["urls"][0]
        row = {"url": url, "success": True, **pages[url]}
        return httpx.Response(200, json={"results": [row]})

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(
            max_pages=5,
            max_depth=3,
            max_chars_per_page=1000,
            task_poll_seconds=0,
        ),
    )
    result = provider.invoke(
        WEB_CRAWL,
        {"url": "https://example.com", "max_depth": 2, "max_pages": 3},
    )

    assert result["ok"] is True
    assert result["page_count"] == 3
    assert [page["url"] for page in result["pages"]] == [
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert [page["depth"] for page in result["pages"]] == [0, 1, 1]
    assert result["budget"]["max_pages"] == 3


def test_host_allowlist_and_blocklist_apply_to_discovered_links() -> None:
    pages = {
        "https://example.com/": {
            "markdown": "root",
            "links": {
                "internal": [{"href": "/ok"}, {"href": "https://blocked.example.com/x"}]
            },
        },
        "https://example.com/ok": {"markdown": "ok", "links": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = json.loads(request.content)["urls"][0]
        return httpx.Response(
            200,
            json={"results": [{"url": url, "success": True, **pages[url]}]},
        )

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(
            allowed_hosts=frozenset({"example.com"}),
            blocked_hosts=frozenset({"blocked.example.com"}),
            task_poll_seconds=0,
        ),
    )
    result = provider.invoke(
        WEB_CRAWL,
        {
            "url": "https://example.com",
            "max_depth": 1,
            "max_pages": 5,
            "include_external": True,
        },
    )

    assert [page["url"] for page in result["pages"]] == [
        "https://example.com/",
        "https://example.com/ok",
    ]


def test_content_is_truncated_at_runtime_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com",
                        "success": True,
                        "markdown": "x" * 20,
                    }
                ]
            },
        )

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(max_chars_per_page=10, task_poll_seconds=0),
    )
    result = provider.invoke(
        WEB_FETCH, {"url": "https://example.com", "max_chars": 10}
    )

    assert result["page"]["markdown"] == "x" * 10
    assert result["page"]["markdown_truncated"] is True
    assert result["page"]["markdown_original_chars"] == 20


def test_legacy_async_task_response_is_polled_without_recursion() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/crawl":
            return httpx.Response(200, json={"task_id": "task-1"})
        if request.url.path == "/task/task-1":
            calls += 1
            if calls == 1:
                return httpx.Response(200, json={"status": "running", "task_id": "task-1"})
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "task_id": "task-1",
                    "result": {
                        "url": "https://example.com/",
                        "success": True,
                        "markdown": "done",
                    },
                },
            )
        raise AssertionError(request.url)

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(task_poll_seconds=0, task_poll_attempts=3),
    )
    result = provider.invoke(WEB_FETCH, {"url": "https://example.com/"})

    assert result["ok"] is True
    assert result["page"]["markdown"] == "done"
    assert calls == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/secret",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
    ],
)
def test_fetch_rejects_private_non_http_and_credential_targets(url: str) -> None:
    provider = Crawl4AIHTTPProvider(
        client=_client(lambda request: httpx.Response(500))
    )
    with pytest.raises(ValueError):
        provider.invoke(WEB_FETCH, {"url": url})


def test_remote_plaintext_crawl4ai_service_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="requires https"):
        Crawl4AIHTTPProvider(base_url="http://crawl.internal:11235")


def test_health_is_bounded_and_provider_neutral() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "0.9.x"})

    provider = Crawl4AIHTTPProvider(client=_client(handler))
    health = provider.health()

    assert health["ok"] is True
    assert health["provider"] == "crawl4ai"
