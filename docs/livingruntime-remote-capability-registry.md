# LivingRuntime Remote Capability Registry

## Goal

Provide one source of truth for the LivingRuntime Remote capability surface and prevent drift between MCP discovery, plugin metadata, public relay, and tests.

Canonical standalone registry:

```
plugins/livingruntime-remote/scripts/contract.py
```

The `REMOTE_TOOLS` tuple is authoritative for the 0.4.3 surface:

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

`capabilities` must report missing tools explicitly. Tests and public relay surfaces must derive from or validate against this registry rather than maintaining a smaller hand-written list.

The public relay implementation is maintained in `pi-remote/main`; LivingRuntime Remote remains an independent product even though transport/release code shares that repository.
