# Project instructions

## Persistent browser accounts

User requirement recorded on 2026-10-03: all persistent accounts on a host must use one shared browser instance.

- Agent Runtime owns the shared browser lifecycle; Ferro and other consumers attach to it.
- Adding an account, website, agent session, or task must not launch another persistent Chrome/browser instance, desktop, or takeover server.
- Accounts on different websites (for example ChatGPT and X) may share a persistent browser profile and use separate, reusable tabs.
- Multiple accounts on the same website must retain separate login state inside the shared browser, using supported contexts/profiles or the site's account switching. Verify the active account before account-specific work.
- Persist login state across runtime/browser restarts. Do not substitute temporary contexts for durable account storage.
- Reuse durable task tabs and reclaim task-created blank/finished tabs without closing active conversations or operator tabs.
- Existing per-account browsers are legacy deployment state. Preserve authenticated sessions and verify account identity during migration; do not report a one-browser migration as complete until the running processes and bindings demonstrate it.

## Engineering quality and module ownership

- Before changing code, identify the owning module, its public interfaces, and the affected consumers.
- Prefer small changes in the responsible module; do not add coordination, external I/O, and business logic into one `run()` method.
- Keep most functions under 50 lines; review functions over 100 lines for mixed responsibilities. Do not split merely to hit a line limit.
- Use public contracts for synchronous operations; use events when work should be independent, not as a default for every call.
- Avoid new cross-module mutable state, duplicate compatibility paths, and new circular dependencies.
- Respect `.agent-engineering/policy.json` boundaries. Do not modify architecture rules or their CI solely to make a feature pass.
- Run existing behavior tests plus `python .agent-engineering/guard.py check` before claiming completion.
- Before claiming completion, report tests actually executed, architectural impact, and unresolved failures. Passing behavior tests alone does not establish good architecture.
- Changes to architecture policy or CI require independent review; do not silently relax a boundary. A loosening is recorded only via `aegkit relax` (append-only relaxations audit); policy edits must be isolated PRs.
