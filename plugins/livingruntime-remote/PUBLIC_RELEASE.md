# Public release path

The local plugin is the short path for private development. A public ChatGPT plugin is a separate release track.

## Already prepared

The shared Remote MCP server under `tools/remote-mcp-gateway/` has:
- explicit read/write/destructive/open-world tool annotations;
- bounded file, command, Git, process, systemd, and log surfaces;
- localhost-only deployment defaults;
- audit logging and output/time limits.

## Still required before public submission

1. Deploy the Remote MCP endpoint behind stable public HTTPS.
2. Replace the private bearer-token deployment model with supported user authentication suitable for a published plugin, normally OAuth 2.1 with PKCE.
3. Serve the required domain verification challenge from the public hostname.
4. Publish website, support, privacy-policy, and terms URLs.
5. Run positive and negative submission test cases against the production endpoint.
6. Submit the remote MCP plugin through the OpenAI plugin submission flow after developer/business verification.

Do not expose the current localhost bearer-token gateway directly to the public internet merely to satisfy the public-plugin requirement.

The local SSH plugin does not wait on this track and does not need a public MCP endpoint.
