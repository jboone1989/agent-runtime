# Crawl4AI web observation integration

Agent Runtime exposes provider-neutral web observation capabilities while keeping Crawl4AI outside the Runtime core.

## Capabilities

- `web.fetch`: fetch one public HTTP(S) page and return bounded markdown/metadata/links.
- `observation.http`: first-class compatibility capability for the same bounded fetch contract.
- `web.crawl`: bounded breadth-first traversal owned by Runtime, with depth/page/content budgets.

All three are `READ_ONLY` capabilities.

## Why Runtime owns crawl traversal

Crawl4AI 0.9 hardened the Docker HTTP API and rejects request-supplied executable/browser-internal configuration such as JavaScript, hooks, proxies, cookies, and request-defined deep-crawl strategies. Runtime therefore submits plain URL seeds to `/crawl` and performs traversal locally from returned links. This preserves the provider boundary and avoids turning the crawler service into a generic code-execution surface.

## Crawl4AI service

Use Crawl4AI 0.9+ for the secure-by-default HTTP server. A local deployment can listen on loopback:

```bash
docker pull unclecode/crawl4ai:latest
docker run -d --name crawl4ai --shm-size=1g -p 127.0.0.1:11235:11235 unclecode/crawl4ai:latest
```

For a network-exposed deployment, configure Crawl4AI's API token and place TLS in front of it. Runtime sends `Authorization: Bearer <token>` when `api_token` is supplied. Runtime refuses plaintext non-loopback Crawl4AI service URLs unless `allow_insecure_http=True` is explicitly selected.

## ApplicationRuntime path

```python
from agent_runtime.application import ApplicationRuntime
from agent_runtime.integrations import Crawl4AIHTTPProvider

runtime = ApplicationRuntime.bootstrap(
    "/var/lib/my-agent/runtime",
    subject_id="my-agent",
)
runtime.register_provider(
    Crawl4AIHTTPProvider(
        base_url="http://127.0.0.1:11235",
        api_token=None,
    )
)

receipt = runtime.invoke(
    "web.fetch",
    {"url": "https://example.com"},
)
print(receipt.payload["page"]["markdown"])
```

The invocation is recorded in the existing ApplicationRuntime receipt ledger.

## CapabilityInventory / ReadCapabilityGateway path

```python
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.invocation import ReadCapabilityGateway
from agent_runtime.capabilities.model import CapabilityRequirement
from agent_runtime.integrations import Crawl4AIHTTPProvider, register_web_provider

inventory = CapabilityInventory()
adapter = register_web_provider(Crawl4AIHTTPProvider(), inventory)
gateway = ReadCapabilityGateway(inventory, adapter)

result = gateway.invoke(
    CapabilityRequirement(capability_id="observation.http"),
    {"url": "https://example.com"},
)
```

## Resource and network policy

`WebFetchPolicy` provides Runtime-owned limits:

```python
from agent_runtime.integrations import Crawl4AIHTTPProvider, WebFetchPolicy

provider = Crawl4AIHTTPProvider(
    policy=WebFetchPolicy(
        max_pages=10,
        max_depth=2,
        max_chars_per_page=100_000,
        allowed_hosts=frozenset({"example.com"}),
        blocked_hosts=frozenset({"private.example.com"}),
    )
)
```

Runtime also rejects:

- non-HTTP(S) targets;
- credential-bearing URLs;
- localhost / `.local` / `.internal` targets;
- non-global literal IP addresses, including loopback, private, link-local, and cloud metadata addresses.

Crawl4AI's own current SSRF controls remain required because hostname DNS resolution happens at the crawler service boundary.

## Output contract

`web.fetch` / `observation.http` returns a single `page` containing:

- `url`
- `success`
- `status_code`
- bounded `markdown`
- `markdown_truncated`
- `markdown_original_chars`
- normalized `links`
- `metadata`
- `error`

`web.crawl` returns `pages`, `page_count`, `successful_pages`, `errors`, and the effective crawl `budget`.

Raw HTML is excluded by default. `include_html=True` is supported for a single fetch and is bounded by the same per-page character limit.
