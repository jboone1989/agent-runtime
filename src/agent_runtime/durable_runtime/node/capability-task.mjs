import { createHash } from "node:crypto";
import { defineTask } from "@earendil-works/pi-durable";

const hash = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");

function executionContext(task, subject) {
  const cp = task.state.checkpoint;
  const step = task.input.steps[cp.index];
  return { task_id: task.input.id, step_id: step.id, attempt: cp.attempt,
    idempotency_key: hash([subject, task.input.id, step.id]),
    replay_safe: step.replay_safe, timeout_seconds: step.timeout_seconds };
}

function receipt(task, subject, status, extra = {}) {
  const context = executionContext(task, subject);
  return { id: `${context.idempotency_key}:${context.attempt}:${status}`,
    task_id: context.task_id, step_id: context.step_id, attempt: context.attempt,
    status, result: {}, error: null, ...extra };
}

async function entry(tx, conversationId, taskId, kind, payload) {
  await tx.appendEntry(conversationId, { kind: "runtime.execution-event",
    data: { version: 1, task_id: taskId, kind, payload, timestamp: new Date().toISOString() } });
}

async function finish(task, runtime, context, record, retry = false) {
  const cp = task.state.checkpoint;
  const receipts = [...cp.receipts, record];
  const next = record.status === "SUCCEEDED" ? cp.index + 1 : cp.index;
  await runtime.commit(async tx => {
    await entry(tx, runtime.conversationId, task.input.id, "step.receipted", record);
    if (retry) {
      await entry(tx, runtime.conversationId, task.input.id, "step.started",
        { step_id: record.step_id, attempt: cp.attempt + 1 });
      return { status: "running", checkpoint: { ...cp, attempt: cp.attempt + 1, receipts } };
    }
    if (record.status === "SUCCEEDED" && next < task.input.steps.length) {
      return { status: "running", checkpoint: { phase: "prepare", index: next, attempt: 0, receipts } };
    }
    const state = record.status === "SUCCEEDED" ? "COMPLETED"
      : record.status === "UNCERTAIN" ? "NEEDS_RECONCILIATION" : "FAILED";
    const result = { state, receipts, error: record.error };
    await entry(tx, runtime.conversationId, task.input.id, "task.settled", { state });
    return { status: "terminal", outcome: state === "COMPLETED"
      ? { status: "completed", result }
      : { status: "failed", error: { message: record.error ?? state }, result } };
  }, context);
}

async function execute(task, runtime, context, subject, invoke) {
  const cp = task.state.checkpoint;
  const step = task.input.steps[cp.index];
  const memo = `dispatched:${cp.index}:${cp.attempt}`;
  if (await runtime.memo(memo, context)) {
    const status = step.replay_safe ? "FAILED" : "UNCERTAIN";
    await finish(task, runtime, context,
      receipt(task, subject, status, { error: "process interrupted before result commit" }),
      step.replay_safe && cp.attempt < step.max_attempts);
    return;
  }
  // Pi stores dispatch intent before any RPC can cause a Python side effect.
  await runtime.memo(memo, true, context);
  const response = await invoke(step, executionContext(task, subject));
  const status = response.ok ? "SUCCEEDED"
    : !step.replay_safe && response.effect_started !== false ? "UNCERTAIN"
    : response.timed_out ? "TIMED_OUT" : "FAILED";
  await finish(task, runtime, context, receipt(task, subject, status,
    { result: response.result ?? {}, error: response.error ?? null }),
    !response.ok && response.retryable === true && step.replay_safe && cp.attempt < step.max_attempts);
}

export function capabilityTask(subject, invoke) {
  return defineTask({
    name: "agent-runtime.capability-task", version: 1,
    initial: () => ({ phase: "prepare", index: 0, attempt: 0, receipts: [] }),
    phases: {
      prepare: async (task, runtime, context) => {
        const cp = task.state.checkpoint;
        await runtime.commit(async tx => {
          if (cp.index === task.input.steps.length) {
            await entry(tx, runtime.conversationId, task.input.id, "task.settled", { state: "COMPLETED" });
            return { status: "terminal", outcome: { status: "completed",
              result: { state: "COMPLETED", receipts: cp.receipts, error: null } } };
          }
          await entry(tx, runtime.conversationId, task.input.id, "step.started",
            { step_id: task.input.steps[cp.index].id, attempt: 1 });
          return { status: "running", checkpoint: { ...cp, phase: "execute", attempt: 1 } };
        }, context);
      },
      execute: (task, runtime, context) => execute(task, runtime, context, subject, invoke),
    },
    abort: async (task, runtime, context) => {
      await runtime.commit(() => ({ status: "terminal", outcome: { status: "aborted",
        result: { state: "ABORTED", receipts: task.state.checkpoint.receipts, error: null } } }), context);
    },
  });
}

export { entry };
