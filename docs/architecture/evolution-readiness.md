# Runtime v0.4 Evolution readiness

Status: **frozen**. Runtime v0.3 now has a verified-learning-evidence gate, but the
strict cross-project order still requires VirtualBrain Phase 4A/4B/4C before Runtime
v0.4 and Network evolution evidence.

## What Runtime v0.3 now proves

A `VerifiedLearningEvidence` record is created only when all of these bindings hold:

- the trace belongs to the Runtime identity;
- exactly one trace names the action;
- the same trace explicitly names the outcome;
- the outcome is not earlier than the trace's effective time;
- trace confidence is finite and at least `0.5`;
- the evidence ID binds agent, trace, action, and outcome;
- the record survives snapshot persistence and restore.

Strategy versions may consume only ledger evidence. Claimed success/failure counts are
recomputed and checked, an evidence item cannot be consumed twice, and an existing
strategy version cannot be silently replaced.

This proves evidence integrity for the current Runtime learning path. It does **not**
prove safe self-evolution.

## v0.4 unfreeze gates

All gates below must be evidenced before Evolution implementation starts:

1. Runtime v0.3 full regression, persistence, and packaged-runtime proofs pass.
2. VirtualBrain Phase 4A/4B/4C is complete and supplies its agreed evidence without a
   Runtime import of VirtualBrain internals.
3. Agent Network defines evolution evidence and verifier semantics without modifying
   the frozen Trust Foundation v0.2 contract.
4. Any shared protocol change has an ADR/RFC and frozen schema before implementation
   in any repository.
5. Evolution activation has an explicit version, pre-activation checkpoint, bounded
   evaluation window, rollback path, and authorization ceiling.
6. Longitudinal evidence thresholds and contradiction handling are specified; three
   local successes alone are not sufficient evidence for evolution.

Until every gate is satisfied, the only allowed work is evidence collection, contract
design, test fixtures, and documentation. Runtime must not add mutation, activation,
inheritance, or reproduction behavior under the v0.4 name.
