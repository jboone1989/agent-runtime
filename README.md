# Agent Runtime

A persistent runtime for autonomous agents. This repository is the **digital life layer**. It is not VirtualBrain and not Agent Network.

| Project | Role |
|---|---|
| VirtualBrain | Research: why a subject forms self and motivation |
| **Agent Runtime** | How a subject continues to exist, form internal goals, and act |
| Agent Network | How subjects trust one another |

## Status

- **v0.1 Continuity of Self** — identity, SelfModel / WorldModel, memory, goals, viability, AutonomyPolicy, persist/restore
- **Step E** — pluggable `MotivationEngine`; `VirtualBrainMotivationEngine.wrap(person)` maps PAD/needs/RPE without a hard dependency
- **Step F** — endogenous LLM provider failover (no new user prompt)

Not implemented: evolution/reproduction *execution* (schemas only), Agent Network as a dependency.

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
