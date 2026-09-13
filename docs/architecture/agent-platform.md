# ADR: Agent Runtime as the shared agent platform

Status: accepted

## Decision

Every new stateful agent in this project family is hosted by Agent Runtime.
A stateful agent is any component that owns an identity, goals, memory, durable
state, or authority to act. Stateless tools, skills, provider agents, and model
clients are capabilities of a Runtime-hosted agent; they are not parallel
runtimes.

The dependency directions are:

```text
application composition root
├── agent-runtime
├── optional virtualbrain
├── optional agent-network
└── optional provider SDKs

agent-runtime <-> Contract v1 <-> virtualbrain
agent-runtime -> AgentBackend adapters -> provider SDKs
```

Agent Runtime remains the authority for identity, goals, memory, context
projection, policy, budgets, action records, evidence, learning, and recovery.
Provider sessions are execution resources. They never become the Runtime
identity or trusted memory authority.

VirtualBrain is an optional mind/motivation implementation. It does not own
Runtime planning, attention control, memory storage, tool execution, or
authorization. Neither production package imports the other package's internal
types; an application composition root connects them through Contract v1.

Concrete access to the external environment is also a Runtime responsibility.
Read-only Git, workspace, clock, environment, system, process, HTTP, event, and
database observations enter through Capability Inventory and the read
invocation boundary. VirtualBrain may translate a JSON-safe observation result
into its own `Percept`; it does not implement those clients or traversal
mechanisms. Observation capabilities never imply mutation authority.

## Reuse before create

Do not implement infrastructure already supplied by an adopted SDK unless a
documented requirement cannot be met and an acceptance test demonstrates the
gap. In particular, do not recreate:

- provider HTTP transports, retry protocols, or streaming parsers;
- model tool-call loops;
- generic shell, file, search, edit, or web tools;
- provider thread/session persistence and resume;
- generic MCP clients;
- provider sandboxes;
- provider prompt caching or conversation compaction.

Runtime adapters may translate provider events into domain records, intersect
provider permissions with Runtime policy, and verify outcomes. Those adapters
must not leak provider SDK types into Runtime domain models.

## Authority boundary

Backend requests have an explicit mode:

- `ADVISORY`: tools and side effects are forbidden. Output is unverified input.
- `EXECUTION`: tools are limited to the pre-authorized set and resources.

Provider configuration can narrow authorization but can never expand Runtime
authorization. A provider's success claim is not verified evidence. Runtime
verification remains authoritative.

## Consequences

- New agents declare a manifest and domain extensions instead of creating a new
  runtime.
- Agent Runtime remains useful with no provider SDK installed.
- VirtualBrain remains useful with Agent Runtime absent for isolated research.
- Provider loss cannot erase Runtime identity, goals, or trusted memory.
- Contract v1 stays frozen; compatible additions use namespaced extensions.
