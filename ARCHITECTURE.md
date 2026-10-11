# agent-runtime: architecture contract

Document module ownership and the reason for each boundary here.
Each feature must have one owning module; other modules may use public contracts.

## Explicitly forbidden dependencies

| From | Must not depend on |
| --- | --- |
| `learning` | `dashboard` |
| `memory` | `dashboard` |

## Ownership (fill in with actual project modules)

| Module | Owns | Public API |
| --- | --- | --- |
| TODO | TODO | TODO |

The machine-readable source of truth is `.agent-engineering/policy.json`.
Changing boundaries requires a separate review; don't silently relax them.
