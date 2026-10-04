# Long-lived Agent Memory — Stage 0 Audit

Date: 2026-10-04  
Status: Stage 0 implementation audit; no production cutover in this change.  
Normative design: `long_lived_agent_memory_plan_v1.md` (frozen architecture decision; currently stored outside this repository).

## 1. Executive conclusion

The new Memory architecture does **not** start from zero.

A substantial first extraction has already happened on the development main lines:

- canonical durable storage, evidence sources, retrieval, contextualization, relation enrichment,
  consolidation, formation, episodic memory, semantic/relational memory and local semantic
  retrieval have been moved or are being moved from Ferro into Agent Runtime;
- Ferro already contains a provider-neutral `agent.memory` compatibility boundary;
- learning receipt projection is already separated from generic storage/retrieval logic.

However, production is currently a **hybrid deployment assembled from dirty release worktrees**,
not a clean Git revision. Before Graphiti/Neo4j or PostgreSQL are added, the existing extraction
must be reconciled into one reproducible development/deployment baseline.

The current SQLite canonical store is valuable migration data and should be preserved. It is not,
by itself, the final canonical contract required by the frozen design because revision/adoption
history and `valid_at` versus `known_at` are not yet first-class canonical concepts.

## 2. Actual code/deployment baseline

### Agent Runtime

Development checkout at `/home/ubuntu/src/agent-runtime` is detached at `a0ef266` and is not
the repository's current main line.

The usable clean main worktree is:

`/home/ubuntu/worktrees/runtime-posting-auto-recovery-20261003`

At the start of the audit it was `main`, one commit ahead of `origin/main`.
During the audit another concurrent browser-fix commit (`41125c7`) landed on this same main
worktree, making it two commits ahead. The Memory audit itself did not create that commit.

Relevant main-line extraction history includes:

- `b22ca18` provider-neutral capability protocol and memory port;
- `3795266` durable memory store;
- `c3ea595` durable retrieval;
- `2d92e73` evidence source memory;
- `79b8d2a` optional local semantic retrieval provider;
- `bf3f934` evidence contextualization;
- `0510857` relation enrichment;
- `eef8c0a` semantic consolidation;
- `fe54579` formation pipeline;
- `23f8887` episodic memory;
- `e185520` semantic/relational memory.

Production Python import precedence is forced through `.pth` files to:

`/home/ubuntu/agent-runtime/current/src`

which currently resolves to release:

`/home/ubuntu/agent-runtime/releases/b63049b-decoupled-wakes`

Its declared commit is `b63049b33d6d94f249e2f99cee13398cee8f2bfd`, but the release worktree is
dirty. Six later Memory modules are present as untracked files. Their Git object hashes exactly
match the later `origin/main` versions:

- `memory/durable.py`
- `memory/retrieval.py`
- `memory/consolidation.py`
- `memory/contextualization.py`
- `memory/relations.py`
- `memory/sources.py`

Therefore the current production Runtime cannot be reproduced from its release commit alone.

### Ferro

Development main is at `98b1f6b`; `origin/main` is at `729efe2` at audit time.

Production runs from:

`/home/ubuntu/content-agent/releases/c74ab22-worker-decoupling`

with declared commit `c74ab22037a88c0193734611be107ac13c8af7e1`, but this release worktree is
also dirty.

Several production Memory files have been manually advanced to byte-identical copies of the
current development versions, while other Stage-1 extraction commits exist only on the later
development history. Production is therefore neither clean `c74ab22` nor clean current main.

**Stage-0 rule:** do not start the new provider migration from either dirty release worktree.
Reconcile and test from a clean main-line source tree first.

## 3. Current data/resource baseline

VM09 baseline observed during the audit:

- 2 CPUs;
- about 3.84 GB physical RAM;
- about 1.18 GB host memory available at the sampled point;
- `content-agent.service` active;
- content-agent cgroup about 688 MB resident at the sampled point;
- content-agent cgroup peak already reached 1.0 GB;
- about 115 MB swap currently in use by that cgroup at the sampled point;
- cgroup `MemoryMax=1G`, `MemorySwapMax=256M`.

Current Memory state root:

`/home/ubuntu/content-agent-state/application/memory_system`

Observed data:

- `memory.db`: 114,180,096 bytes;
- `memory_events.jsonl`: 1,001,028 bytes;
- `formation_audit.jsonl`: 388,995 bytes;
- evidence archive contains more than 200 files;
- current HNSW generation: about 4.62 MB.

No PostgreSQL or Neo4j process was running on VM09 during the audit.

Current local retrieval model measurements from the persisted inference metrics:

- embedding/query workers commonly peak around 674 MB RSS;
- reranking 32 candidates commonly peaks around 787–826 MB RSS;
- 32-candidate reranking takes roughly 14–16 seconds in the sampled records;
- small embedding/query batches usually take roughly 2–4 seconds.

This is already a material fraction of the host. The full target combination must not be assumed
safe on VM09 until PostgreSQL + Neo4j + Graphiti + model-service peak usage is measured together.

## 4. Canonical contract audit

Current Runtime `CanonicalMemoryStore` already provides useful durable primitives:

- stable `memory_id`;
- type/kind/subject/scope;
- provenance references and evidence references;
- `created_at`, `observed_at`, `valid_from`, `valid_until`;
- ACTIVE/SUPERSEDED/DISPUTED/RETRACTED/ARCHIVED status;
- topic/entity indexes;
- content hashes;
- append-only `memory_events` protected by SQLite triggers;
- exact-ID collision rejection when content differs.

Keep the data and semantics.

Gaps against the frozen long-lived-agent contract:

1. `Memory revision` is not a first-class immutable canonical record type.
2. `Adoption decision` is not a first-class immutable canonical record type.
3. State transitions currently mutate the current `memories` row and append an event; this is
   insufficient as the final historical adoption model.
4. There is no explicit canonical `recorded_at` / adoption timeline separate from world validity.
5. No complete provider-independent query contract has yet been proven for both `valid_at` and
   `known_at`.
6. Current SQLite is a working migration substrate, not the target PostgreSQL deployment choice.

**Decision:** preserve and migrate current canonical data; extend the canonical contract before
making Graphiti or any other derived provider authoritative for reads.

## 5. Existing retrieval architecture audit

The present system already implements the right high-level idea: bounded task-local retrieval
instead of placing all history in one prompt.

Existing useful behavior to preserve:

- durable per-task retrieval checkpoint;
- short local references rather than long canonical IDs in model turns;
- search/read/evidence/summary/relation/change tools;
- task-scoped external research;
- prompt-size checks before dispatch;
- resumable continuation rather than treating a scheduler slice as terminal;
- derived indexes can be rebuilt from canonical records and evidence;
- exact evidence is kept separate from summaries.

The current implementations are temporary providers:

- SQLite FTS/custom retrieval tables;
- vectors embedded in `memory_nodes`;
- local HNSW binary index;
- custom extracted relation graph;
- custom semantic-summary hierarchy.

These should not all be rewritten at once. Preserve the retrieval/session behavior while replacing
one provider boundary at a time.

## 6. Module disposition

| Current responsibility | Current location | Disposition |
| --- | --- | --- |
| Canonical store + event ledger | Runtime `memory/durable.py` | **KEEP + EXTEND** canonical contract; migrate storage to PostgreSQL only after parity tests |
| Evidence archive/source pointers | Runtime `memory/sources.py` + evidence files | **KEEP** semantics and content hashes; storage remains canonical archive |
| Formation pipeline | Runtime `memory/formation.py` | **KEEP**; change output to explicit revision/adoption records |
| Episodic service | Runtime `memory/episodic.py` | **KEEP** generic service |
| Semantic/relational claim formation | Runtime `memory/semantic.py` | **KEEP claim semantics**, but relation projection moves to Graphiti |
| Ferro `memory_system.py` | compatibility facade | **DELETE AFTER CUTOVER** |
| Ferro `memory_formation.py`, `memory_episode.py`, `memory_semantic.py` | compatibility imports | **DELETE AFTER direct Runtime imports** |
| Ferro `memory_service.py` | composite legacy facade | **SHRINK then DELETE**; provider orchestration belongs in Runtime Memory capability |
| Ferro `runtime_memory_adapter.py` | `agent.memory` compatibility boundary | **KEEP DURING MIGRATION**; becomes normal Runtime provider binding |
| Retrieval/session controller | Ferro `memory_session.py` | **MOVE TO RUNTIME** as resumable iterative retrieval state machine |
| Existing Runtime custom retrieval | Runtime `memory/retrieval.py` | **KEEP AS BASELINE/adapter**, then split into hybrid provider interfaces |
| HNSW + local ANN | Runtime `memory/local_retrieval.py`, Ferro wrappers | **REPLACE** index with pgvector; embedding provider may remain separately |
| SQLite FTS path | current retrieval DB | **REPLACE** with PostgreSQL FTS at storage cutover |
| Relation enrichment/custom relation view | Runtime `memory/relations.py` | **REPLACE** official relation projection with Graphiti + Neo4j |
| Contextualization | Runtime `memory/contextualization.py` | **KEEP concept**, adapt long-document path to LlamaIndex components |
| Semantic summary hierarchy | current consolidation/retrieval tables | **REPLACE** with explicit SQL topic hierarchy + versioned summaries |
| Procedure memory/statistics | Ferro `memory_procedural.py` | **SPLIT** generic procedure revisions/statistics to Runtime; Ferro/AWE policy logic stays outside Memory core |
| Learning receipt projection | Ferro `learning_memory_bridge.py` | **KEEP IN LEARNING/FERRO** as domain projection into generic canonical APIs |
| Self/prospective adapter | Ferro `memory_self.py` | **KEEP adapter boundary**; live Runtime/VirtualBrain state remains authoritative |
| Continuity/backup/rebuild | Ferro `memory_continuity.py` | **MOVE generic mechanics TO RUNTIME**; derived indexes remain rebuildable |
| Recall observability | Ferro `memory_observation.py` | **MOVE generic telemetry TO RUNTIME**, keep domain interpretation outside Memory |
| Memory evolution/benchmarks | Ferro `memory_evolution*.py` | **MOVE evaluation mechanics out of core Memory**; Learning/Runtime Evolution owns promotion |
| Content-domain priors | Ferro `memory_prior*.py` | **KEEP OUTSIDE Memory core**; these are learned decision policy consumers |
| Salience policy | Ferro `memory_salience.py` | **KEEP OUTSIDE Memory core**; cognition/attention concern |
| Ferro Memory life/runtime orchestrators | Ferro `memory_life.py`, `memory_runtime.py` | **DECOMPOSE** into Runtime capability + Ferro-specific lifecycle adapters |

## 7. What must not be carried forward

After the target providers pass acceptance, remove rather than maintain:

- the custom official relationship graph;
- HNSW as the official raw-content vector index;
- the custom official semantic-summary tree;
- Ferro-owned duplicate storage/retrieval implementations;
- dirty-release copying as a deployment method;
- any full-history prompt assembly path;
- any silent fallback that reports retrieval/provider failure as "no memory";
- permanent dual writers or dual fact adjudicators.

Old data is not deleted merely because old code is removed.

## 8. Stage-0 blockers and exit criteria

Stage 0 is **not yet complete for production cutover**. The following must be closed first:

1. Reconcile Agent Runtime current main, Ferro current main, and the two dirty production release
   worktrees into reproducible Git revisions.
2. Record one deployable pair of Runtime/Ferro commits and prove production imports exactly those
   revisions.
3. Freeze the canonical v2 schema/API delta: revision, adoption, dual-time queries, scope,
   provenance and checkpoints.
4. Preserve a byte/hash/count baseline of current canonical records and evidence before migration.
5. Freeze resource budgets for PostgreSQL, Neo4j, Graphiti worker and model service using VM09's
   real browser/content-agent headroom.
6. Only then begin Graphiti/Neo4j provider integration.

## 9. Immediate implementation order

The next code change should **not** be Graphiti installation.

First:

1. make the Runtime/Ferro source baseline reproducible;
2. implement the provider-independent canonical-v2 contract in Agent Runtime while retaining the
   current SQLite adapter and data;
3. move resumable retrieval state/control into Runtime behind the existing `agent.memory` port;
4. add canonical migration/parity tests;
5. then integrate Graphiti/Neo4j as the first new derived provider.

This keeps the live system usable while the authority boundary is corrected first.
