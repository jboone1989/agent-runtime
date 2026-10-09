"""Integration tests use the installed real Pi package, SQLite and killed Node workers."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.coding import BoundedCodingProvider, CodingPolicy
from agent_runtime.durable_runtime import (AgentTask, PiBridgeError, PiDurableRuntime,
    RetryableExecutionError, RuntimeBusyError, TaskStep)
from agent_runtime.durable_runtime.bridge import NODE_ROOT, encode

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (NODE_ROOT / "node_modules/@earendil-works/pi-durable/package.json").exists(),
    reason="requires Node >=22.19 and npm ci in durable_runtime/node",
)


def task(*steps, id="task-1"):
    return AgentTask(id=id, goal="test durable execution", steps=steps)


def runtime(root):
    return PiDurableRuntime(root, subject_id="test-subject")


def test_receipts_survive_restart_without_handlers_and_duplicate_commands(tmp_path):
    async def check():
        calls = []
        async def echo(payload, context):
            calls.append(context)
            return payload
        first = runtime(tmp_path)
        first.register("echo", echo)
        command = task(TaskStep(id="read", handler="echo", payload={"value": 3}))
        result = await first.run(command)
        restored = runtime(tmp_path)
        duplicate = await restored.run(command)
        assert result.state == "COMPLETED"
        assert duplicate == result == await restored.resume(command.id)
        assert len(calls) == 1
        assert result.receipts[0].result == {"value": 3}
        assert list(tmp_path.glob("*.sqlite")), "Pi must use persistent SQLite storage"
    asyncio.run(check())


def test_conflicting_command_is_rejected_and_subjects_are_isolated(tmp_path):
    async def check():
        first = runtime(tmp_path)
        await first.run(task())
        with pytest.raises(PiBridgeError, match="different immutable command"):
            await first.run(AgentTask(id="task-1", goal="changed goal", steps=()))
        other = PiDurableRuntime(tmp_path, subject_id="other-subject")
        assert await other.inspect("task-1") is None
        with pytest.raises(PiBridgeError, match="unknown task"):
            await first.resume("unknown")
    asyncio.run(check())


@pytest.mark.parametrize("safe,expected", [(False, "NEEDS_RECONCILIATION"), (True, "COMPLETED")])
def test_hard_kill_recovers_from_pi_checkpoint_without_repeating_completed_step(tmp_path, safe, expected):
    command = task(TaskStep(id="first", handler="echo"),
        TaskStep(id="second", handler="echo", replay_safe=safe, max_attempts=2 if safe else 1))
    process = subprocess.Popen([shutil.which("node"), str(NODE_ROOT / "worker.mjs"),
        str(tmp_path), "test-subject"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(encode({"action": "run", "task": command.model_dump(mode="json")}))
        process.stdin.flush()
        first = json.loads(process.stdout.readline())
        assert first["step"]["id"] == "first"
        process.stdin.write(encode({"type": "result", "id": first["id"], "ok": True, "result": {"saved": True}}))
        process.stdin.flush()
        second = json.loads(process.stdout.readline())
        assert second["step"]["id"] == "second"
        # This is an abrupt OS process termination, with a committed dispatch intent.
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
    async def check():
        restored = runtime(tmp_path)
        calls = []
        async def echo(payload, context):
            calls.append(context)
            return {"resumed": True}
        restored.register("echo", echo)
        saved = await restored.inspect(command.id)
        assert saved.state == "RUNNING"
        assert saved.receipts[0].result == {"saved": True}
        assert calls == [], "inspection must never schedule pending work"
        result = await restored.resume(command.id)
        assert result.state == expected
        assert all(call.step_id == "second" for call in calls)
        assert len(calls) == (1 if safe else 0)
        if safe:
            assert calls[0].attempt == 2
            assert calls[0].idempotency_key == second["context"]["idempotency_key"]
        else:
            assert result.receipts[-1].status == "UNCERTAIN"
            assert await restored.resume(command.id) == result
    asyncio.run(check())


def test_retry_budget_and_key_survive_transient_failures(tmp_path):
    async def check():
        first = runtime(tmp_path)
        contexts = []
        async def flaky(payload, context):
            contexts.append(context)
            if context.attempt < 3:
                raise RetryableExecutionError("temporary failure")
            return {"ok": True}
        first.register("flaky", flaky)
        result = await first.run(task(TaskStep(id="read", handler="flaky", replay_safe=True, max_attempts=3)))
        assert result.state == "COMPLETED"
        assert [row.attempt for row in result.receipts] == [1, 2, 3]
        assert len({context.idempotency_key for context in contexts}) == 1
        exhausted = await first.run(task(TaskStep(id="read", handler="flaky", replay_safe=True,
                                                  max_attempts=2), id="exhausted"))
        assert exhausted.state == "FAILED"
        assert len(exhausted.receipts) == 2
        assert await runtime(tmp_path).resume("exhausted") == exhausted
    asyncio.run(check())


@pytest.mark.parametrize("safe,expected", [(True, "TIMED_OUT"), (False, "UNCERTAIN")])
def test_deadline_preserves_unsafe_effect_uncertainty(tmp_path, safe, expected):
    async def check():
        first = runtime(tmp_path)
        async def slow(payload, context):
            await asyncio.sleep(10)
            return {}
        first.register("slow", slow)
        result = await first.run(task(TaskStep(id="slow", handler="slow", replay_safe=safe, timeout_seconds=0.02)))
        assert result.receipts[-1].status == expected
        assert result.state == ("FAILED" if safe else "NEEDS_RECONCILIATION")
    asyncio.run(check())


def test_missing_handler_is_rejected_before_effect(tmp_path):
    async def check():
        result = await runtime(tmp_path).run(task(TaskStep(id="missing", handler="missing")))
        assert result.state == "FAILED"
        assert result.receipts[0].error == "handler not registered"
    asyncio.run(check())


def test_owner_lease_and_cancellation_release_the_real_pi_process(tmp_path):
    async def check():
        owner = runtime(tmp_path)
        started = asyncio.Event()
        async def waiting(payload, context):
            started.set()
            await asyncio.Event().wait()
        owner.register("waiting", waiting)
        pending = asyncio.create_task(owner.run(task(TaskStep(id="wait", handler="waiting"))))
        await asyncio.wait_for(started.wait(), 15)
        with pytest.raises(RuntimeBusyError):
            await runtime(tmp_path).run(task(id="other"))
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        restored = runtime(tmp_path)
        result = await restored.resume("task-1")
        assert result.state == "NEEDS_RECONCILIATION"
    asyncio.run(check())


def test_python_owner_hard_kill_terminates_node_and_preserves_external_effect(tmp_path):
    script = '''
import asyncio, sys
from pathlib import Path
import agent_runtime.durable_runtime.bridge as bridge
from agent_runtime.durable_runtime import AgentTask, TaskStep, PiDurableRuntime
root = Path(sys.argv[1])
original_owner = bridge.owned_worker
def owner(pid):
    (root / 'node-pid').write_text(str(pid))
    return original_owner(pid)
bridge.owned_worker = owner
async def main():
    runtime = PiDurableRuntime(root, subject_id='test-subject')
    async def effect(payload, context):
        (root / 'external-effect').write_text('applied once')
        await asyncio.Event().wait()
    runtime.register('effect', effect)
    await runtime.run(AgentTask(id='effect', goal='external write',
        steps=(TaskStep(id='write', handler='effect'),)))
asyncio.run(main())
'''
    env = {**os.environ, "PYTHONPATH": str(NODE_ROOT.parents[2])}
    child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "external-effect").exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(child.stderr.read().decode())
            time.sleep(0.05)
        assert (tmp_path / "external-effect").exists()
        node_pid = int((tmp_path / "node-pid").read_text())
        child.kill()
        child.wait(timeout=10)
        import psutil
        deadline = time.monotonic() + 10
        def worker_alive():
            try:
                return psutil.Process(node_pid).status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return False
        while worker_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not worker_alive(), "Node worker must not outlive its capability owner"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()
    async def check():
        restored = runtime(tmp_path)
        calls = []
        async def effect(payload, context):
            calls.append(context)
            return {}
        restored.register("effect", effect)
        result = await restored.resume("effect")
        assert result.state == "NEEDS_RECONCILIATION"
        assert calls == []
        assert (tmp_path / "external-effect").read_text() == "applied once"
    asyncio.run(check())


def test_application_runs_existing_coding_capability_and_replays_its_receipt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "sample.py").write_text("value = 3\n", encoding="utf-8")
    async def check():
        app = ApplicationRuntime.bootstrap(tmp_path / "state", subject_id="ferro",
            allowed_permissions=frozenset({"workspace.code.read"}))
        app.register_provider(BoundedCodingProvider(CodingPolicy(repo_root=repo)))
        command = task(TaskStep(id="read-code", handler="code.read", payload={"path": "sample.py"}, replay_safe=True))
        result = await app.run(command)
        assert result.state == "COMPLETED"
        assert result.receipts[0].result["provider"] == "agent-runtime-bounded-coding"
        assert result.receipts[0].result["payload"]["text"] == "value = 3"
        # No application persist(), handler registration or second subject identity.
        restored = ApplicationRuntime.bootstrap(tmp_path / "state", subject_id="ferro")
        assert await restored.run(command) == result
    asyncio.run(check())


def test_application_checks_current_permissions_and_replay_policy_before_provider(tmp_path):
    calls = []
    class Provider:
        def manifest(self):
            return {"provider": "write-fixture", "capabilities": [{"capability_id": "write",
                "side_effect_level": "LOCAL_STATE", "required_permissions": ["write"]}]}
        def invoke(self, capability_id, payload):
            calls.append(payload)
            return {"ok": True}
    async def check():
        denied = ApplicationRuntime.bootstrap(tmp_path, subject_id="ferro")
        denied.register_provider(Provider())
        assert (await denied.run(task(TaskStep(id="write", handler="write")))).state == "FAILED"
        allowed = ApplicationRuntime.bootstrap(tmp_path, subject_id="ferro", allowed_permissions=frozenset({"write"}))
        allowed.register_provider(Provider())
        unsafe = await allowed.run(task(TaskStep(id="write", handler="write", replay_safe=True), id="unsafe"))
        assert unsafe.state == "FAILED"
        assert "cannot be replay-safe" in unsafe.receipts[0].error
        timeout = await allowed.run(task(TaskStep(id="write", handler="write", timeout_seconds=1.0), id="timeout"))
        assert timeout.state == "FAILED"
        assert calls == []
    asyncio.run(check())


def test_event_cursor_replays_more_than_one_page_without_running_handlers(tmp_path):
    async def check():
        first = runtime(tmp_path)
        async def echo(payload, context):
            return {}
        first.register("echo", echo)
        command = task(*(TaskStep(id=str(i), handler="echo") for i in range(55)))
        await first.run(command)
        reader = runtime(tmp_path)
        entries = await reader.events(command.id)
        assert len(entries) == 112
        cursor = entries[59]["id"]
        assert await reader.events(command.id, after=cursor) == entries[60:]
        assert len({row["id"] for row in entries}) == len(entries)
    asyncio.run(check())
