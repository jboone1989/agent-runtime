# Contract v1 — canonical source of truth

This directory is the **only** contract authority for Agent Runtime × VirtualBrain.

- Runtime defines the schemas and golden fixtures.
- VirtualBrain implements them via `src/virtualbrain/adapter/` and keeps identical copies under `tests/fixtures/contract_v1/`.
- Do not add a second product. Do not let VirtualBrain redefine these DTOs.

Compatibility: `1.x` is additive. `2.0` is a breaking change.

Every message includes `"contract_version": "1.0"`.

MotivationOutput uses `action_tendencies` (never `tendencies`).

BodyStateView is `components` + `sensed_channels` + `inferred_channels` + `extensions`. VirtualBrain chemicals belong in `extensions.virtualbrain`, never as the Body DTO root.
