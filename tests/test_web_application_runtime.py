from __future__ import annotations

import httpx

from agent_runtime.application import ApplicationRuntime
from agent_runtime.integrations.web import WEB_FETCH, Crawl4AIHTTPProvider


def test_application_runtime_registers_and_invokes_crawl4ai_provider(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crawl":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/",
                            "success": True,
                            "markdown": "runtime web observation",
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    client = httpx.Client(
        base_url="http://crawl4ai.test",
        transport=httpx.MockTransport(handler),
    )
    provider = Crawl4AIHTTPProvider(client=client)
    runtime = ApplicationRuntime.bootstrap(tmp_path, subject_id="subject:web-test")
    runtime.register_provider(provider)

    assert WEB_FETCH in runtime.available_capabilities()
    receipt = runtime.invoke(WEB_FETCH, {"url": "https://example.com/"})

    assert receipt.provider == "crawl4ai"
    assert receipt.payload["ok"] is True
    assert receipt.payload["page"]["markdown"] == "runtime web observation"
    assert runtime.receipts[-1]["capability_id"] == WEB_FETCH
