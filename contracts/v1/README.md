# Contract v1 — canonical source of truth

This directory is the **only** contract authority for Agent Runtime × VirtualBrain.

```
contracts/v1/
├── schemas/     JSON Schema (strict: required + additionalProperties false)
├── fixtures/    golden messages
└── manifest.json  sha256 of every schema and fixture
```

VirtualBrain vendors these files under `tests/fixtures/contract_v1/` and records `contract.lock.json`. Do not depend on a Windows checkout path.

Compatibility: `1.x` is additive. `2.0` is a breaking change.

Every message includes `"contract_version": "1.0"`.

MotivationOutput uses `action_tendencies` (never `tendencies`).

VirtualBrain-specific fields belong in `extensions.virtualbrain`, never on the canonical root.

`relation` on body components is one of: CORE_SELF, BODY, OWNED, DEPENDENCY, ALLY, EXTERNAL, UNKNOWN.
