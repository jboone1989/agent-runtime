# LivingRuntime Remote

Bounded remote-development tools for a LivingRuntime host you already control.

This plugin/app exposes first-class MCP tools named:

- `connection_status`
- `read_file`
- `git`
- `logs`

plus narrower write/process/service tools. It is independent of Remote Desktop Commander. Do not treat Desktop Commander, a generic shell, or Codex-only marketplace wiring as a substitute for these tools.

There are two supported transports. They are complementary, not alternatives:

| Transport | Who starts the MCP server | Who can see the tools |
| --- | --- | --- |
| Local stdio | Codex / ChatGPT Desktop Work after installing the local plugin | Local plugin hosts only |
| Secure MCP Tunnel | `scripts/tunnel.py` on Windows or Linux, wrapping the same MCP server | ChatGPT ordinary chat, on plans that allow developer-mode custom MCP apps |

`.agents/plugins/marketplace.json` and `.codex/config.toml` only prove the **local stdio / Codex** path. They are not evidence that ChatGPT ordinary chat can discover LivingRuntime Remote.

## Security boundary

These limits are unchanged:

- SSH uses `BatchMode=yes` and `StrictHostKeyChecking=yes`.
- Passwords and private keys are never stored in the plugin.
- File tools resolve remote paths against configured roots and reject symlink escapes.
- Git blocks credential helpers, daemon/shell, and path/config override flags.
- Process tools are limited to the same remote Unix user and cwds under a configured root.
- `systemd` and `logs` require exact configured unit names.
- Shell command strings are not a tool. `bash -c` / `bash -lc` stay blocked on the gateway.
- Audit records go to `~/.livingruntime/remote-audit.jsonl` or the gateway audit log.
- Loopback MCP ports stay on `127.0.0.1`. Do not expose `8765` or `8766` to the public internet.

This is still a development execution channel, not a hostile-code sandbox.

## 1. Codex / local stdio usage

Requirements: Python 3.11+ as `python`, OpenSSH as `ssh`, key-based access, and `python3` on the host.

```powershell
python .\plugins\livingruntime-remote\scripts\configure.py --host livingruntime-vm --root /home/ubuntu --unit content-agent.service --test
```

The local plugin launches `scripts/launcher.py`, which is Windows-safe and does not require bash. Codex loads it from `mcp.json` (`type: stdio`).

Open this repository as a trusted project, restart the local ChatGPT/Codex client, install **LivingRuntime Remote** from the **LivingRuntime Local** marketplace, and start a new session.

## 2. ChatGPT ordinary chat via Secure MCP Tunnel

ChatGPT ordinary chat cannot spawn this stdio server by itself. OpenAI’s supported private path is [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels): ChatGPT talks to an OpenAI-hosted tunnel endpoint; `tunnel-client` makes outbound HTTPS to OpenAI and forwards MCP requests to the local stdio or loopback HTTP server.

Do not put `http://127.0.0.1:8765/mcp` on the public internet, and do not use ngrok as the production private path.

### One-time OpenAI setup

1. Create a tunnel in [Platform tunnel settings](https://platform.openai.com/).
2. Associate it with the ChatGPT workspace that should see it.
3. Grant Tunnels Read + Use to the operator. Creating tunnels also needs Tunnels Manage.
4. Copy the `tunnel_...` id and a **runtime API key**. They are different values.

```powershell
python .\plugins\livingruntime-remote\scripts\configure.py --host livingruntime-vm --root /home/ubuntu --unit content-agent.service --tunnel-id tunnel_REPLACE_ME --test
$env:CONTROL_PLANE_API_KEY = "runtime-key-from-platform"
python .\plugins\livingruntime-remote\scripts\tunnel.py --install-client
```

On Windows you can also run `scripts\start-tunnel.cmd`. Keep that process running. Then:

1. Enable Developer mode if your ChatGPT plan allows it.
2. Open ChatGPT Plugins and select the plus button.
3. Name the app **LivingRuntime Remote**.
4. Under Connection choose **Tunnel**, then select or paste the same `tunnel_id`.
5. Create the app and confirm Scan Tools shows `connection_status`, `read_file`, `git`, and `logs`.
6. Copy the `plugin_asdk_app_...` id from the browser URL into `.app.json` using `app.example.json` as the shape.
7. Open a **new ordinary chat**, enable LivingRuntime Remote from the tools/plugins menu, and run:

`connection_status` → `read_file` → `git` → `logs`

Optional loopback HTTP, still not public:

```powershell
python .\plugins\livingruntime-remote\scripts\launcher.py --transport http --host 127.0.0.1 --port 8766
python .\plugins\livingruntime-remote\scripts\tunnel.py --http --port 8766
```

`mcp.http.json` documents that URL. It is not the Codex default, because Codex should keep using stdio unless you start the HTTP server yourself.

## 3. Plugin installation and publication

There are three install surfaces. Mixing them up is why the plugin can work in Codex and still be invisible in ordinary ChatGPT chat.

1. **Local/repo marketplace** — `.agents/plugins/marketplace.json`. Visible to Codex and ChatGPT Desktop Work for this repo. Not ordinary chat.
2. **Developer-mode app** — created in ChatGPT Plugins with Connection: Tunnel or a public `https://.../mcp` URL. This is the private ChatGPT ordinary-chat path, on supported plans.
3. **Public Plugins Directory** — submit through the [plugin submission portal](https://developers.openai.com/plugins/deploy/submission) with a stable public HTTPS MCP endpoint, domain verification, verified publisher identity, and the `chatgpt-app-submission.json` materials. Secure MCP Tunnel cannot be used as that public endpoint.

After OpenAI approves and you publish, the listing appears in the universal Plugins Directory shared by ChatGPT and Codex. Until then, Plus/ordinary-chat users will keep seeing approved plugins such as Remote Desktop Commander and will not see this private app.

## 4. Plan and product limitations

Verified against OpenAI Help Center as of September 2026. This repository cannot change these product rules.

| ChatGPT plan | Local stdio plugin in Codex / Desktop Work | Developer-mode custom MCP in ordinary chat | Full write/modify custom MCP | Install a public Plugins Directory listing |
| --- | --- | --- | --- | --- |
| Plus | Yes, in a supported local client with this repo | Not documented | No | Only after OpenAI approves and publishes the plugin; install still depends on plan, region, and workspace policy |
| Pro | Yes | Read/fetch custom MCP in developer mode | No; full MCP is Business/Enterprise/Edu | Same as above |
| Business / Enterprise / Edu | Yes | Yes, after an admin enables developer mode | Yes, in the documented beta | Workspace admins also control which apps/plugins members may use |

The unique external blocker for “install LivingRuntime Remote in ChatGPT ordinary chat on Plus and call write-capable tools” is **OpenAI product access**, not this SSH gateway:

- Plus is not documented as supporting private full custom MCP apps.
- Public directory listing requires OpenAI review plus a public HTTPS MCP endpoint. Tunnel is explicitly not that path.
- Upgrading the ChatGPT plan is not a code fix.

If you are on a supported workspace, the code path to verify is: tunnel-client healthy → ChatGPT app created with Connection: Tunnel → new chat → the four named tools.

## `connection_status`

The tool is named `connection_status` on both the local MCP server and the host gateway. `gateway_status` remains as a compatibility alias on the gateway only.

It returns:

- endpoint/gateway reachability and bind (stdio or loopback HTTP; `public_ingress` is always false)
- remote host identity (alias, user, hostname)
- SSH or gateway connectivity
- authenticated/authorized state
- latency_ms and last_success_unix
- configured roots and systemd units

It never returns tokens, passwords, or private keys.

## Tests

```powershell
python -m pip install mcp==1.26.0
python -m unittest discover -s plugins/livingruntime-remote/tests -v

python -m pip install "mcp==2.2.0" "uvicorn>=0.35,<1"
python -m unittest discover -s tools/remote-mcp-gateway -v
```

The discovery tests assert that MCP `tools/list` contains `connection_status`, `read_file`, `git`, and `logs` over both stdio and loopback HTTP.
