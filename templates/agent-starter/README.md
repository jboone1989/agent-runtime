# Agent Starter

This template defines a domain agent hosted by Agent Runtime. Add domain
capabilities, sensors, actuators, policies, and tests. Do not copy Runtime
identity, goal, memory, context, provider, sandbox, MCP, or evidence systems into
the agent package.

An agent package should contain:

```text
agent.yaml
src/<agent_name>/
├── capabilities/
├── sensors/
├── actuators/
└── domain/
tests/
```

Use a capability when a component is stateless. Use a provider-neutral
`AgentBackend` adapter when a vendor agent performs bounded work. Use Contract
v1 when integrating VirtualBrain. Runtime remains the authority for identity,
durable state, authorization, evidence, and recovery.

