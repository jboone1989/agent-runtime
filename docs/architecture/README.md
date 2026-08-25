# Architecture — frozen cross-stage rules

These documents freeze boundaries that later Capability, Learning, Evolution, and Network work must not reinvent. They do **not** expand the current Life Loop Integration slice.

| Document | What it freezes |
|---|---|
| [post-life-loop-roadmap.md](post-life-loop-roadmap.md) | Version numbers across Runtime / VirtualBrain / Agent Network |
| [executive-boundary.md](executive-boundary.md) | Motivation / Executive / Planner / Policy; Attention; Plan; ExperienceTrace |
| [checkpointing.md](checkpointing.md) | `CheckpointCoordinator`, causal refs, clock domains |
| [contract-extension-governance.md](contract-extension-governance.md) | `extensions.<owner>.<feature>` rules; Contract v1 root stays frozen |
| [evolution-readiness.md](evolution-readiness.md) | Evidence already proven in Runtime v0.3 and the remaining v0.4 unfreeze gates |
| [functional-self-awareness.md](../functional-self-awareness.md) | Behavioral acceptance standard, ablation requirement, and stop condition |

Related:

- Life Loop slice (unchanged implementation scope): [`docs/superpowers/specs/2026-08-22-life-loop-integration-design.md`](../superpowers/specs/2026-08-22-life-loop-integration-design.md)
- Capability Fabric rules (Resolver delayed to v0.2.4): [`docs/capability_fabric_rules.md`](../capability_fabric_rules.md)
- Contract v1: [`contracts/v1/README.md`](../../contracts/v1/README.md)

Hard freezes:

- Contract v1 root shape is frozen. Compatibility goes through extensions, then Contract v2.
- Agent Network Trust Foundation v0.2 is frozen. Runtime convenience must not unfreeze it.
- Runtime production code must not import `virtualbrain`.
- Network protocol must not depend on Agent Runtime types.
