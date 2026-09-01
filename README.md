# Agent Runtime

A persistent runtime for autonomous agents. This repository is the **digital life layer**. It is not VirtualBrain and not Agent Network.

| Project | Role |
|---|---|
| VirtualBrain | Research: why a subject forms self and motivation |
| **Agent Runtime** | How a subject continues to exist, form internal goals, and act |
| Agent Network | How subjects trust one another |

## Status

- **v0.1 Continuity of Self** — identity, SelfModel / WorldModel, memory, goals, viability, AutonomyPolicy, persist/restore
- **v0.2 Sensory + Regulatory + Survival** — `tick()` / `run()` collect sensors, local regulation, then escalate only if needed
- **v0.2.1 Environment & Embodiment Foundation** — EnvironmentAdapter, RawObservation → Perception → Signal, BodyModel / SelfBoundary, delta Observation, component-aware integrity, Actuator protocol, mock physical environment. Same Runtime core for digital and physical packs.
- **v0.2.2 Capability Foundation** — inventory, need, gap, and policy records. No resolver, acquisition, or network delegation yet.
- **v0.2.3 Executive Foundation** — Attention, WorkingContext, Plan/PlanStep, interrupt/wait/resume/replan, ExperienceTrace. Deterministic; no LLM.
- **v0.2.4 Capability Resolver** — Reuse Before Create on `PlanStep.required_capabilities`. Records search evidence and decisions. Does not install anything.
- **v0.2.5 Acquisition / Verification** — inspect → policy → sandbox → verify → activate for a fixture skill adapter. GitHub/pip/MCP/Network stay out. Activation is revocable. Only ACTIVE is executable.
- **v0.2.6 Network Capability / Delegation** — Network claims become `CapabilityCandidate`s; `DELEGATE_REMOTE` does not mark them OWNED. Trust counts do not authorize. Quorum and artifact revision bind receipts. Irreversible retries reuse one idempotency key.
- **v0.2.7 Social Coordination Foundation** — DialogueAct, consent, public vs private commitments, JointGoal. Liking and verified identity do not raise authorization. No social psychology model.
- **v0.3 Learning + Memory Governance** — ExperienceTrace credit, persisted verified-learning-evidence ledger, competing hypotheses, evidence-gated strategy replay/rollback, habit unbind, memory provenance/quarantine. Learning cannot raise authorization. No fine-tune.
- **Runtime Observation Foundation** — one read-only provider bundle registered
  through Capability Inventory for Git, workspace, clock, environment, system,
  process, and HTTP observations. Concrete environment access lives here, not
  in VirtualBrain; mutation remains on the separate execution path.
- **Step E** — pluggable `MotivationEngine`; `VirtualBrainMotivationEngine.wrap(person)` maps PAD/needs/RPE without a hard dependency
- **Step F** — endogenous LLM provider failover (no new user prompt)
- **Next (frozen)** — Evolution (v0.4). It may consume only v0.3 verified evidence and remains blocked on VirtualBrain Phase 4A/4B/4C plus Network evolution-evidence readiness. See `docs/architecture/evolution-readiness.md`.

`step(observation)` remains the injection/test path. Autonomous operation is:

```python
rt = AgentRuntime.bootstrap("./state")          # digital embodiment
rt.tick()
rt.run_forever(max_ticks=10)

from agent_runtime.embodiment import mock_physical_pack
physical = AgentRuntime.bootstrap("./state-p", embodiment=mock_physical_pack())
physical.tick()
```

For an actual bounded launch from the repository, install the project and run:

```console
python -m agent_runtime --state-dir ./state --ticks 5
```

To attach the DeepSeek Anthropic-compatible API configured in the current user
environment, set `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`,
then run:

```console
python -m agent_runtime --state-dir ./state --ticks 1 --llm
```

The provider is advisory rather than the life-loop controller. It receives a
bounded state summary only when an LLM probe is explicitly requested. Its output
is recorded as `external_unverified` and quarantined from trusted memory and
authorization. API keys are read from the environment and are never persisted in
Agent state.

After obtaining consent to send the bounded state summary to the configured
provider, run the repeatable continuity and boundary evaluation with:

```console
python scripts/evaluate_digital_life.py --state-dir ./state
```

## Functional self-awareness (behavioral scope)

The project uses an operational, behavioral definition rather than claiming
subjective experience. A Runtime must spontaneously monitor internal changes,
separate self/world causes, preserve and revise evidence-backed self-beliefs,
make and reconcile counterfactual self-predictions, calibrate what it knows, and
outperform controls where persistent SelfModel use is removed.

The frozen acceptance rules are in
`docs/functional-self-awareness.md`. Run the complete three-trial acceptance suite
with:

```console
python scripts/evaluate_functional_self_awareness.py --output-dir ./state/awareness-eval --trials 3 --cycles 100
```

Run a real DeepSeek injection and stateless-text comparison, after consent to send
the bounded state, with:

```console
python scripts/evaluate_llm_awareness_adversarial.py --state-dir ./state
```

The experimental `self_awareness_enabled=False` constructor option removes the
causal use of monitoring, conflict reconciliation, intervention evidence, and
self-prediction while preserving the rest of the Runtime for ablation comparisons.

The first launch creates an identity; later launches from the same state directory
restore that identity and continue its cycle counter. Use `--physical` only when
creating a new mock-physical state directory. The installed `agent-runtime` command
accepts the same options.

Cross-project DTO schemas live in `contracts/v1/` (canonical MotivationInput/Output, Self/World/Body/Outcome views). VirtualBrain implements this contract via adapter; neither repo imports the other's internal classes.

v0.2.1 freeze: embodiment restore via `EmbodimentDescriptor`, unified `ActuationGateway`, pluggable `SignalEncoder` mappers, EnvironmentReader vs Controller, full evidence lineage. Contract v1 root is frozen. Post–Life Loop roadmap: `docs/architecture/post-life-loop-roadmap.md`. Runtime v0.3 now records verified evidence; that is necessary but not sufficient to unfreeze v0.4.

## Architecture principle

智能主体不应被设计成一个中央 LLM 控制所有行为，而应被设计成一个由多个异步感知、调节、认知与学习回路共同维持的复杂适应系统。

VirtualBrain 研究这些回路如何产生情绪、自我、动机；Agent Runtime 让这些回路真正长期运行；Agent Network 让这些数字生命组成社会。

The external observation boundary is documented in
[`docs/architecture/observation-foundation.md`](docs/architecture/observation-foundation.md).


```python
from agent_runtime import AgentRuntime
from agent_runtime.observations import Observation

rt = AgentRuntime.bootstrap(state_dir="./state")
rt.submit_goal("write the weekly note")
rt.step(Observation.self_and_world(self_metrics={"compute": 0.8}, world={}))
rt.persist()

restored = AgentRuntime.restore(state_dir="./state")
assert restored.knows_who_it_is()
```

Optional VirtualBrain (install the sibling package, then):

```python
from agent_runtime.integrations.virtualbrain import VirtualBrainMotivationEngine

AgentRuntime.bootstrap(
    state_dir="./state",
    motivation_engine=VirtualBrainMotivationEngine.wrap(person, provider=provider),
)
```

LLM model id lives only in `src/agent_runtime/config/default.yaml` (`deepseek-v4-pro[1m]`). Business code does not hardcode it.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
