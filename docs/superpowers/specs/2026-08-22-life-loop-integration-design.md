# Life Loop Integration — Contract-driven Runtime × VirtualBrain

Date: 2026-08-22
Status: approved design (implementation not started)
Slice: Port + engines + AgencySession + 4 closed loops + restart. **Not** 500-tick baseline.

## Goal

Prove that a real Agent Runtime and a real VirtualBrain mind can close a long-lived loop **only** through Contract v1:

```text
sense → perceive → regulate → Observation
  → MotivationInput v1
  → ContractV1AgencySession.evaluate
  → MotivationOutput v1
  → TendencyInterpreter
  → goals / planner / ActuationGateway
```

This is an integration proof, not a claim that attention, memory-informed motivation, long-term planning, support seeking, or learning exist. It is not v0.3 Learning, not Executive Foundation (v0.2.3), not Capability Resolver (v0.2.4), and not a Contract v1 schema change.

## Current baseline

| Repo | Branch | HEAD | Role |
|---|---|---|---|
| agent-runtime | `feat/life-loop-integration` | `f7a13bc` | design approved; Port / ContractMotivationEngine / TendencyInterpreter not implemented yet |
| virtualBrain | `feat/phase3.1-runtime-port` | `928786e` | Phase 3.1 contract sync frozen to Runtime `fa6e3f1` |

Already in Runtime: `contracts/v1` builders (`motivation_input`, views, `motivation_output`), `jsonschema>=4.20`, `legacy_tendency_interpreter.py`. Missing: `MotivationPort`, `ContractMotivationEngine`, real `TendencyInterpreter`, stateful mind session, restart of mind state.

`MotivationEngine.process(...)` signature stays. `DefaultMotivationEngine` and `VirtualBrainMotivationEngine` stay for existing tests.

## Non-goals (this slice)

- 500-tick baseline and A/B/C ablations (Default / VirtualBrain / NoAffect)
- Changing `SimplePlanner`, Sensor, Perception, Body, Actuator (except a bug fix)
- Changing Contract v1 root/schema
- Deleting `VirtualBrainMotivationEngine`
- Capability Resolver / acquisition (v0.2.4+, after Executive Foundation)
- Executive Foundation (v0.2.3): Attention, WorkingContext, long-lived Plan, interrupt/resume
- CheckpointCoordinator (Life Loop Hardening, after this slice freezes)
- Mapping Runtime `battery` (or any metric) to `CORT`
- Importing `virtualbrain` from Runtime production code
- Per-tick `new VirtualPerson()` or `if environment == robot`

## Branches

- Runtime: `feat/life-loop-integration` from current v0.2.2 HEAD. Do **not** reuse `feat/v0.2.2-life-loop` (v0.2.2 already means capability foundation).
- VirtualBrain: `feat/life-loop-session` from `928786e`, so `feat/phase3.1-runtime-port` stays the Phase 3.1 freeze tip.

## Architecture

```text
tick:
  Sensor → Perception → Regulation → Observation(event.resolved)
  ContractMotivationEngine
    canonical.motivation_input  + jsonschema.validate
    MotivationPort.evaluate(dict) -> dict
  ContractV1AgencySession (VB, one agent_id, long-lived)
    ContractAppraisalBridge   (structured; no LLM; no CORT from Runtime)
    Interoception / ViabilityAffect / Needs / research SelfModel
    compute_motivation on INTERNAL research input
    to_motivation_output + jsonschema.validate
  TendencyInterpreter → MotivationState
  GoalGenerator / SimplePlanner / ActuationGateway
```

Hard rules:

1. Runtime production code never imports `virtualbrain`. Tests may `pytest.importorskip("virtualbrain")`.
2. Contract v1 root is frozen. Extensions only inside VirtualBrain's own research mapping, not new root fields.
3. `compute_motivation()` reads `self_state.valence` at **root**. Contract `SelfStateView` keeps affect in `extensions.virtualbrain`. The session must build a **research** `MotivationInput` from its own affect/interoception/needs. Never pass Runtime `SelfStateView` through `compute_motivation` unchanged.
4. Mind snapshot is an opaque dict owned by the session. `AgentRuntime.persist()` does not write PAD/CORT/interoception.
5. `ContractMotivationEngine` does **not** call `tendencies_from_viability()`. Viability is in MotivationInput; the mind responds.

## Runtime interfaces

### `MotivationPort`

File: `src/agent_runtime/motivation/port.py`

```python
class MotivationPort(Protocol):
    def evaluate(self, request: dict) -> dict: ...
```

No `snapshot`/`restore` on this protocol. Restart tests talk to the session object directly.

`InProcessMotivationPort` in the same file: wraps any object with `evaluate`. Does not import VirtualBrain.

### `ContractMotivationEngine`

File: `src/agent_runtime/motivation/contract_engine.py`

Constructor: `ContractMotivationEngine(port, interpreter=None, agent_id)`. `interpreter` defaults to `DefaultTendencyInterpreter()`. `agent_id` comes from `identity.agent_id` at bootstrap. Each `process()`:

1. `canonical.motivation_input(self_model=..., agent_id=..., world_state=..., viability=..., event=...)`
2. Validate MotivationInput against `contracts/v1/schemas/motivation_input.schema.json`
3. `response = port.evaluate(request)`
4. Validate MotivationOutput; unknown `direction` or schema failure **raises** (no `MotivationState.neutral()` fallback)
5. Copy scalars into `MotivationState` (valence, arousal, urgency, threat, opportunity, drives, attention_priority, biases, learning_signal, self_model_updates)
6. For each contract tendency, `interpreter.interpret(...)`; merge `executable_tendencies` by strength; apply interpreter deltas onto exploration/risk then clip to contract ranges

`process()` does not gain new parameters. Interpreter `goals` and `available_capabilities` are empty dict/list in this slice so `MotivationEngine` Protocol stays unchanged.

### `TendencyInterpreter`

File: `src/agent_runtime/motivation/interpreter.py`

```python
@dataclass
class TendencyEffect:
    risk_bias: float
    exploration_delta: float
    goal_priority_delta: dict[str, float]
    planning_constraints: list[str]
    candidate_subgoals: list[str]
    executable_tendencies: list[ActionTendency]  # first-slice SimplePlanner compat

class TendencyInterpreter(Protocol):
    def interpret(
        self,
        tendency: ContractActionTendency,
        *,
        self_model: SelfModel,
        world_state: WorldState,
        goals: list,
        available_capabilities: dict,
    ) -> TendencyEffect: ...
```

`DefaultTendencyInterpreter` (embodiment-agnostic):

| direction | bias | compatible `ActionTendency.action` |
|---|---|---|
| REST, AVOID | risk down, exploration down | `conserve` |
| EXPLORE | exploration up | `introspect` |
| CONFRONT | — | `diagnose` |
| SEEK_SUPPORT | — | `record` |
| APPROACH | — | `maintain` |
| FREEZE | exploration down | `noop` |

`CONTRACT_ACTION_TO_KIND` stays in `legacy_tendency_interpreter.py`. Do not move it back into `canonical.py`. Later slice: Planner consumes `TendencyEffect` directly and drops the compatible action column.

Files Runtime must **not** change in this slice: `canonical.py`, Sensor, Perception, Body, Actuator, `SimplePlanner`.

## VirtualBrain session

File: `src/virtualbrain/adapter/service.py`

```python
class ContractV1AgencySession:
    def __init__(self, agent_id: str, *, cfg=None, affect_name: str = "viability"): ...
    def evaluate(self, request: dict) -> dict: ...
    def snapshot(self) -> dict: ...
    def restore(self, snapshot: dict) -> None: ...
```

One session, one `agent_id`. Mismatched `request["self_state"]["agent_id"]` raises. No process-global session registry required; tests construct the session and wrap it in `InProcessMotivationPort`.

Default stack (not a full `VirtualPerson`):

- `InteroceptionModel` (channels sourced from session stress/danger proxies, not Runtime CORT fields)
- `ViabilityAffectModel` (`affect_name="viability"`). `chemical_pad` may be selected later via config; this slice does not send chemical pulses from appraisal
- `NeedsState`
- `SelfRepresentation` + `SelfModelUpdater`
- `compute_motivation` + `to_motivation_output`

`test_runtime_virtualbrain.py` keeps `VirtualBrainMotivationEngine.wrap(person)`.

### `evaluate` pipeline

1. Validate MotivationInput with vendored `tests/fixtures/contract_v1/schemas`
2. `ContractAppraisalBridge.appraise(request)` → `threat_delta`, `demand_impacts`, `controllability`
3. Update needs; `affect.step`; `interoception.step` with `true_state={"stress": stress_proxy, "danger": danger_proxy}` (proxies from appraisal/viability, never Runtime CORT fields). Configure interoception channels to source `stress` and `danger`.
4. Build **internal** research `MotivationInput` from affect PAD/threat, needs pressures, interoception perceived stress/danger, plus contract world_state/viability/events
5. `compute_motivation` → `to_motivation_output` → validate → return

`dt` = `cfg.time.minutes_per_tick / 60`. MotivationInput has no timestamp; the session increments its own tick.

### Appraisal (deterministic, first slice)

No LLM. No `environment_id` branch. Runtime never assigns CORT.

| Input | Mind interpretation |
|---|---|
| `event.data.resolved is True` | small `threat_delta`; safety satisfaction up slightly; control up |
| `event.data.resolved is False` | large `threat_delta`; safety satisfaction down; control down |
| viability dimension `< 0.7` | add discrepancy (`security` / `operational_integrity` → safety; `capability` / `compute` → competence; `storage` / `financial_resources` → resource_security) |
| `world_state.environment["battery"]` or body sensed `battery` `< 0.7` | resource discrepancy only; never read `environment_id` |

Preferred viability baseline for discrepancy is **0.7** (same as research viability default).

### Snapshot / restore

Persist JSON-serializable:

- `schema_version`, `agent_id`, `tick`, `affect_name`
- affect: PAD, threat, research viability vector, `prev_overall`, `perceived_control`
- interoception: belief estimates/confidence, rng seed, channel params
- `needs.sat`
- self: identity, capability_estimates, boundaries
- updater history summary (`belief_before` / `after` / error only)

Do not persist: providers, hardware handles, Runtime objects, live rng objects (seed only).

`restore` must not reset threat/PAD/interoception to a fresh-session baseline.

Restart harness (Runtime does not call these):

```text
rt.persist()
mind = session.snapshot()
session2.restore(mind)
rt2 = AgentRuntime.restore(
    ...,
    motivation_engine=ContractMotivationEngine(
        InProcessMotivationPort(session2),
        agent_id=session2.agent_id,
    ),
)
```

## Tests

### Runtime unit (no VirtualBrain install required)

- Built MotivationInput validates
- Fake port returning missing fields or unknown `direction` raises
- `AVOID` → `conserve` + lower risk/exploration; `EXPLORE` → `introspect`
- Engine does not merge `tendencies_from_viability`

### VirtualBrain unit (no Runtime process)

- Resolved vs unresolved: unresolved threat strictly higher; output schema-valid
- Unresolved tendencies dominated by `AVOID` / `FREEZE` / `CONFRONT`, not `EXPLORE`
- Low resource viability (no embodiment field) yields `REST` or `AVOID`; session source has no `environment_id` branch
- snapshot/restore: threat, PAD, interoception belief match pre-snapshot (`pytest.approx(..., abs=1e-6)`)
- Wrong `agent_id` raises

### Closed loop 1 — Digital resolved threat

Same env as current `test_life_loop.py`: primary `error_rate=0.72`, backup `0.02`. Engine is `ContractMotivationEngine` + real session.

- local `switch_provider`
- no `COGNITIVE_CAPABILITY_AT_RISK`
- `motivation.arousal` above that session's resting value
- `motivation.threat < 0.5`
- no new VIABILITY goal this tick whose description is capability-restore

### Closed loop 2 — Digital unresolved threat

Both providers `0.95`.

- `COGNITIVE_CAPABILITY_AT_RISK`
- `motivation.threat >= 0.7`
- a `VIABILITY` or `INTERNAL` goal exists
- that goal's `evidence_refs` is non-empty

### Closed loop 3 — Mock physical battery

`battery=0.10`. Same session class as digital tests; session code must not read `environment_id`.

- `conserve` or `resource_conservation >= 0.6`
- still `AgentRuntime`
- motivation includes REST or AVOID (interpreter side); arousal at least resting

### Closed loop 4 — Restart continuity

20 ticks on a digital scenario. At tick 10 persist Runtime + session as above.

- `rt2.identity.agent_id` unchanged
- goal description set not wiped
- session2 threat / valence / interoception belief match pre-persist (`pytest.approx(..., abs=1e-6)`)
- tick 11 threat does not collapse to a brand-new session baseline

`RecordingBrain` may remain in old adapter unit tests. It is **not** the primary proof in `tests/test_life_loop.py`.

Existing Runtime and VirtualBrain suites must stay green. Life Loop tests are additive.

## Error handling

- Schema-invalid input or output: raise
- Unknown contract `direction`: raise (`canonical.motivation_output` already does this on the serialize path; the engine must not catch-and-drop)
- Port exception: propagate
- `agent_id` mismatch on session: raise

## Scope clarifications (do not expand this slice)

1. This slice is an integration proof. It does not implement attention, memory-informed motivation, long-term planning, support seeking, or learning.
2. `TendencyInterpreter` `goals` and `available_capabilities` stay empty here. Do not grow `MotivationEngine.process()` arguments to carry executable context; that belongs to later ExecutiveContext.
3. `SEEK_SUPPORT → record` and the other SimplePlanner compatibility mappings are temporary. They are not the final social or help-seeking semantics.
4. Side-by-side Runtime snapshot and session snapshot are only for current tests. Formal long-lived restore is later `CheckpointCoordinator` work. See `docs/architecture/checkpointing.md`.
5. Contract v1 extensions stay empty on the Runtime-owned path. Memory / Goals / Plans enter the mind only after extension governance and a budgeted projection. See `docs/architecture/contract-extension-governance.md`.
6. `recent_events` is the current tick's event. It is not autobiographical memory or a complete experience trace.

Post–Life Loop version table: `docs/architecture/post-life-loop-roadmap.md`.

## Out of scope follow-ups (named so they are not accidental)

1. 500-tick baseline + three controls (VirtualBrain Phase 4A, after a real Runtime loop exists)
2. Planner native `TendencyEffect` (drop compatible action column)
3. Optional `chemical_pad` AgencySession and online SA-03+/SA-06+ (Phase 4B)
4. Life Loop Hardening: `CheckpointCoordinator`, extension governance, lifecycle error semantics
5. Runtime v0.2.3 Executive Foundation (Attention / WorkingContext / Plan / interrupt / ExperienceTrace)
6. Runtime v0.2.4 Capability Resolver
7. Runtime v0.3 Learning / Memory Governance
