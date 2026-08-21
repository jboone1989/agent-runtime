# Post–Life Loop version roadmap

Status: documentation alignment (no business code)  
Date: 2026-08-22  
Anchors: Runtime `f7a13bc`, VirtualBrain `928786e`, Agent Network `21948d5`

This is the cross-repo version table. It does not authorize implementing later stages in the current Life Loop slice.

## Agent Runtime

| Stage | Content | Relative to the old route |
|---|---|---|
| v0.1 | Continuity of Self | Done |
| v0.2 | Sensory / Regulatory / Survival | Done |
| v0.2.1 | Environment / Embodiment | Frozen |
| v0.2.2 | Capability Inventory / Need / Gap / Policy foundation | Done |
| Life Loop Integration | Runtime × VirtualBrain long-lived session, first closed loops | Current slice. **No Capability version number.** |
| Life Loop Hardening | `CheckpointCoordinator`, extension governance, lifecycle errors | New, after Life Loop freeze |
| **v0.2.3** | **Executive Foundation** (Attention, WorkingContext, Plan/PlanStep, interrupt/wait/resume/replan, ExperienceTrace) | Newly inserted **before** Resolver |
| **v0.2.4** | Capability Resolver / Reuse Before Create | Was v0.2.3 |
| **v0.2.5** | Acquisition / Sandbox / Verification | Was v0.2.4 |
| **v0.2.6** | Network Capability / Delegation Autonomy | Was v0.2.5 |
| **v0.2.7** | Social Coordination Foundation (commitment, consent/revocation, joint goal, dialogue act; no social psychology model) | New |
| v0.3 | Learning + Memory Governance + habit + social learning | Still no fine-tune start |
| v0.4 | Evolution | Unchanged, consumes v0.3 verified experience |
| v0.5 | Reproduction / Inheritance | Unchanged, needs Network Lineage Evidence |

Resolver input is `PlanStep.required_capabilities` plus Executive context, not a guess from `ActionProposal.kind`. That is why v0.2.3 must land first.

## VirtualBrain

| Stage | Content |
|---|---|
| Phase 1–3 | Affect, Needs, Memory, Interoception, SelfModel, Motivation, SA-01/02/07 | Frozen |
| Life Loop Session | `ContractV1AgencySession` + snapshot/restore | Next implementation after this docs alignment |
| Phase 4A | Longitudinal agency on a real Runtime loop |
| Phase 4B | Online SA-03/04/05/06; offline full-sequence detectors are not sufficient |
| Phase 4C | Competing models for attention, memory selection, social cognition. Research only; not a Runtime controller |
| Phase 5 | Competing cognitive architectures |

VirtualBrain does **not** implement the Runtime Attention Controller, Planner, or Memory Store. A Life Loop session is a **single-agent stateful mind adapter**.

## Agent Network

| Stage | Content | Rule |
|---|---|---|
| Trust Foundation v0.2 | Identity / Evidence / Contract / Receipt | Permanently frozen |
| Trust Engine v0.3 | Evidence projections, experience counts, verifier selection | Existing route |
| **v0.4 Coordination Extensions** | Public commitments, joint tasks, dispute/revocation Evidence | No private relationship or subjective affect |
| **v0.5 Version / Evolution / Lineage Evidence** | Agent version, activation, rollback, inheritance, lineage | After Runtime v0.4 models stabilize |

The old phrase “Social / Lineage primitives” is retired. Coordination and Lineage are different stages.

## Strict execution order

```text
0. Three-repo docs alignment (this branch)
1. Runtime Life Loop port / engine / interpreter
2. VirtualBrain ContractV1AgencySession
3. Four closed loops + current manual restart proof
4. Life Loop freeze
5. CheckpointCoordinator + extension governance
6. Runtime v0.2.3 Executive Foundation
7. Runtime v0.2.4 Capability Resolver
8. Runtime v0.2.5 Acquisition / Verification
9. Runtime v0.2.6 Network Delegation
10. Runtime v0.2.7 Social Coordination Foundation
11. Runtime v0.3 Learning / Memory Governance
12. VirtualBrain Phase 4A / 4B / 4C
13. Runtime v0.4 Evolution + Network evolution evidence
14. Runtime v0.5 Reproduction + lineage
```

May run in parallel only when they do not share protocol changes:

- Runtime Life Loop port/engine and VirtualBrain session, with Contract v1 already frozen
- VirtualBrain experiment design and Runtime infrastructure design at the same stage
- Network docs/RFC and Runtime adapter design

Shared protocol changes require a separate ADR/RFC and a frozen schema before any repo implements them.
