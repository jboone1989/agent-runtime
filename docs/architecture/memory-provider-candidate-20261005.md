# Memory provider candidate — 2026-10-05

This is a tested implementation batch, not completion of the frozen architecture.
The original decision is now checked in as `long_lived_agent_memory_plan_v1.md`.

## Implemented in this batch

- Official Graphiti 0.30.2 structured adopted-relation projection, stable canonical IDs, scoped graph reads and replay. The previous native Neo4j validation receipt records one successful authority/scope/interruption/rebuild test.
- Provider-neutral ProjectionScope shared across graph, topics, document hierarchy and content indexing. Canonical reads accept agent_id before lookup.
- Independent SQL domain/topic/subtopic navigation, many-topic membership, immutable summary revisions and adoption bindings. Retractions and changes invalidate old memberships and ancestors. Validity transitions schedule refresh even without new adoptions. Summary generation refuses to commit if inputs changed or disclosed budgets were exceeded.
- LlamaIndex 0.14.25 document hierarchy without a model. Stable fragment IDs include parent paths; each fragment is verified against exact original character spans. Parsing artifacts are reused after restart.
- PostgreSQL content adapter with pgvector 0.5.0, psycopg 3.3.6 and explicit Jieba 0.42.1 Chinese search segmentation. Scope and dual-time adoption selection apply before candidate ranking. PostgreSQL FTS is not described as BM25. Start with exact vector search, without an HNSW allocation.
- A separately schedulable incremental index worker. Content derivations and vectors are saved before provider writes; checkpoints advance after successful commits. One provider error does not prevent independent providers from progressing, and failures remain explicit.
- Exact scoped evidence archives are flushed before canonical proposals reference them; adoption remains a separate decision. The content worker indexes the original archive body, not a navigation title.
- Runtime-owned MemoryQueryService and MemoryCapabilityProvider supply original paging, document descent, topic expansion, change cursors and subject binding. Content candidates are checked against the adopted revision, exact saved spans and archive integrity before exposure. Query paths never parse, build indexes or generate summaries. An empty result while the content index is behind is an explicit error. A standalone worker process records failed cycles and resumes saved indexing artifacts.

## Verification actually performed

Forty targeted tests passed on the local source-module snapshot. LlamaIndex and Graphiti SDK objects were real installed packages. Three PostgreSQL integration tests used the real psycopg/pgvector adapters against an isolated PGlite PostgreSQL WASM engine over the PostgreSQL wire protocol; these exercised lexical/vector lookup, identical retry, adoption withdrawal, history, nonoverlapping world-time intervals and scope-before-limit.

The first remote validation receipt records 59 tests: 56 passed, 3 PostgreSQL tests skipped, no failures. This exercised complete package imports and real Neo4j, with the pinned SDKs installed in a separate environment. Subsequent archive/query/worker additions passed 49 local targeted tests, including two additional PostgreSQL wire tests for original reads, service normalization, filters-before-limit and rebuilding without models.

The second remote run reached the native Neo4j test but stalled. Its debug log reports `java.lang.OutOfMemoryError: Metaspace` at 2026-10-05 05:49:04 UTC under the existing 128 MB metaspace cap. The pytest process was terminated (-15); that run did not complete. Its reused XML described the prior run, so the validation runner now removes old XML before starting and records completeness explicitly. The first successful receipt does not establish that the old Neo4j configuration can survive repeated replay.

This is not a native PostgreSQL performance or concurrency benchmark. The remote validation entry point is `scripts/validate_memory_provider_batch.py`. It includes existing retrieval-session/formation tests and reports native endpoints not supplied as NOT_RUN, never passed. `scripts/validate_memory_native_stack.py` is the reviewable rootless next gate: extract Ubuntu PostgreSQL/pgvector packages only under validation, use a private Unix socket with peer authentication and a 384 MB cap, replace only the isolated Neo4j validation unit with a measured 192 MB metaspace / 768 MB cgroup budget, run the provider tests, optionally measure existing transient real embedding/reranking workers alongside Neo4j/browser, then stop the private databases. Model measurement requires 1100 MB free host RAM; otherwise it fails explicitly. It does not deploy production.

## Remaining acceptance gates

1. Native PostgreSQL + pgvector load/rebuild and combined VM09 peak measurement remain pending. Complete package imports and native Neo4j passed for the first provider batch; repeat with the new service additions.
2. Bind the new Runtime query capability and scheduled index process to production. The new content worker supports exact archived sources; legacy archive migration and summary indexing through this adapter still need integration. Graph extraction proposals also need to flow through canonical adoption before projection.
3. Verify existing scoped records, raw source reads, topic classification and real task evidence recall on a production snapshot.
4. Make a real learning outcome change a later action, then complete autonomous trigger through POSTED receipt, external result observation and later adoption. The repeated-context draft/session failures remain separate unresolved production work.
5. Merge to main, deploy and delete old graph/tree/HNSW paths only after those gates pass. No production provider was switched and no old data or implementation was deleted in this batch.

## Current command gate

The Remote connector returned `approval_required` for new diagnostic/test commands. Its documented rule is "Human approval for new commands". No exact permission was self-approved or bypassed. The reviewable next command prepares only the separate memory validation environment and runs the candidate tests; it does not deploy the production service.
