"""Python capability RPC client. All checkpoints and scheduling belong to Pi."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .lease import execution_lease
from .process_owner import owned_worker
from .protocol import TaskHandler
from .models import (AgentTask, ExecutionContext, ExecutionRejectedError, PiBridgeError,
                     RetryableExecutionError, TaskSnapshot)

NODE_ROOT = Path(__file__).with_name("node")


def encode(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


class PiDurableRuntime:
    def __init__(self, state_dir: str | Path, *, subject_id: str, node: str = "node"):
        if not subject_id.strip():
            raise ValueError("subject_id must be non-empty")
        self.state_dir = Path(state_dir).resolve()
        self.subject_id = subject_id
        self.node = node
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, name: str, handler: TaskHandler) -> None:
        if not name.strip() or name in self._handlers:
            raise ValueError("handler name is empty or already registered")
        self._handlers[name] = handler

    async def run(self, task: AgentTask) -> TaskSnapshot:
        result = await self._exchange({"action": "run", "task": task.model_dump(mode="json")})
        return TaskSnapshot.model_validate(result)

    async def resume(self, task_id: str) -> TaskSnapshot:
        return TaskSnapshot.model_validate(await self._exchange({"action": "resume", "task_id": task_id}))

    async def inspect(self, task_id: str) -> TaskSnapshot | None:
        result = await self._exchange({"action": "inspect", "task_id": task_id})
        return TaskSnapshot.model_validate(result) if result is not None else None

    async def events(self, task_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        """Replay application entries from Pi storage; does not run pending work."""
        return await self._exchange({"action": "events", "task_id": task_id, "after": after})

    async def _exchange(self, command):
        if not (NODE_ROOT / "node_modules/@earendil-works/pi-durable/package.json").exists():
            raise PiBridgeError(f"install Pi Durable dependencies: npm ci --prefix {NODE_ROOT}")
        with execution_lease(self.state_dir) as lease_fd, tempfile.TemporaryFile() as errors:
            ownership = {"pass_fds": (lease_fd,)} if os.name != "nt" else {}
            process = await asyncio.create_subprocess_exec(
                self.node, str(NODE_ROOT / "worker.mjs"), str(self.state_dir), self.subject_id,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=errors,
                limit=4 * 1024 * 1024,
                **ownership,
            )
            try:
                with owned_worker(process.pid):
                    process.stdin.write(encode(command))
                    await process.stdin.drain()
                    return await self._read(process, errors)
            finally:
                # Kill on cancellation too. Pi retains the dispatched intent for recovery.
                if process.returncode is None:
                    process.kill()
                await process.wait()

    async def _read(self, process, errors):
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message["type"] == "invoke":
                response = await self._invoke(message)
                process.stdin.write(encode({"type": "result", "id": message["id"], **response}))
                await process.stdin.drain()
            elif message["type"] == "done":
                return message["result"]
            elif message["type"] == "error":
                raise PiBridgeError(message["error"])
            else:
                raise PiBridgeError("unrecognized Pi bridge response")
        errors.seek(0)
        detail = errors.read()[-4096:].decode("utf-8", errors="replace")
        raise PiBridgeError(f"Pi bridge exited without a result: {detail}")

    async def _invoke(self, message):
        handler = self._handlers.get(message["step"]["handler"])
        if handler is None:
            return {"ok": False, "error": "handler not registered", "effect_started": False}
        try:
            call = handler(message["step"]["payload"], ExecutionContext.model_validate(message["context"]))
            timeout = message["step"]["timeout_seconds"]
            result = await call if timeout is None else await asyncio.wait_for(call, timeout)
            response = {"ok": True, "result": dict(result)}
            encode(response)  # Reject non-JSON results before the protocol write.
            return response
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "effect_started": not isinstance(exc, ExecutionRejectedError),
                    "retryable": isinstance(exc, (RetryableExecutionError, TimeoutError)),
                    "timed_out": isinstance(exc, TimeoutError)}
