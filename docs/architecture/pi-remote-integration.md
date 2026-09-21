# Pi Remote Integration Boundary

Agent Runtime is the persistent digital-life runtime. Pi Remote is a separate coding runtime.

## Ownership

Agent Runtime owns identity, continuity, goals, viability, memory governance, embodiment, learning, and general capability orchestration.

Pi Remote owns coding sessions, coding roles/workflows, checkpoints, worktree coordination, coding credential/capability handling, and remote coding transport integrations.

LivingRuntime Remote plugin/gateway source moved to the Pi Remote repository. Agent Runtime no longer owns that transport implementation.

## Dependency direction

Agent Runtime may use Pi Remote as an optional coding capability:

```text
Agent Runtime
  -> PiRemoteCapability adapter
  -> stable Pi Remote MCP / HTTP / SDK protocol
  -> Pi Remote
  -> Pi harness
```

Pi Remote must not import Agent Runtime internals. Agent Runtime must not duplicate Pi Remote Session, Role, Checkpoint, WorkspaceLease, or review logic.

Agent Runtime must continue to boot and operate when Pi Remote is absent.

## Why there is no direct adapter yet

The Pi Remote public protocol is still being stabilized. A direct import or private-CLI subprocess adapter now would create accidental coupling that would need to be removed later.

The adapter should be implemented only against a stable Pi Remote boundary and should expose capabilities such as:

- create or resume a coding Session
- submit a bounded coding goal
- inspect Session/workflow status
- obtain Checkpoint/Review evidence
- request a policy-gated commit/deploy action
- receive completion, finding, or blocked outcomes

Secrets remain outside Agent Runtime model context and are represented by capability/credential references.

## Migration rule

New remote-development transport work belongs in `jboone1989/pi-remote`.

Agent Runtime changes should only add consumer-side integration once the Pi Remote protocol is stable enough to avoid importing implementation details.
