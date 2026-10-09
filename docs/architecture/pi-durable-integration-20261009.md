# Pi Durable integration: first execution slice

Baseline: `agent-runtime` commit `41062a0`. This change uses the actual
`@earendil-works/pi-durable` npm package, pinned with Pi AI and Chord to **1.1.0**.
It replaces the abandoned local durable engine prototype.

## Ownership

```text
Ferro / application decides an AgentTask
    -> ApplicationRuntime.run(task)
    -> Python PiDurableRuntime client
    -> Node Pi Harness / custom capability task
    -> Python authorized capability handler
    -> Pi atomic commit: checkpoint + application event + receipt
    -> application observes outcome and learns
```

`ApplicationRuntime` depends on the public `TaskExecutor` protocol. Applications
can inject another implementation through `task_executor=`; its subject identity
is checked before registration. Pi is the lazy default, so ordinary application
startup and single capability invocations do not load the bridge or require Node.

Pi owns task scheduling, checkpoints, dispatch memos, persisted task outcomes,
and SQLite commits. Python owns capability registration, current permissions,
transport, and the host process lease. No Python execution-state database or
checkpoint engine is retained. No model provider, API key, or model call is
required for this integration. The application supplies its decisions explicitly.

The custom Pi task has `prepare` and `execute` phases. A dispatch memo is committed
by Pi before a callback can cause an effect. The callback's execution receipt,
application event and next checkpoint/final outcome are written in one Pi commit.
This is a Pi task definition, not a second scheduler.

## What the code inspection found

| Responsibility | Current owner | First-slice change |
| --- | --- | --- |
| `AgentRuntime.run()` | `runtime.py:4737` | A short tick loop; preserved |
| Mixed cognition/action/learning | `AgentRuntime.step()` at `runtime.py:4769` | Preserved for staged migration |
| Full agent snapshot | `AgentRuntime.persist()` at `runtime.py:1930` | Preserved; distinct from task progress |
| Supervision/retry/checkpoint cadence | `service.py:155` | Preserved; not yet routed through Pi |
| Invocation receipts/application cycle | `application.py` | New long-task path uses authoritative Pi receipts |
| Participant checkpoint envelopes | `lifecycle/checkpoint.py` | Preserved for identity/domain snapshots |
| Retrieval continuation | `memory/session.py` | Domain contract retained; not yet migrated |
| Backend deadline and verification | `execution/gateway.py` | Preserved; not bypassed by the bridge |
| Browser pending submissions/recovery | `llm/chatgpt_browser.py` | Preserved; requires its own provider receipt adapter |
| Backend events | `backends/model.py` | Existing contract preserved |
| Network task/verification receipts | `capabilities/network/models.py` | Existing contract preserved |

## Install and run

Requirements: Python >=3.11 and Node >=22.19.0. Development verification used
Python 3.11.9 and Node 24.15.0 on Windows.

```powershell
python -m pip install -e ".[dev]"
npm ci --prefix src/agent_runtime/durable_runtime/node --registry=https://registry.npmjs.org --ignore-scripts
python examples/pi_durable_capability_task.py --repo . --path README.md --state ../pi-demo --task-id read-readme-1
```

Run the same command twice: the second run returns the committed task receipt,
without invoking the capability again. If installed from a wheel, use
`npm ci --prefix <installed agent_runtime/durable_runtime/node directory>`.
No automatic dependency install occurs when a task runs.

```python
from agent_runtime import ApplicationRuntime
from agent_runtime.durable_runtime import AgentTask, TaskStep

task = AgentTask(
    id="research-20261009-1",
    goal="Read the source before making a decision",
    steps=(TaskStep(id="read", handler="code.read",
                    payload={"path": "README.md"}, replay_safe=True,
                    max_attempts=2),),
)
# Register a capability provider, with the corresponding permission, first.
snapshot = await application.run(task)
snapshot = await application.durable.resume(task.id)
events = await application.durable.events(task.id, after=last_seen_entry_id)
```

Each `(subject_id, task.id)` has its own Pi SQLite database, named by a hash.
This isolates task schedulers: reopening one task cannot execute another task.
Reusing an id with a different goal, steps or payload is rejected. Task snapshots
and events can be inspected without scheduling work, and completed receipts can
be replayed without registering a provider.

The default local executor serializes ownership of a subject's storage during an
exchange. It is not a shared, always-on multi-agent Node service. Lifecycle wake-up
and broader workflow migration remain the supervisor/application's responsibility.

## Recovery and retry contract

- Completed steps are skipped after restart.
- Interrupted replay-safe steps can retry while their persisted attempt budget
  remains. The idempotency key stays stable across attempts; attempts consume
  budget even when a crash occurs before a result commit.
- A step with an external/state-changing effect defaults to unsafe. Interruption
  after dispatch returns `NEEDS_RECONCILIATION`; it does not automatically repeat
  the effect. An unsafe handler exception or deadline also preserves uncertainty.
- Explicit transient errors (`RetryableExecutionError`) and timeouts retry only
  replay-safe handlers. Other failures are terminal. Known rejection before any
  effect (`ExecutionRejectedError`) records a failure rather than uncertainty.
- The application provider adapter rechecks current permissions and refuses to
  mark state-changing capabilities replay-safe. Its synchronous provider contract
  has no cancellable deadline, so duration settings are rejected before invocation.
  Native async handlers registered on `PiDurableRuntime` support cooperative
  deadlines. A coroutine that suppresses cancellation can exceed its deadline.
- Windows workers belong to a Job Object that kills them if their Python owner
  dies. POSIX workers inherit the execution lease and exit on parent stdin EOF.
  The host lease prevents concurrent openers; Pi itself supplies no process lock.

The receipt records **invocation completion**, not independently verified goal
success. Existing gateway verification must remain authoritative when its path
is migrated. Providers must return persistable, appropriately redacted payloads;
this bridge does not add a universal credential-redaction layer.

## Event replay and limits

`events()` replays durable application entries with Pi entry ids as a cursor,
including admission, step intent, receipt and settlement. It paginates Pi's
history without calling `Harness.resume()` or `waitForTask()`. This supplements
Pi's snapshot-based live watch; it is not a general pub/sub Event Bus.

An uncertain task ends with a Pi failed outcome and a domain result of
`NEEDS_RECONCILIATION`. Resubmitting it preserves that result. Automated read-back,
operator reconciliation and continuation after reconciliation are **not** implemented
in this first slice: the caller must verify the external effect and explicitly
submit a new task for remaining work. Exactly-once external effects require
provider idempotency or authoritative read-back; checkpointing alone cannot guarantee them.

The Ferro
decision/learning loop, browser deployment and full service lifecycle have not
been migrated. The existing shared browser is untouched. The next migration
should select one real long-running workflow and adapt its outcome/verification
contract before removing its old execution-state ownership.

## Verification

The integration suite executes the real pinned npm package, with no model calls.
It covers abrupt Node termination, abrupt Python-owner termination after an
external write, completed-step skipping, bounded retry, stable idempotency keys,
duplicate/conflicting commands, subject isolation, owner contention, cancellation,
deadlines, permission/replay-policy rejection, existing `code.read` capability
execution, and event pagination/cursor replay. See `tests/test_pi_durable_runtime.py`.

```powershell
python -m pytest -q tests/test_pi_durable_runtime.py tests/test_application_api.py tests/test_web_application_runtime.py tests/test_runtime_execution_gateway.py tests/test_platform_boundaries.py tests/test_continuous_service.py
$env:PYTHONPATH="src"
lint-imports --config .importlinter
```

The integration tests skip if Node/npm dependencies are unavailable; install
them before using their results as durability evidence. No architecture policy
or CI boundary was relaxed.

Upstream references:
- https://github.com/earendil-works/pi/blob/main/packages/durable/README.md
- https://github.com/earendil-works/pi/blob/main/packages/durable/test/examples/12-tasks.ts
- https://github.com/earendil-works/pi/blob/main/packages/durable/test/examples/13-recovery.ts
