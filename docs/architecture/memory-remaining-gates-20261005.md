# Frozen Agent Memory plan: remaining gates, 2026-10-05

## Completed implementation batches, not completion of the frozen plan
Canonical revision/adoption v2 and the reusable Runtime retrieval controller are deployed at Runtime de62b71 / Ferro 90dadac. The 149 selected tests and three observed production decisions establish those boundaries. They do not establish the complete architecture or all original numbered stages.

## Remaining work against the original frozen stages
- Stage 0: resource baseline is now captured; complete combined peak and fixed provider-comparison budgets.
- Stage 1: complete provider-neutral scope, canonical public record interfaces, independent index worker and removal of remaining Ferro-owned indexing/model implementation.
- Stage 2: Graphiti 0.30.2 / Neo4j adoption mapping, stable canonical identities, scope before search, valid_at and known_at semantics, real-data replay, interruption and deterministic reconstruction.
- Stage 3: independent theme memberships and summary revisions, LlamaIndex local document hierarchy, pgvector and Chinese lexical pipeline.
- Stage 4: integrate all independent views, real original-evidence reads, multi-round recovery and explicit failures.
- Stage 5: show an adopted outcome/procedure revision changing a later action with unique-attempt statistics.
- Stage 6: autonomous trigger through POSTED receipt, external observation and later behavior; recovery/rebuild; retire replaced code only after verification.

## Measured deployment baseline
A passive twelve-second sample found at least 1,268,330,496 bytes of available host RAM. Existing browser process RSS reached 1,094,086,656 bytes; existing Ferro-related process RSS reached 782,991,360 bytes. RSS includes shared pages and must not be equated with unique allocation. Host swap used reached 1,387,851,776 bytes. The service lifetime cgroup peak reached its 1 GiB cap, swap reached 256 MiB, with no recorded OOM kill. This is not a Graphiti or Neo4j load benchmark and does not prove they fit.

Graphiti, Neo4j Python driver, LlamaIndex, pgvector and psycopg are absent from the production environment. Docker executable exists. Graphiti 0.30.2 was verified using the official release list and a read-only pip index query; production dependencies have not been changed.

## Actual prerequisite block
The remote connector requires explicit host approval for the read-only command:
`docker image ls --format {{.Repository}}:{{.Tag}}`
Request: exec_req_aff13ec9acec4788044b.
The command did not run. No container was installed or launched, no graph provider switch occurred, and no fallback architecture was selected. Existing production service remains active.

Sources reviewed:
- Frozen long_lived_agent_memory_plan_v1.md, sections 6, 12–16.
- https://github.com/getzep/graphiti/releases/tag/v0.30.2
- https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/
