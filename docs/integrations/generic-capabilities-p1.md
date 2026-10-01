# Generic capability integrations — P1

This batch extends the Runtime-owned capability fabric without adding a second agent loop or registry. External libraries remain implementation providers; Runtime keeps capability IDs, authority, budgets, evidence and lifecycle semantics.

## Filesystem events

`observation.filesystem_events` is an `ObservationExtension` backed by watchdog.

- Roots are configured at provider construction and pinned through the same Runtime root boundary used by other observations.
- Supported event classes are created, modified, deleted and moved.
- Queue length, events per poll, event types, recursion and debounce/coalescing are bounded by `FilesystemEventPolicy`.
- Symlink/reparse traversal outside a bound root is rejected.
- Overflow is reported as evidence rather than silently hidden.
- Restart semantics are explicit: events are observed only after the current provider instance starts; this is not presented as a durable event log.

Install with `agent-runtime[filesystem-events]`.

## Browser / Computer Use

Agent Runtime exposes one browser capability contract:

- `browser.navigate`
- `browser.inspect`
- `browser.screenshot`
- `browser.click`
- `browser.type`

Navigation, inspection and screenshots are classified READ_ONLY. Click and type are classified EXTERNAL_SIDE_EFFECT and require `browser.interact` permission.

Runtime bounds sessions, timeouts, text, interactive element count, typed characters, screenshots and allowed/blocked hosts. HTTP(S) targets are validated before navigation and on the final page URL. Hostnames are resolved and non-global destinations are rejected to reduce DNS-rebinding/SSRF risk.

There are two implementations of the contract:

- `BrowserHarnessProvider` is the preferred persistent Computer Use backend. It delegates CDP connection lifecycle, real browser interaction primitives and browser-process continuity to the open-source Browser Harness project. Each Runtime `session_id` is durably bound to an exact Browser Harness/CDP target. Runtime shutdown does not stop Browser Harness or Chrome, so authenticated sessions survive Runtime restarts.
- `PlaywrightBrowserProvider` remains the bounded ephemeral backend for tests and isolated jobs. Its browser contexts are process-local and are destroyed when the provider closes.

This split is deliberate: Agent Runtime owns capability IDs, permissions, host policy, durable session identity and evidence, but does not reimplement screencasting, mouse/keyboard dispatch, tab mechanics or other Computer Use primitives already supplied by Browser Harness/CDP.

Install the ephemeral Playwright backend with `agent-runtime[browser]`. Browser Harness is intentionally kept in its own environment and consumed through its executable/daemon contract; persistent-browser deployments should instantiate `BrowserHarnessProvider` and set `AGENT_RUNTIME_BROWSER_HARNESS` (or `BrowserHarnessRuntime.executable`) to that executable. Existing logged-in Chrome instances can be attached explicitly with `BrowserHarnessRuntime.cdp_url` or `cdp_ws`.

## Database observation

`observation.database_read` is an `ObservationExtension` implemented with SQLAlchemy Core. The model/request boundary never accepts raw SQL.

Each `DatabaseSource` binds a connection URL, table allowlist, optional schema and an explicit read-only declaration. Requests may perform only:

- `tables` — allowlist-filtered table discovery
- `columns` — bounded column metadata for one allowed table
- `select` — structured columns, filters, ordering and row limit

Rows, columns, filters, output bytes and execution time are bounded. SQLite enables query-only mode and a progress-handler timeout. PostgreSQL begins a read-only transaction and sets a local statement timeout. Other dialects fail closed until Runtime has a concrete read-only/timeout policy for them.

Connection URLs are provider configuration and are not returned in result/evidence payloads. Production credentials should be supplied through the Runtime secret resolver or an equivalent deployment secret mechanism rather than model-visible task data.

Install with `agent-runtime[database]`.

## Local durable scheduling

The APScheduler provider exposes:

- `task.schedule`
- `task.cancel`
- `task.list`

Scheduling and cancellation are LOCAL_STATE operations and require `task.manage`. Listing is READ_ONLY.

The provider does not execute arbitrary Python function paths. A schedule stores a bounded `target_capability`, JSON payload and date/interval/cron trigger. When a job fires, an injected dispatch callback should call `ApplicationRuntime.invoke`, so the target capability is checked again against the Runtime side-effect ceiling, permissions and invocation receipt path at execution time.

Schedule definitions are stored in an atomic local JSON state file. Recurring schedules are restored after restart. Expired one-shot date schedules are dropped rather than replayed late. `task.list` exposes a payload hash instead of echoing the stored payload. Deployments should keep raw credentials out of scheduled payloads and pass opaque secret references instead.

Install with `agent-runtime[scheduler]`.

## CI and deployment

The full regression workflows install the P1 optional extras so dependency resolution and provider imports are exercised on clean Python 3.11 and 3.12 runners. Browser binaries are intentionally not downloaded in normal CI; browser executable/OS dependency installation remains a deployment concern.
