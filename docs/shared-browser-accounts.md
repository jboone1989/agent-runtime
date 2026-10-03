# Shared browser accounts

Agent Runtime owns one Chrome process, desktop and noVNC server per state root.
Sites reuse separate tabs in that browser. Add accounts through
`BrowserHarnessProvider.for_account(account_id)`, or supply `account_id` in
a browser capability payload. Every non-default account has its own CDP browser
context and target bindings. Cookie, LocalStorage and IndexedDB checkpoints are
private files under `browser-harness/accounts/`; they restore into the same
shared endpoint after Chrome restarts.

Always configure an explicit `cdp_url` or `cdp_ws`. The adapter does not launch
a browser. Keep a stable account ID and endpoint across restarts.
`default` denotes Chrome's persistent profile, used by the migrated first
account and different sites whose cookies do not overlap.

## Ferro

Add a registered X identity through the existing OAuth flow, then select
“打开账号浏览器” on that account card to log into its independent web session.
The selected registry ID is sent to connect/status/control endpoints; an
authenticated identity must match that registry entry.

Programmatic routing:
```python
router = XBrowserTransportRouter(state_root=state_root, account_id=account_id)
identity = await router.identity()
```
A registry entry whose `browser_profile_id` is `default` retains the migrated
primary account. Other entries use their stable registry ID as context ID.
`AGENT_RUNTIME_X_ACCOUNT_ID` and `AGENT_RUNTIME_CHATGPT_ACCOUNT_ID` configure
the context for default service adapters.

## Supervision and migration

Run only the existing owner supervisor. Its systemd unit must use
`KillMode=process`: restarting that supervisor should leave Chrome, the desktop
and noVNC alive so the replacement adopts their existing state.
Explicit browser.profile.stop stops the shared browser stack.

If two legacy Chrome processes are found, startup refuses to create another.
Use `scripts/migrate_shared_browser.py` to snapshot, import, verify and cut over
disjoint-site sessions. It backs up secrets on the host with restricted
permissions, checks cookie/origin conflicts, verifies both identities and retires
only the source stack. Same-site sessions require separate account contexts.

Validate isolation and restart recovery against a disposable Chrome with
`scripts/shared_browser_acceptance.py CHROME_PATH HARNESS_PATH`.
