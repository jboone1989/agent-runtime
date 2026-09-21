# LivingRuntime Remote Release Checklist

## Product

- [x] standalone plugin package exists
- [x] LivingRuntime Remote and pi-remote product boundaries are explicit
- [x] README explains setup and capabilities
- [x] product identity is `livingruntime.remote`
- [x] shipped capability version is `0.4.3`

## Runtime

- [x] connection_status reports runtime identity
- [x] capabilities reports available and missing tools
- [x] capability schema hash is exposed
- [x] missing tools are explicit
- [x] installed runtime and gateway report 0.4.3

## Capability

Required 0.4.3 capability surface:

- connection_status
- capabilities
- list_projects
- read_file
- write_file
- list_dir
- git
- exec
- process
- systemd
- logs
- apply_patch
- diagnostics

## Security

- [x] no private keys stored by plugin
- [x] bounded roots enforced
- [x] destructive operations audited
- [x] service control allowlisted
- [x] public relay uses authenticated OAuth
- [x] pairing attempts are rate limited
- [x] stale/offline devices do not receive newly queued work

## Release validation

- [x] plugin self-check
- [x] MCP tool discovery tests
- [x] read/write/git/log workflow tests
- [x] apply_patch atomicity tests
- [x] public relay OAuth tests
- [x] device pairing tests
- [x] external OAuth → pairing → relay → connection_status smoke test
- [x] relay timeout does not leave unclaimed work queued
- [ ] replace temporary sslip.io hostname with final branded production domain
- [ ] complete final marketplace/submission verification using the production domain

Public relay source and deployment code are maintained in `pi-remote/main`.
