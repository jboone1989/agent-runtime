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

## Release checklist

### Runtime

- [x] MCP tool surface exists
- [x] bounded tool policy
- [x] SSH gateway
- [x] local development validation

### Public service

- [ ] stable public endpoint
- [ ] OAuth/user authentication model
- [ ] domain verification
- [ ] privacy policy
- [ ] terms/support pages
- [ ] production abuse limits

### Product

- [ ] standalone README
- [ ] installation guide
- [ ] first-run onboarding
- [ ] troubleshooting guide
- [ ] demo workflow

## Relationship with pi-remote

LivingRuntime Remote is the lightweight entry product. pi-remote remains the advanced autonomous development/runtime layer.

The two products share transport foundations but have different user experiences.
