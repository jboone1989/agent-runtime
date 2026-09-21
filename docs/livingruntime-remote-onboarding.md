# LivingRuntime Remote Onboarding

## What it is

LivingRuntime Remote is the lightweight remote capability layer.

It connects ChatGPT-compatible clients to a user's machine through a bounded tool surface.

It is intentionally smaller than pi-remote:

- LivingRuntime Remote: inspect, diagnose, and operate within explicit boundaries.
- pi-remote: persistent coding workflows, sessions, planning, checkpoints, and autonomous development.

## First run

1. Install the LivingRuntime Remote connector/plugin.
2. Configure the target machine transport.
3. Verify connectivity:

```
connection_status
```

4. Inspect available projects and capabilities.
5. Use bounded operations only when required.

## Example workflow

```
User
  -> ChatGPT
  -> LivingRuntime Remote
  -> connection_status
  -> read_file / git / logs
  -> project
```

## Safety model

The public product should expose explicit capabilities rather than unrestricted shell access.

Important properties:

- authenticated transport
- bounded file access
- controlled Git operations
- audit visibility
- clear user ownership of machines and credentials

## Road to public release

Before public distribution:

- production authentication
- public endpoint hardening
- documentation site
- support channels
- privacy and terms
- onboarding tests
