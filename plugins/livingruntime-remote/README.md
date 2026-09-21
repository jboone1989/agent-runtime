# LivingRuntime Remote local plugin

This plugin gives supported local ChatGPT/Codex clients a second remote-development path that is independent of Remote Desktop Commander.

It launches a local stdio MCP process. That process uses the operating system's OpenSSH client and an existing key/SSH-agent configuration to reach the LivingRuntime host. Passwords and private keys are never stored in the plugin.

## One-time local setup

Requirements:

- Python 3.11+ available as `python`
- OpenSSH client available as `ssh`
- key-based SSH access to the target host
- the target host has `python3`

Create an SSH config alias first, for example `livingruntime-vm`. Verify it yourself with normal SSH so that the server host key is already trusted.

Then configure only non-secret scope metadata:

```powershell
python .\plugins\livingruntime-remote\scripts\configure.py --host livingruntime-vm --root /home/ubuntu --test
```

Add exact service names only when remote service lifecycle control is needed:

```powershell
python .\plugins\livingruntime-remote\scripts\configure.py `
  --host livingruntime-vm `
  --root /home/ubuntu `
  --unit content-agent.service `
  --test
```

Configuration is stored in `~/.livingruntime/remote.json`. It contains the SSH alias, workspace roots, and service allowlist only.

The launcher creates an isolated runtime under `~/.livingruntime/mcp-runtime` and pins `mcp==1.26.0`. It does not modify the system Python installation.

## Local plugin discovery

The repository also contains `.agents/plugins/marketplace.json`. Open the repository as a trusted project in a supported ChatGPT desktop/Codex local client, restart the client, install **LivingRuntime Remote** from the **LivingRuntime Local** marketplace, and start a new session after plugin changes.

## Security boundary

- SSH uses `BatchMode=yes` and `StrictHostKeyChecking=yes`.
- No password prompt is supported.
- File reads/writes resolve remote paths against configured roots and reject symlink escapes.
- Process operations are limited to the same remote Unix user and processes whose cwd is under a configured root.
- systemd and journal tools require exact configured unit names.
- Shell command strings are not exposed as a tool.
- Local tool activity is appended to `~/.livingruntime/remote-audit.jsonl`.

This is still a development execution channel, not a hostile-code sandbox. A permitted development process runs with the privileges of the configured remote Unix account.
