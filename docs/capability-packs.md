# Dynamic Capability Packs

Agent Runtime owns capability governance, not every capability implementation.
Domain agents declare provider-neutral needs; deployment installs and trusts only
the packs appropriate for that agent instance.

A pack is a separate Python distribution registered under the entry-point group
`agent_runtime.capability_packs`. Runtime never runs `pip`, searches PyPI, or
loads arbitrary installed packages on an agent's request. `trusted_pack_ids` is
an explicit deployment allowlist and is checked before an entry point is loaded.

The flow is:

```text
Agent intent / manifest
        ↓
CapabilityNeed
        ↓
CapabilityResolver
        ↓
trusted installed Capability Pack
        ↓
least-authority selected-capability wrapper
        ↓
ApplicationRuntime side-effect + permission gates
        ↓
provider invocation + normal Runtime receipt
```

Existing P0-P2 integrations remain compatibility/reference providers. New
domain-specific capabilities should normally ship as external packs instead of
adding SDKs and provider code to the Runtime core.
## Agent declaration

`capabilities` continues to describe domain capabilities the agent itself
provides. `required_capabilities` and `optional_capabilities` describe external
capabilities the agent wants its Runtime instance to bind.

```yaml
required_capabilities:
  - web.search
  - capability_id: image.search
    intent: find attributed visual evidence
optional_capabilities:
  - capability_id: image.generate
    intent: create an original illustration when search is insufficient
    max_side_effect_level: EXTERNAL_SIDE_EFFECT
    required_permissions: [media.generate]
  - capability_id: video.generate
    max_side_effect_level: EXTERNAL_SIDE_EFFECT
    required_permissions: [media.generate]
```

Short-form declarations default to a `READ_ONLY` maximum. A pack claiming a
higher side-effect level is rejected as incompatible with that need. Required
and optional declarations cannot overlap. Missing optional capabilities do not
make a manifest unready; unresolved required capabilities do.

A runtime intent can also construct `CapabilityNeed` directly. This uses the
same resolver path as manifest reconciliation; domain code still names only the
capability, not a vendor SDK.
## Pack contract

A provider distribution exposes one cheap pack factory and defers heavy model,
network, or SDK initialization until a capability is actually selected.

```toml
[project.entry-points."agent_runtime.capability_packs"]
content-media = "runtime_content_media:build_pack"
```

The returned object implements:

```python
class Pack:
    pack_id = "content-media"
    version = "1.0.0"

    def manifest(self): ...
    def build_provider(self, config=None): ...
```

The pack manifest declares `pack_id`, `version`, underlying `provider`, SPDX-like
`license`, and capability rows. Each row declares `capability_id`, `kind`,
`side_effect_level`, and `required_permissions`. The provider built later must
repeat the same side-effect and permission contract for the selected capability;
a mismatch fails closed.

`InstalledCapabilityPackSource` translates trusted pack declarations into normal
`CapabilityCandidate` objects, so existing Resolver evaluation, search evidence,
license policy, side-effect constraints, and reuse-before-create semantics remain
canonical.
## Authority and restart boundary

Binding never raises authority. `CapabilityPackManager` may register a provider,
but every invocation still passes through `ApplicationRuntime`'s configured
side-effect ceiling and permission allowlist. Operator approval to bind a pack
does not add a permission to the Runtime.

Automatic binding is governed by `CapabilityPolicy`. The default policy remains
closed. A deployment may opt into automatic READ_ONLY binding for already
installed, trusted packs. Capabilities with declared permissions still require
explicit binding approval; invocation remains independently gated.

Only the selected capability is exposed. If a media pack implements
`image.search`, `image.generate`, and `video.generate`, a request for
`image.search` does not register the other two.

Provider configuration can contain deployment secrets, but Runtime stores only a
SHA-256 configuration fingerprint in manager memory to reject in-process config
changes. Binding results and search evidence do not contain configuration values.

Pack bindings are intentionally reconstructed at process start from the agent
manifest or re-issued `CapabilityNeed` plus deployment trust/config. Runtime does
not persist provider secrets or silently preserve a one-off dynamic authority
request. Persistent intent belongs to the agent/VirtualBrain state; deployment
trust and credentials belong to deployment configuration.
