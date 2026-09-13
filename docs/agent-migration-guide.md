# Migrating an agent onto Agent Runtime

1. Classify the existing component. A component with identity, goals, memory,
   durable state, or autonomous actions is an agent. A stateless operation is a
   capability, not another runtime.
2. Create and validate `agent.yaml` from `templates/agent-starter`.
3. Remove duplicated identity, goal, plan, memory, provider-loop, sandbox, MCP,
   and session infrastructure.
4. Register domain sensors and actuators. Route every side effect through
   Runtime policy and evidence recording.
5. Register external coding or general-purpose agents through `AgentBackend`.
   Keep provider SDK types inside the adapter.
6. Project only `BoundedRuntimeContext` to a backend. Never send secrets or use
   provider history as trusted Runtime memory.
7. Add restore, denial, evidence-failure, and provider-loss tests.

## Definition of done

- The agent runs with provider SDKs absent when its domain permits it.
- Restart preserves Runtime identity and domain state.
- An advisory request cannot use tools.
- An execution backend cannot exceed Runtime authorization.
- Provider success does not override failed Runtime verification.
- The package does not implement a parallel Runtime subsystem.

