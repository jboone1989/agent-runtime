# LivingRuntime Remote

Windows user-level remote development plugin for every LivingRuntime host and repo you already control.

Install it **once for your Windows user**, not once per git checkout. ChatGPT Desktop Work and Codex then use the same plugin to reach:

- `/home/ubuntu/agent-runtime`
- `/home/ubuntu/virtualbrain/current`
- `/home/ubuntu/wechat-traffic-agent` (`ferro`)
- `/home/ubuntu/trading`
- later projects added to `~/.livingruntime/remote.json`

Named tools:

- `list_projects`
- `connection_status`
- `read_file`
- `git`
- `logs`

plus narrower write/process/service tools. Independent of Remote Desktop Commander.

```text
ChatGPT Desktop Work
        → LivingRuntime Remote
        → stdio MCP / launcher.py
        → SSH livingruntime-vm
        → any configured project
```

Repo-local `.agents/plugins/marketplace.json` is only a development aid. Daily use is the personal marketplace at `%USERPROFILE%\.agents\plugins\marketplace.json`. Public Plugins Directory, Secure MCP Tunnel, OAuth, and plugin review are a later track.

## Security boundary

Unchanged:

- SSH uses `BatchMode=yes` and `StrictHostKeyChecking=yes`.
- Passwords and private keys are never stored in the plugin.
- File tools stay inside configured roots and reject symlink escapes.
- Git blocks credential helpers, daemon/shell, and path/config override flags.
- `systemd` and `logs` require exact configured unit names.
- Shell command strings are not a tool.

## 1. Personal Windows install

From this feature branch:

```powershell
cd D:\agent-runtime
git checkout feat/livingruntime-remote-chatgpt-transport
git pull

python .\plugins\livingruntime-remote\scripts\install-personal.py
```

That copies the plugin to `%USERPROFILE%\.codex\plugins\livingruntime-remote` and writes/merges `%USERPROFILE%\.agents\plugins\marketplace.json` with source path `./.codex/plugins/livingruntime-remote`.

ChatGPT Desktop uses a cached copy after install. After you change plugin source, rerun `install-personal.py` and fully restart Desktop.

## 2. Configure hosts and projects once

```powershell
python "$HOME\.codex\plugins\livingruntime-remote\scripts\configure.py" `
  --host livingruntime-vm `
  --root /home/ubuntu `
  --seed-projects `
  --test
```

This writes `~/.livingruntime/remote.json` with host `main` and default projects `agent-runtime`, `virtualbrain`, `ferro`, and `trading`. SSH credentials stay in `~/.ssh`.

Then in ChatGPT Desktop Work the model can do:

```text
list_projects()
read_file(project="virtualbrain", path="pyproject.toml")
git(project="virtualbrain", args=["status"])
logs(project="ferro", lines=100)
```

Absolute paths still work.

## 3. ChatGPT Desktop Work acceptance

Fully quit and restart ChatGPT Desktop. Open the Plugins Directory, choose **LivingRuntime Local** → **LivingRuntime Remote** → Install.

Must pass in Desktop Work:

1. `检查 LivingRuntime Remote 的连接状态。` → `connection_status`
2. `读取 VirtualBrain 的 pyproject.toml。` → `read_file(project="virtualbrain", ...)`
3. `查看 VirtualBrain 当前 git status 和最近 5 个提交。` → `git`
4. `检查 Ferro 最近 100 行服务日志。` → `logs`
5. A real VirtualBrain edit/test/diff task

Then try ordinary Chat (`+` → More, or `@LivingRuntime Remote`). If Work works and ordinary Chat does not, stop changing SSH/MCP. That is a Desktop-only / plan surface limit.

## 4. Later tracks

Secure MCP Tunnel, public HTTPS, OAuth, and Plugins Directory submission stay documented in `PUBLIC_RELEASE.md`. Do not use them as the local-success criteria.

## Tests

```powershell
python -m pip install mcp==1.26.0
python -m unittest discover -s plugins/livingruntime-remote/tests -v
```
