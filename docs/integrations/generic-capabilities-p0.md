# Generic capability integrations — P0

This document describes the first provider-neutral generic integrations added to
Agent Runtime. Runtime owns capability identity, authorization, budgets, and
evidence. Upstream projects remain replaceable implementation providers.

## Web capability ownership

`observation.http` remains owned by `RuntimeObservationProvider` and HTTPX for
bounded HTTP/API reads. Crawl4AI exposes only `web.fetch` and `web.crawl`.
SearXNG is the first provider for `web.search`.

This separation is intentional:

- `observation.http`: deterministic bounded HTTP/API observation.
- `web.search`: discover public URLs from a query.
- `web.fetch`: render one public web page into LLM-friendly content.
- `web.crawl`: Runtime-owned bounded traversal over rendered pages.

## MCP provider adapter

Install the optional SDK with `pip install -e '.[mcp]'`.

MCP discovery is evidence, not authority. An MCP server may advertise arbitrary
tools, but Runtime only exposes tools that have an explicit `MCPToolBinding`.
The binding supplies the provider-neutral capability ID, side-effect level, and
required permissions. Unmapped tools remain candidates with the conservative
`IRREVERSIBLE` classification and `mcp.unclassified` permission marker; they are
not registered as ACTIVE capabilities.

ApplicationRuntime still enforces its side-effect ceiling and permission set
before `MCPProvider.invoke()` is reached. Capability Fabric registration calls
`verify_bindings()` and fails closed if a configured tool is no longer
advertised by the server.

## Document parsing

Install `pip install -e '.[documents]'` for MarkItDown PDF/DOCX/PPTX/XLSX
support.

`MarkItDownDocumentProvider` exposes:

- `document.parse`: bounded Markdown conversion.
- `document.extract`: bounded plain-text projection of the converted document.

The provider accepts only files below explicitly bound roots, pins the root,
opens path components without following symlinks, applies an extension allowlist,
and enforces input byte and output character budgets. Runtime passes a binary
stream to MarkItDown; it does not pass arbitrary URIs or user-controlled absolute
paths. MarkItDown plugins are disabled by default.

## Secret resolver

The secret resolver is internal infrastructure, not a model-facing capability.
There is deliberately no `secret.read` capability.

`SecretRef` contains only provider/namespace/name metadata and a fingerprint
derived from that reference. `SecretValue` redacts its string and repr output;
`reveal()` is intended to be called only at the concrete provider boundary.
Runtime evidence may contain `SecretRef.evidence()`, but never the resolved
credential value.

`KeyringSecretResolver` supports an explicit OS-keyring service allowlist and is
available through `pip install -e '.[secrets]'`. Production deployments may add
another `SecretResolver` implementation such as Vault without changing agent
capability contracts.

## Acceptance rules

Every model-facing provider must retain a provider-neutral capability ID,
explicit side-effect classification, Runtime-owned resource bounds, invocation
receipts, and failure/provider-loss tests. Generic integrations must reuse the
existing ApplicationRuntime and Capability Inventory instead of introducing a
second registry or controller loop.
