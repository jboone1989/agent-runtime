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
- **v0.2.3 Executive Foundation** — Attention, WorkingContext, Plan/PlanStep, interrupt/wait/resume/replan, ExperienceTrace. Deterministic; no LLM. Capability Resolver is **v0.2.4**.
- **Step E** — pluggable `MotivationEngine`; `VirtualBrainMotivationEngine.wrap(person)` maps PAD/needs/RPE without a hard dependency
- **Step F** — endogenous LLM provider failover (no new user prompt)
- **Next** — Capability Resolver (v0.2.4). Do not jump to Acquisition, Network delegation, or v0.3 Learning.

`step(observation)` remains the injection/test path. Autonomous operation is:

```python
rt = AgentRuntime.bootstrap("./state")          # digital embodiment
rt.tick()
rt.run_forever(max_ticks=10)

from agent_runtime.embodiment import mock_physical_pack
physical = AgentRuntime.bootstrap("./state-p", embodiment=mock_physical_pack())
physical.tick()
```

Cross-project DTO schemas live in `contracts/v1/` (canonical MotivationInput/Output, Self/World/Body/Outcome views). VirtualBrain implements this contract via adapter; neither repo imports the other's internal classes.

v0.2.1 freeze: embodiment restore via `EmbodimentDescriptor`, unified `ActuationGateway`, pluggable `SignalEncoder` mappers, EnvironmentReader vs Controller, full evidence lineage. Contract v1 root is frozen. Post–Life Loop roadmap: `docs/architecture/post-life-loop-roadmap.md`. v0.3 Learning stays frozen until Executive, Resolver, and Memory Governance exist.

## Architecture principle

智能主体不应被设计成一个中央 LLM 控制所有行为，而应被设计成一个由多个异步感知、调节、认知与学习回路共同维持的复杂适应系统。

VirtualBrain 研究这些回路如何产生情绪、自我、动机；Agent Runtime 让这些回路真正长期运行；Agent Network 让这些数字生命组成社会。


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
