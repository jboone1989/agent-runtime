# Capability modularization

Status: Accepted  
Date: 2026-10-04

## Principle

Agent Runtime is the capability orchestrator, not the implementation home for every capability.

A concrete agent such as Ferro owns only agent-specific identity, goals, policy, missions, and domain decisions. Reusable abilities are provided through stable capability contracts.

Target dependency direction:

```text
Agent (Ferro, Trading, future agents)
        |
        v
Agent Runtime
  capability routing
  lifecycle / jobs / receipts
  process supervision
  state transitions
        |
        +--> VirtualBrain / brain capability
        +--> Memory capability
        +--> Learning capability
        +--> Browser / Computer Use capability
        +--> Research capability
        +--> Channel capabilities
        +--> Coding / remote execution capability
```

Runtime may host adapters and installed capability packs, but capability implementations may live in separate Python distributions, Rust/Go/Java processes, MCP servers, local daemons, or remote services.

## Hard rules

1. Agents address provider-neutral capability IDs; they do not import provider libraries for reusable abilities.
2. Agent Runtime owns routing, authorization ceilings, lifecycle, correlation, and durable receipts.
3. Capability implementations own their internal algorithms and storage.
4. A capability may return observations, results, evidence, or receipts; it must not directly mutate another capability's authoritative state.
5. Cross-capability coordination happens through Runtime commands/events, not direct implementation imports.
6. Language and process boundaries are deployment choices. Replacing one provider must not require changes in unrelated agents or capabilities.
7. New reusable subsystems must not be added to Ferro.
8. Existing Ferro-owned reusable subsystems are migration inventory, not precedent.

## Standard capability IDs

Initial provider-neutral IDs:

- `agent.brain`
- `agent.memory`
- `agent.learning`
- `agent.research`
- `agent.browser`
- `channel.*`

The first concrete migration is `agent.memory`.

## Memory migration

Phase M1 introduces a stable language-neutral envelope and a typed Runtime client. Ferro's current MemoryService is temporarily exposed by a legacy adapter.

```text
Ferro
  |
  v
MemoryCapabilityClient
  |
  v
ApplicationRuntime
  |
  v
agent.memory provider
  |
  +--> current: LegacyFerroMemoryCapabilityProvider -> MemoryService
  +--> next: standalone agent-memory package/service
  +--> later: Rust/Go implementation if it wins the same contract tests
```

The public v1 surface is intentionally small:

- `remember`
- `recall`
- `read`
- `changes`
- `health`

### Physical extraction status

The first implementation slice has moved canonical durable persistence into
`agent_runtime.memory.durable`. It owns the existing SQLite schema, append-only
event ledger, revision state and metadata indexes without importing Ferro,
retrieval models or cognition.

Ferro's historical `memory_system.py` is now a compatibility facade. It keeps
the existing database path and the historical default subject ID while old
callers migrate. Legacy retrieval-view invalidation is supplied through the
generic transition hook rather than a Runtime-to-Ferro import.

The deterministic retrieval/index slice now lives in
`agent_runtime.memory.retrieval`. It owns canonical-to-view synchronization,
SQLite FTS, validity windows, graph navigation, change cursors and
reciprocal-rank fusion.

Embedding and ANN are deliberately injected through the dense-search callback.
The current FastEmbed/HNSW implementation remains a Python capability detail;
Agent Runtime does not import either library.

Ferro's MemoryService now delegates search/read/expand/relations/changes and
generic view maintenance to Runtime retrieval. Source ingestion, model-backed
contextualization, relation enrichment and semantic consolidation remain in the
legacy capability and are the next boundaries to classify between Memory,
Research and Learning.

Ferro-specific receipt migration, research enrichment, and model-assisted consolidation remain behind the legacy adapter until they are classified as generic Memory or Learning responsibilities.

## Rewrite rule

Do not perform whole-system language rewrites.

For each module:

1. freeze the provider-neutral contract;
2. put the current implementation behind an adapter;
3. add parity/contract tests;
4. build the replacement independently;
5. run the same tests against both providers;
6. switch the binding;
7. remove the old implementation only after acceptance.

Other modules must remain operational throughout the replacement.
