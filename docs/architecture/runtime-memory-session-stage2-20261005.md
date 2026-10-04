# Runtime resumable memory retrieval — stage 2

## Ownership and behavior
Runtime owns `agent_runtime.memory.session`: task checkpoints, M1-style references, pending read intent, tool observations, revision checks and explicit budgets. Ferro's `memory_session.py` is deleted. Its existing Runtime adapter supplies structured decision formatting and the domain decision contract. Learning retains its research and application policy, queries, web acquisition and evidence sufficiency classification. Runtime does not import Ferro or choose learned propositions.

The default budget is four accepted decision rounds per durable retrieval session (focus, research step or application), with a 6,000-token memory context. Ferro conservatively counts its entire JSON payload, including domain memory, quotes and the working packet, against that limit. The selection limit is 9,000 input-plus-reserved-output tokens / 24,000 characters; the actual dispatched serialized request, schema and repair are checked against 12,000 tokens / 36,000 characters. Default reserved output is 2,400 tokens. Maintenance cognition and structured-format repair are separate provider calls; this is not a promise of four total network calls or four rounds for an entire content cycle.

Selection windows (8, 4, 2, 1) are disclosed by supplied/available counts. Raw archives remain external. If even the smallest selection exceeds the budget, the controller ends explicitly rather than silently clipping the canonical archive. A scheduler slice may end early with NEEDS_CONTINUATION; persisted total rounds cannot be reset on resume. BUDGET_EXHAUSTED becomes a terminal content result, not a fresh retry allowance.

Before executing a tool, the accepted decision round and stable task-local action identity are saved atomically. A resumed task first finishes pending tool/context work. Research reuses a saved external search receipt and reconstructs its source registry; a crash after receipt acquisition does not trigger the same query again. This provides receipt reuse, not atomic exactly-once guarantees for an upstream search interrupted before its receipt was saved.

Only explicit input errors become repairable tool observations. Provider/contract failures are recorded as PROVIDER_ERROR and the original exception propagates. Revised or invalid originals invalidate cached decisions; old read excerpts are withheld from the next packet when their saved revision no longer matches.

## Legacy checkpoints
ferro-memory-session.v1 is migrated lazily to agent-memory-session.v2. References, working summary, tool observations, external search receipts and all domain binding fields remain in the checkpoint. Old checkpoints did not record decision-round consumption: the migration explicitly records LEGACY_CONTINUATION and legacy_tool_turns and grants a new four-round continuation allowance. It does not invent historical budget accounting. An old read without a saved revision must be reread before it is exposed as current evidence.

## Verification
- Runtime: 13 controller recovery/failure tests and 44 canonical/retrieval/consolidation regressions passed.
- Ferro: 92 selected service, research, application, capability boundary, adapter, content-loop and terminal-budget tests passed.
- External acquisition interruption test: a failure after receipt acquisition resumes with the same action identity; one search call, preserved references, recovered exact evidence and two consumed decision rounds.
- Twelve recent production checkpoints were migrated on an isolated SQLite/evidence snapshot. References, observations and domain bindings were unchanged; production state was not written.
- The expired-source fixture now supplies a valid historical start/end interval, consistent with canonical v2. Archive-growth verification retains tenfold archive records and tenfold original length, with 6 vs 60 history records to fit the host's bounded execution window.
- A synthetic evidence-reading probe through the real shared ChatGPT route exceeded the connector's 30-second foreground window before producing a completed receipt. It is not counted as a passed real-model acceptance test. No alternative browser or model was launched.

## Remaining scope
Graphiti/Neo4j official graph integration, the separate hierarchy provider, pgvector/FTS provider budgets and the remaining frozen-plan acceptance matrix are still subsequent work. Current host swap pressure is a material deployment constraint for adding graph services.

## Production deployment and observed task
Both feature commits were merged and pushed to main before deployment: Runtime de62b71cc612a7950dc53eabe07eb6ae3056e167 and Ferro 90dadac3287c4ad4c8df0da71322d544b4d9ccbf. The prepared releases contain those commits without code overlays. Service state and 485 memory checkpoint files were backed up while paused; activation occurred at 2026-10-04 18:02 UTC. Six ordinary workers plus the existing neural worker were observed using the new Ferro release.

The first observed production cycle, v2-owner-d6af18e42b74-1791136945, completed focus, research and application sessions with one accepted decision round each. Their memory contexts were 5,022 / 4,778 / 2,573 tokens. Research and application ended EVIDENCE_INCOMPLETE; the research classified its evidence as REPEATED, and no new learning is claimed. The cycle reached X_EXPRESSION_READY, which does not prove publication.

The actual serialized requests observed in that cycle stayed at or below 21,360 characters and 8,809 estimated input-plus-reserved-output tokens. This production chain establishes real model and provider integration, while the earlier synthetic READ_EVIDENCE probe remains incomplete. Offline tests establish the individual read and interruption behavior; no completed live READ_EVIDENCE round is claimed.

See runtime-memory-session-production-20261005.json for exact release paths, imports, worker identities, persisted budgets and read-only production observations. The documentation receipt commit after deployment does not change executable production code.
