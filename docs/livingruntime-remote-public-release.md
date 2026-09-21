# LivingRuntime Remote Public Release Track

## Goal

LivingRuntime Remote is an independent lightweight product for bounded remote development and operations. It does not require the persistent autonomous workflow provided by pi-remote.

User flow:

```
ChatGPT
→ LivingRuntime Remote
→ authenticated transport
→ bounded tools
→ user's machine/project
```

## 0.4.3 engineering status

Completed:

- standalone plugin/package identity
- full MCP capability registry
- bounded SSH-backed local transport
- Secure MCP Tunnel support
- public HTTPS relay
- embedded OAuth flow with PKCE
- device pairing and revocation
- public relay rate limiting
- offline-device detection
- queued-task cancellation on relay timeout
- public relay exposure of apply_patch and diagnostics
- privacy/terms/submission metadata in the release implementation
- end-to-end OAuth → pairing → public relay → remote connection_status validation

The public release implementation and relay are maintained in `pi-remote/main`. This is a repository/transport sharing decision; the LivingRuntime Remote product remains separate from the pi-remote autonomous coding product.

## Remaining launch dependencies

These are release/distribution tasks rather than missing core Remote code:

- move from the temporary sslip.io hostname to the final branded production domain
- complete final domain/submission verification
- publish the final support/privacy/terms URLs on that domain
- run the marketplace review against that exact production configuration

## Relationship with pi-remote

LivingRuntime Remote is the simple remote capability entry point. pi-remote adds persistent sessions, planning, checkpoints, role workflows, and autonomous development on top.
