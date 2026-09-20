# Public release path

Private ChatGPT use and public Plugins Directory publication are different tracks.

## Private ChatGPT ordinary chat (supported plans)

Use OpenAI Secure MCP Tunnel. The MCP server stays on loopback or stdio. `scripts/tunnel.py` is the Windows-safe operator entrypoint. This is production-ready as a **private** connection. It is not a public plugin listing.

Required on the OpenAI side: a `tunnel_id`, a runtime API key, tunnel workspace association, and a ChatGPT plan/workspace that can create developer-mode apps.

## Public Plugins Directory

OpenAI documents that public plugins need a stable, publicly reachable HTTPS MCP endpoint. Secure MCP Tunnel does not satisfy that requirement.

Before submitting `chatgpt-app-submission.json` through the [plugin submission portal](https://developers.openai.com/plugins/deploy/submission):

1. Deploy the Remote MCP gateway or an equivalent bounded server behind public HTTPS. Do not bind `0.0.0.0:8765`.
2. Use supported user authentication for a published plugin, normally OAuth 2.1 with PKCE. The current bearer token is a private-deployment credential, not a consumer login.
3. Serve `/.well-known/openai-apps-challenge` for domain verification.
4. Publish website, support, privacy, and terms URLs. This package includes `PRIVACY.md` and `TERMS.md`.
5. Verify developer/business identity in the OpenAI Platform and grant Apps Management write access.
6. Scan tools, confirm `readOnlyHint` / `openWorldHint` / `destructiveHint` on every tool, and rerun the five positive plus three negative cases.

## Already prepared in this repository

- Explicit tool names including `connection_status`, `read_file`, `git`, and `logs`
- Read/write/destructive/open-world annotations
- Loopback-only HTTP defaults and a Secure MCP Tunnel runner
- Portable `plugin.json`, `.app.json` mapping file, submission JSON, privacy/terms, and logo
- Discovery tests for MCP `tools/list`

## Not a code bug

A Plus account that can see Remote Desktop Commander but cannot create or install this private write-capable app is hitting OpenAI’s documented product boundary. Shipping more SSH features will not change that. The remaining publish blocker is OpenAI review plus a public HTTPS MCP endpoint if you want ordinary-chat Plus users to install it from the Plugins Directory.
