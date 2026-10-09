// One host-owned process per exchange. Pi owns SQLite, tasks, memos and receipts.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai/models";
import { createRegistry, defineDoc, defineExtension, Harness } from "@earendil-works/pi-durable";
import { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";
import { capabilityTask, entry } from "./capability-task.mjs";

const context = BACKGROUND_CONTEXT;
const [directory, subject] = process.argv.slice(2);
const lines = createInterface({ input: process.stdin });
const input = lines[Symbol.asyncIterator]();
const send = message => process.stdout.write(JSON.stringify(message) + "\n");
const Metadata = defineDoc({ kind: "agent-runtime.task-admission", version: 1,
  scope: "session", initial: () => ({ subject_id: subject, command: null, pi_task_id: null }) });

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}

async function invoke(step, execution) {
  const id = `${execution.idempotency_key}:${execution.attempt}`;
  send({ type: "invoke", id, step, context: execution });
  const line = await input.next();
  // Parent death is a process interruption, never a fabricated terminal receipt.
  if (line.done) process.exit(74);
  const response = JSON.parse(line.value);
  if (response.type !== "result" || response.id !== id) throw new Error("RPC identity mismatch");
  return response;
}

async function admit(root, task, Task) {
  return root.commit(async tx => {
    const metadata = await tx.doc(Metadata);
    if (metadata.subject_id !== subject) throw new Error("subject identity mismatch");
    if (metadata.command !== null) {
      if (JSON.stringify(canonical(metadata.command)) !== JSON.stringify(canonical(task))) {
        throw new Error("task id already has a different immutable command");
      }
      return metadata.pi_task_id;
    }
    metadata.command = task;
    metadata.pi_task_id = await tx.createTask(Task, task, { ownership: { kind: "conversation" } });
    await entry(tx, root.id, task.id, "task.admitted", { goal: task.goal });
    return metadata.pi_task_id;
  }, context);
}

function snapshot(record) {
  const outcome = record.state.outcome;
  return { task: record.input, pi_task_id: record.id,
    state: outcome?.result?.state ?? (outcome ? "FAILED" : "RUNNING"),
    checkpoint: record.state.checkpoint ?? null,
    receipts: outcome?.result?.receipts ?? record.state.checkpoint?.receipts ?? [],
    error: outcome?.error?.message ?? outcome?.result?.error ?? null };
}

async function events(root, after) {
  const rows = [];
  let cursor;
  do {
    const page = await root.entries({ order: "ascending", minEntryId: after + 1 }, 100, cursor, context);
    for (const record of page.items) {
      if (record.kind === "runtime.execution-event") rows.push({ id: record.id, ...record.data });
    }
    cursor = page.next;
  } while (cursor !== undefined);
  return rows;
}

async function handle(command) {
  const taskId = command.task?.id ?? command.task_id;
  if (typeof taskId !== "string" || !taskId) throw new Error("task id required");
  const key = createHash("sha256").update(JSON.stringify([subject, taskId])).digest("hex");
  const path = join(directory, `${key}.sqlite`);
  if (!existsSync(path) && command.action !== "run") {
    if (command.action === "inspect") return null;
    if (command.action === "events") return [];
    throw new Error("unknown task");
  }
  mkdirSync(directory, { recursive: true });
  const Task = capabilityTask(subject, invoke);
  const registry = createRegistry();
  registry.install(defineExtension({ name: "agent-runtime", tasks: [Task] }));
  // No model/provider is configured or called: Ferro supplies each step decision.
  const harness = await Harness.open(await openNodeSqliteStorage(path),
    { models: createModels(), registry }, context);
  try {
    const root = await harness.root(context);
    if (command.action === "events") return await events(root, command.after);
    const metadata = await harness.snapshot(Metadata, context);
    const id = command.action === "run" ? await admit(root, command.task, Task) : metadata?.pi_task_id;
    if (id == null) throw new Error("task admission missing");
    if (command.action === "inspect") return snapshot(await harness.getTask(id, context));
    if (!["run", "resume"].includes(command.action)) throw new Error("unknown command");
    return snapshot(await harness.waitForTask(id, context));
  } finally {
    await harness.close(context);
  }
}

try {
  const first = await input.next();
  if (first.done) throw new Error("command required");
  send({ type: "done", result: await handle(JSON.parse(first.value)) });
} catch (error) {
  send({ type: "error", error: error.message });
} finally {
  lines.close();
}
