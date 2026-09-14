# Jina Reader Capability Pack

Independent Agent Runtime capability pack providing provider-neutral `web.fetch`
through Jina Reader. It is not included in the root `agent-runtime` wheel.

Install only in Runtime environments that need page retrieval:

```bash
pip install ./packs/jina-reader
```

The distribution registers entry point `jina-reader` in
`agent_runtime.capability_packs`. Deployment must explicitly trust that pack ID.

Deployment configuration:

- `reader_base_url`: defaults to `https://r.jina.ai`.
- `api_key`: optional deployment-only Jina API key; never enters requests/results.
- `timeout_seconds`: bounded HTTP timeout.
- `max_chars`: deployment result ceiling, hard-capped by the provider.
- `allow_insecure_http`: explicit opt-in for remote plain HTTP endpoints.

Agent requests accept only `url` and `max_chars`; arbitrary headers, cookies,
browser code, credentials, proxies, or upload controls are not exposed.
