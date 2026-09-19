# Evolution Engine v1

Status: **Phase 0 contract frozen for implementation**
Owner: Agent Runtime
Primary adopter: Ferro / Content Agent
Last updated: 2026-09-19

## Purpose

Evolution Engine is the single, ordered self-improvement protocol for Runtime-hosted agents.
It turns real experience into reversible, evidence-bound capability change without granting
new authority. Domain agents may contribute observations, outcomes, and mutation executors,
but they must not reimplement the core lifecycle.

The engine answers a different question from the Goal system:

- Goal system: **what should I do now?**
- Evolution Engine: **what should I change about myself so future goal execution improves?**

Evolution must remain subordinate to identity, governance, authorization, and resource ceilings.

## Evolution constitution

The following invariants are architectural constraints, not tunable heuristics:

1. Evidence before mutation.
2. Observation is not causation.
3. One failure does not create permanent learning.
4. One success does not create permanent learning.
5. Every mutation has durable provenance.
6. Every mutation is reversible or explicitly declared irreversible before trial.
7. Existing successful behavior is part of the regression constraint set.
8. `UNKNOWN` and `INSUFFICIENT_EVIDENCE` are valid outcomes.
9. Rejected and negative experiments remain queryable evidence.
10. Online execution and durable evolution are separate loops.
11. Prefer the smallest adequate mutation layer.
12. Model-parameter mutation is the last resort.
13. Evolution cannot raise authorization, reveal protected secrets, or weaken governance.
14. A model-generated proposal is advisory evidence, never authority.
15. Promotion requires prospective evidence against a frozen baseline.
16. Capability activation must be versioned, observable, and rollback-capable.
17. Stale, contradicted, or superseded knowledge must be deactivated rather than accumulated forever.
18. Meta-evolution may change learning policy only through the same candidate/trial/promotion protocol.

## Ordered lifecycle

```text
WORLD / SELF
    |
    v
1. OBSERVATION
    |
    v
2. EXPERIENCE LEDGER
    |
    v
3. OUTCOME / EVIDENCE
    |
    v
4. DIAGNOSIS
    |
    v
5. NEED / GOAL FORMATION
    |
    v
6. EVOLUTION ROUTER + CANDIDATE
    |
    v
7. REPLAY / SHADOW / PROSPECTIVE TRIAL
    |
    v
8. PROMOTE / REJECT / CONTINUE
    |
    v
9. CAPABILITY REGISTRY
    |
    v
10. SLEEP / CONSOLIDATION
    |
    v
11. META-EVOLUTION
```

No later phase may bypass an earlier phase by inventing its own evidence or activation path.

## Canonical records

Evolution Engine v1 converges on the following durable records.

### EvolutionExperience

A replayable account of what the agent believed, intended, did, and spent at decision time.

Required concepts:

- identity / agent id
- observed_at / effective_at
- world-state references
- self-state references
- goal references
- active belief / prediction references
- action references
- capability versions
- model / prompt / tool versions
- resource cost
- outcome availability boundary
- provenance

Existing `ExperienceTrace` is the starting substrate. Do not create a second unrelated trace system.

### EvolutionEvidence

Evidence attached after the world can settle the action.

Evidence dimensions:

- outcome: did the external objective happen?
- process: did execution respect the intended policy and procedure?
- quality: how good was the result?
- cost: tokens, money, compute, latency, scarce-resource consumption
- side effects: regressions or external harms
- provenance / confidence / observability class

Evidence classes must distinguish at least:

- `WORLD_OBSERVED`
- `SYSTEM_MEASURED`
- `USER_PROVIDED`
- `MODEL_INFERRED`
- `UNKNOWN`

Existing `OutcomeRecord` and `VerifiedLearningEvidence` are inputs to this layer.

### EvolutionDiagnosis

A diagnosis must preserve competing explanations instead of collapsing correlation into cause.

Minimum fields:

- diagnosis id
- experience / evidence refs
- observed pattern
- candidate causes
- supporting evidence
- counterevidence
- unknowns
- causal confidence
- proposed discriminating experiment
- applicability scope

Diagnosis is allowed to conclude `INSUFFICIENT_EVIDENCE`.

### EvolutionNeed

A durable statement that changing the agent may be worthwhile.

Canonical origins:

- `EXTERNAL_MISSION`
- `FAILURE`
- `RESOURCE_PRESSURE`
- `CAPABILITY_GAP`
- `REPEATED_INEFFICIENCY`
- `OPPORTUNITY`
- `CURIOSITY`
- `VALUE_CONFLICT`
- `META_LEARNING_SIGNAL`

Existing EvolutionFact -> EvolutionPattern -> EvolutionNeed remains the seed implementation.

### EvolutionCandidate

A concrete, versioned change proposal.

Every candidate must name exactly one primary mutation layer:

1. knowledge
2. retrieval / indexing
3. skill
4. prompt
5. workflow
6. tool / harness
7. program
8. architecture
9. model parameters

Candidates also carry:

- source need / diagnosis
- hypothesis
- expected fitness
- protected invariants
- rollback plan
- baseline reference
- evidence refs
- risk / cost budget

Generated Repair is a **program-mutation executor**, not the Evolution Engine itself.

### EvolutionExperiment

A frozen comparison between baseline and candidate.

Stages:

```text
PROPOSED
  -> REPLAY_VALIDATED
  -> SHADOW
  -> PROSPECTIVE_TRIAL
  -> PROMOTED
```

Alternative terminal or holding states:

- `REJECTED`
- `INCONCLUSIVE`
- `CONTINUE_OBSERVING`
- `DORMANT`
- `ROLLED_BACK`
- `SUPERSEDED`

The baseline, candidate version, window, metrics, known-at boundaries, and stop conditions
must be fixed before the prospective window is observed.

### CapabilityVersion

The durable result of successful evolution.

It must preserve:

- capability id and version
- parent / predecessor
- born_at
- source candidate and experiment
- supporting evidence
- counterevidence
- applicability context
- confidence
- usage / outcome summary
- last verified time
- state: candidate / shadow / active / dormant / deprecated / rejected
- superseded_by
- rollback target

## Runtime ownership boundary

Agent Runtime owns:

- canonical evolution record schemas
- lifecycle state machine
- evidence integrity
- diagnosis protocol
- candidate mutation-layer routing contract
- experiment / baseline / prospective-window semantics
- promotion / rejection semantics
- capability registry
- consolidation / pruning policy
- meta-evolution governance

Domain agents such as Ferro own:

- domain observations and world outcome collection
- domain-specific metrics
- domain-specific mutation executors
- domain replay adapters
- domain capability implementations

VirtualBrain may propose priorities, beliefs, predictions, or structural signals. It does not
gain authority to bypass Runtime evidence, experiment, or activation gates.

Agent Network may transport signed evidence and capabilities. It does not decide local activation.

## Online loop vs evolution loop

Online execution:

```text
observe -> decide -> act -> record immutable trace -> continue serving
```

Evolution loop:

```text
aggregate -> evaluate -> diagnose -> propose -> experiment -> select -> consolidate
```

The online loop may submit facts or candidate material but must not silently mutate durable
behavior because of a single live event.

## Sleep / consolidation

Sleep is a lifecycle activity, not process shutdown.

Consolidation responsibilities:

- cluster repeated experiences
- compare success vs failure cohorts
- detect contradictory knowledge
- identify recurring capability gaps
- merge duplicates
- age stale evidence
- propose deprecations
- preserve negative experiments
- generate bounded candidates
- schedule validation

Sleep must not directly promote candidates.

## Meta-evolution

Meta-evolution changes how evolution itself operates, for example:

- experience selection
- evidence weighting
- diagnosis search
- candidate generation
- experiment duration
- promotion thresholds
- mutation-layer routing

A meta-change is still an EvolutionCandidate and must pass replay, shadow, prospective evaluation,
and rollback requirements. The engine is never allowed to exempt itself from its own protocol.

## Existing implementation mapping

The current codebase already contains useful pieces. They are to be absorbed, not duplicated.

| Existing component | v1 role |
| --- | --- |
| `ExperienceTrace` | Phase 1 experience substrate |
| `OutcomeRecord` | Phase 2 world settlement substrate |
| `VerifiedLearningEvidence` | Phase 2 evidence-integrity gate |
| `LearningController` | early strategy/capability learning gate |
| `EvolutionFact / Pattern / Need` | Phase 5 need formation |
| `EvolutionHypothesis` | early hypothesis record |
| `EvolutionTrial` | early experiment result; must become prospective-aware |
| `AgentVersion / EvolutionProposal / Evaluation / Activation / Rollback` | candidate/version lifecycle building blocks |
| Content Agent Generated Repair | Phase 6/7 program mutation executor |
| Content Agent capability replacement | capability challenger discovery input |

## Implementation phases

### Phase 0 — Constitution and frozen contract

Deliverables:

- this architecture
- explicit ownership boundary
- canonical lifecycle and state machine
- compatibility mapping for existing Runtime learning/evolution modules
- tests that later implementations cannot bypass activation / evidence ceilings

Exit condition: future evolution work can be assigned to one phase and one canonical record family.

### Phase 1 — Experience Ledger

Unify existing trace material into a replayable EvolutionExperience projection.

Must prove:

- decision-time context is retained
- capability/model/tool versions are retained
- costs are retained
- outcome-known-at boundaries are representable
- records survive restart
- records are immutable after sealing

### Phase 2 — Outcome and Evidence Engine

Unify outcome/process/quality/cost/side-effect evidence.

Must prove:

- model inference cannot impersonate world observation
- delayed outcomes bind to the correct experience
- unverifiable evidence cannot promote a candidate
- insufficient evidence stays explicit

### Phase 3 — Diagnosis Engine

Add competing-cause diagnosis and discriminating-experiment generation.

Must prove:

- correlation is not automatically promoted to causal belief
- counterevidence is retained
- ambiguous cases can stop at insufficient evidence
- diagnosis provenance is replayable

### Phase 4 — Need / Goal Formation

Route external missions, failures, resource pressure, capability gaps, inefficiency,
opportunities, curiosity, value conflicts, and meta-learning signals into canonical needs.

Must prove:

- duplicate needs consolidate
- pressure is evidence-bound
- need creation does not grant mutation authority

### Phase 5 — Evolution Router and Candidate Registry

Choose the smallest adequate mutation layer and create immutable candidate versions.

Must prove:

- program mutation is not default
- protected governance paths cannot be targeted
- each candidate has baseline, rollback, risk, and provenance

### Phase 6 — Experiment Engine

Implement frozen baseline, replay validation, shadow mode, prospective trials, and stop conditions.

Must prove:

- no data leakage across the prospective boundary
- candidate and baseline can be compared on the same metric contract
- regression constraints include previously successful behavior

### Phase 7 — Capability Registry and Activation

Promote only evidence-supported candidates into versioned capability state.

Must prove:

- activation is atomic and observable
- rollback restores the prior version
- rejected candidates remain queryable
- superseded capabilities do not disappear from history

### Phase 8 — Sleep / Consolidation

Implement periodic clustering, generalization, conflict handling, pruning, and deprecation proposals.

Must prove:

- one event cannot become permanent learning through sleep
- stale evidence is aged without deleting provenance
- conflicts remain visible until resolved

### Phase 9 — Meta-Evolution

Permit the engine to propose bounded changes to its own policies.

Must prove:

- meta-changes use the same candidate/experiment/activation protocol
- governance and authorization ceilings remain immutable from inside evolution
- rollback works for meta-policy changes

## End-to-end definition of done

Evolution Engine v1 is not complete because classes exist. It is complete only after one real,
production-adjacent chain has been evidenced end to end:

```text
real experience
  -> immutable ledger
  -> delayed world outcome
  -> recurring pattern
  -> diagnosis
  -> evolution need
  -> hypothesis
  -> mutation-layer route
  -> candidate
  -> replay
  -> shadow
  -> prospective baseline comparison
  -> promotion
  -> later similar situation uses the promoted capability
  -> continued outcome observation
  -> rollback remains possible
```

The first Ferro acceptance case should use a bounded, non-authority-raising capability such as
content/social strategy selection. Self-repair may validate a later program-mutation case after
the general lifecycle is proven.

## Anti-drift rule

Every future Ferro self-improvement task must identify:

1. the Evolution Engine phase it belongs to;
2. the canonical record it consumes and produces;
3. the evidence that settles success;
4. the rollback path.

If a task cannot answer these four questions, it is not allowed to introduce a new evolution path.
