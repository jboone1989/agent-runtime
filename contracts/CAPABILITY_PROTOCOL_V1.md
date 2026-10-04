# Capability protocol v1

This contract is language-neutral and separate from the Runtime × VirtualBrain
contract under `contracts/v1`.

Agent Runtime owns capability routing, authorization ceilings, durable receipts,
and lifecycle. Implementations may live in Python, Rust, Go, Java, MCP servers,
local processes, or remote services.

Stable files:

- `capability_call_v1.schema.json`
- `capability_reply_v1.schema.json`
- `memory_call_v1.schema.json`

Memory v1 intentionally freezes only deterministic reusable operations:
`remember`, `recall`, `read`, `changes`, and `health`.
Model-assisted consolidation remains implementation-side until cognition is
separated from the current Ferro memory code.
