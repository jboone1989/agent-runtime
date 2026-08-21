# Checkpointing and lifecycle consistency

Status: frozen design rule. First implementation is **Life Loop Hardening**, after the Life Loop Integration freeze. Do not fold snapshot/restore into `MotivationPort`.

## Why a separate coordinator

Life Loop Integration may persist Runtime state and mind session state side by side in tests. That is a proof, not the long-term lifecycle. Formal multi-participant restore needs one envelope, one logical cycle, and all-or-nothing digest checks.

## Interfaces (Runtime-owned, mind-agnostic)

```python
class SnapshotParticipant(Protocol):
    participant_id: str
    schema_version: str
    def snapshot(self) -> dict: ...
    def restore(self, payload: dict) -> None: ...
```

```python
@dataclass
class CheckpointComponent:
    participant_id: str
    schema_version: str
    payload_digest: str
    payload: dict | None
    external_ref: str | None

@dataclass
class CheckpointEnvelope:
    checkpoint_version: str
    checkpoint_id: str
    agent_id: str
    logical_cycle: int
    created_at: datetime
    components: list[CheckpointComponent]
```

Suggested Hardening layout:

```text
src/agent_runtime/lifecycle/participant.py
src/agent_runtime/lifecycle/checkpoint.py
src/agent_runtime/lifecycle/manifest.py
tests/test_checkpoint_coordinator.py
```

## Coordinator rules

1. Collect every participant snapshot in the same logical cycle.
2. Validate `agent_id` and cycle.
3. Compute each payload digest.
4. Write the envelope atomically (temp file, then rename).
5. If any participant fails, the last successful checkpoint stays valid.
6. On restore, verify every digest before restoring any component.
7. Runtime must not interpret VirtualBrain-private PAD / CORT / interoception fields.

First version is a local aggregate snapshot. No distributed two-phase commit. Remote participants later use `external_ref + digest`.

## First version must support

- Runtime participant
- any opaque mind participant
- single-file or single-directory atomic checkpoint
- digest verification
- participant schema version
- reject `agent_id` / cycle mismatch
- unfinished temp checkpoints must not replace the last success
- old Runtime snapshots remain restorable

`AgentRuntime` must not read or interpret mind payload.

## Clock domains (do not equate)

| Clock | Owner | Use |
|---|---|---|
| Runtime Clock | Runtime | operations, scheduling, persistence |
| VirtualBrain subjective / research time | session | must receive explicit `dt` or a controlled config; Runtime must not guess it |
| Network time | protocol | expiry, signature windows, Evidence order |

## Lifecycle errors

Unknown identity, digest, schema, or authorization failures must raise. Do not treat unknown as false/zero. Do not persist secrets, provider handles, hardware handles, or live RNG objects.

VirtualBrain `ContractV1AgencySession` should speak SnapshotParticipant *semantics* without depending on a Runtime interface package. A Runtime integration adapter performs structural adaptation.
