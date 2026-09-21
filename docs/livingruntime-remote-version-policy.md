# LivingRuntime Remote Version Policy

## Goal

Prevent plugin, runtime, public relay, and capability-schema drift.

## Authoritative version

The standalone package version is defined in:

```
plugins/livingruntime-remote/scripts/contract.py
```

For the current release:

```
PLUGIN_VERSION = "0.4.3"
REMOTE_IDENTITY = "livingruntime.remote"
```

The same contract also defines `REMOTE_TOOLS` and the capability `schema_hash`.

## Compatibility

A Remote connection is healthy only when:

- the runtime identifies as `livingruntime.remote`
- the expected capability registry is present
- `capabilities.missing` is empty
- the reported schema hash matches the expected tool/version contract
- the public relay and installed gateway expose the same release capability surface

Version mismatch must be visible rather than silently ignored.

## Runtime reporting

`connection_status`, `capabilities`, and `diagnostics` together provide the release handshake. A healthy 0.4.3 installation reports the runtime/gateway version, schema hash, available tool list, and explicit missing tools.

The public relay implementation is maintained in `pi-remote/main`; changes to the public capability surface must update the shared 0.4.3 contract and its tests before deployment.
