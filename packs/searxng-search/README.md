# SearXNG Search Capability Pack

This is an independently installable Agent Runtime capability pack. It exposes
the provider-neutral capability `web.search` through a SearXNG service.

It is intentionally not part of the `agent-runtime` wheel. Install it only in
Runtime environments that need web search:

```bash
pip install ./packs/searxng-search
```

The Python distribution registers entry point `searxng-search` in
`agent_runtime.capability_packs`. Deployment must also include that pack ID in
the Runtime trusted-pack allowlist.

Supported deployment configuration:

- `base_url`: SearXNG service URL; defaults to `http://127.0.0.1:8080`.
- `timeout_seconds`: HTTP timeout.
- `allow_insecure_http`: explicit opt-in for remote plain HTTP.
- `max_results`: provider result ceiling.
- `max_query_chars`: query-length ceiling.

Agent code does not receive these provider settings. It requests only
`web.search`; Runtime resolves and binds the pack under deployment policy.
