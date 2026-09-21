# LivingRuntime Remote

LivingRuntime Remote is a lightweight remote capability layer for ChatGPT-compatible clients.

It connects ChatGPT to a user's machine through authenticated transport and bounded tools. It is intentionally smaller than pi-remote: LivingRuntime Remote inspects, diagnoses, and operates within explicit boundaries; pi-remote adds persistent coding workflows, sessions, planning, checkpoints, and autonomous development.

## Version and identity

- identity: `livingruntime.remote`
- capability version: `0.4.3`
- canonical capability registry: `scripts/contract.py`

## Capabilities

- connection_status
- capabilities
- list_projects
- read_file
- write_file
- list_dir
- git
- exec
- process
- systemd
- logs
- apply_patch
- diagnostics

## Design principles

- No password or private-key storage in the plugin.
- SSH keys remain owned by the user environment.
- File access is scoped.
- Git and command execution are bounded.
- Service control is allowlisted.
- Destructive operations are auditable.
- Capability discovery reports missing tools explicitly.

## Setup

Requirements:

- Python 3.11+
- OpenSSH client
- key-based SSH access
- target host with Python 3

Configure the target:

```bash
python scripts/configure.py --host livingruntime-vm --root /home/ubuntu --test
```

Verify:

```
capabilities
connection_status
```

## Relationship with pi-remote

LivingRuntime Remote is an independent product and the simple remote entry point. The public relay/release implementation is maintained alongside pi-remote so the two can share transport foundations without duplicating infrastructure.
