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

## Verification actually performed

Forty targeted tests passed on the local source-module snapshot. LlamaIndex and Graphiti SDK objects were real installed packages. Three PostgreSQL integration tests used the real psycopg/pgvector adapters against an isolated PGlite PostgreSQL WASM engine over the PostgreSQL wire protocol; these exercised lexical/vector lookup, identical retry, adoption withdrawal, history, nonoverlapping world-time intervals and scope-before-limit.

This is not a native PostgreSQL performance or concurrency benchmark, and the local source snapshot did not exercise the full package export/import graph. The remote validation entry point is `scripts/validate_memory_provider_batch.py`. It includes existing retrieval-session/formation tests and reports native endpoints not supplied as NOT_RUN, never passed.

## Remaining acceptance gates

1. Run the candidate in the dedicated remote validation environment with the complete Runtime package imports. Native PostgreSQL + pgvector load/rebuild and combined VM09 peak measurement remain pending.
2. Bind the new adapters to the production Memory query capability and scheduled index process. The new content worker currently indexes canonical revision text; raw-evidence archive ingestion and summary indexing through this adapter still need production integration. Graph extraction proposals also need to flow through canonical adoption before projection.
3. Verify existing scoped records, raw source reads, topic classification and real task evidence recall on a production snapshot.
4. Make a real learning outcome change a later action, then complete autonomous trigger through POSTED receipt, external result observation and later adoption. The repeated-context draft/session failures remain separate unresolved production work.
5. Merge to main, deploy and delete old graph/tree/HNSW paths only after those gates pass. No production provider was switched and no old data or implementation was deleted in this batch.

## Current command gate

The Remote connector returned `approval_required` for new diagnostic/test commands. Its documented rule is "Human approval for new commands". No exact permission was self-approved or bypassed. The reviewable next command prepares only the separate memory validation environment and runs the candidate tests; it does not deploy the production service.
