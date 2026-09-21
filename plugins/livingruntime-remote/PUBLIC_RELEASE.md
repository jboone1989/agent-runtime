# Public release path

LivingRuntime Remote has two transport paths: local/private use and public ChatGPT distribution.

The 0.4.3 public architecture is:

```
ChatGPT
  -> LivingRuntime Remote
  -> OAuth-protected public relay
  -> paired connector / Secure MCP transport
  -> bounded tools
  -> user's machine/project
```

## Prepared

- standalone product identity and plugin package
- full 0.4.3 capability registry
- bounded tool policy
- SSH-backed transport
- Secure MCP Tunnel support
- audit logging and output/time limits
- public HTTPS relay
- OAuth + PKCE flow
- device pairing/revocation
- pairing rate limits
- offline-device detection and timeout cleanup
- submission metadata, privacy and terms in the public release implementation
- end-to-end public relay smoke validation

## Before marketplace submission

The remaining work is deployment/distribution configuration rather than missing core capability code:

1. Move from the temporary sslip.io endpoint to the final branded production domain.
2. Publish final support, privacy, and terms URLs on that domain.
3. Complete domain/submission verification.
4. Run the final marketplace test matrix against that exact production configuration.

The public relay source is maintained in `pi-remote/main`; this standalone package remains the lightweight LivingRuntime Remote product surface.
