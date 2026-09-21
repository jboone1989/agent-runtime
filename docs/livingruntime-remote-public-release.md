# LivingRuntime Remote Public Release Track

## Goal

Package LivingRuntime Remote as an independent product from pi-remote. The product provides bounded remote development/runtime access without requiring the full pi-remote workflow.

## Product boundary

User flow:

ChatGPT
→ LivingRuntime Remote
→ authenticated remote transport
→ bounded tools
→ user's machine/project

Core public capabilities:

- connection status verification
- bounded file reading
- controlled git operations
- diagnostics/log access

## Runtime status

- [x] MCP tool surface exists
- [x] bounded tool policy
- [x] SSH gateway
- [x] local development validation
- [x] product boundary documented
- [x] onboarding flow documented

## Public service requirements

- [ ] stable public endpoint
- [ ] OAuth/user authentication model
- [ ] domain verification
- [ ] privacy policy
- [ ] terms/support pages
- [ ] production abuse limits

## Product requirements

- [x] standalone product definition
- [x] installation flow draft
- [x] first-run onboarding draft
- [ ] troubleshooting guide
- [ ] demo workflow

## Next implementation phase

Restore a standalone release package outside the Agent Runtime lifecycle while keeping transport code reusable.

Required package contents:

- plugin manifest
- MCP configuration
- installation scripts
- self-check command
- public README
- submission metadata

## Relationship with pi-remote

LivingRuntime Remote is the lightweight entry product. pi-remote remains the advanced autonomous development/runtime layer.

The two products share transport foundations but have different user experiences.
