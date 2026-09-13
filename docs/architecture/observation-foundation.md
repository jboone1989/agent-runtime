# Runtime Observation Foundation

Status: implemented on the current provider-foundation slice; independent
review pending.

## Ownership decision

Agent Runtime owns concrete access to the external environment. VirtualBrain
owns what an observation means to the subject after it crosses the perception
boundary. This is a correction to the earlier pattern where VirtualBrain
contained concrete clock, workspace, and Git adapters.

```text
External world
  -> Agent Runtime Capability Inventory / ReadCapabilityGateway
  -> ObservationResult (strict JSON)
  -> VirtualBrain RuntimeObservationBridge
  -> PerceptionGateway -> Percept
```

The action direction stays separate:

```text
VirtualBrain decision -> Runtime execution policy -> action provider -> world
```

## One provider, existing registry

`RuntimeObservationProvider` exposes these read-only capability IDs:

- `observation.git`
- `observation.workspace`
- `observation.clock`
- `observation.environment`
- `observation.system`
- `observation.process`
- `observation.http`

`register_observation_provider` creates ordinary `CapabilityBinding` entries
with `kind=SERVICE`, `access_mode=LOCAL`, and
`side_effects=READ_ONLY`. Invocation uses the existing
`ReadCapabilityGateway`. There is no observation-specific registry.

Every call returns `ObservationResult`: provider and capability identity,
completion timestamp, strict JSON data, bounded evidence, and either success or
an explicit bounded error. Invocation acceptance and observation success are
different facts.

## Bounds and adopted libraries

- Git uses Dulwich and returns repository root, HEAD, branch/detached state,
  staged, modified, deleted, and untracked paths. The provider never invokes
  Git CLI and does not promise porcelain byte compatibility.
- Workspace uses `pathlib`/`os.scandir`; roots are configured by ID, symlinks
  and reparse points are not followed, and list/stat/read/tree operations have
  explicit depth, entry, and byte limits.
- Clock and environment use the standard library. Environment variables are
  opt-in through a configured allowlist.
- System and process observations use psutil and expose no command line or
  process environment secrets. Process listing is capped.
- HTTP uses HTTPX, allows only GET/HEAD, binds target hosts and schemes, caps
  time and response bytes, suppresses sensitive response headers, and defaults
  to no redirects. Optional redirects are same-origin and bounded.

Filesystem events and database reads use the same thin extension protocol when
a concrete consumer exists. They are not implemented in this slice. Mutation
methods, shell, file writes, Git changes, HTTP mutation, and database updates
remain action capabilities under stricter governance.
