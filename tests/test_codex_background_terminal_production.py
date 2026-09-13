"""Opt-in real app-server acceptance for residual Codex terminal cleanup."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile

import pytest

from agent_runtime.backends.codex import (
    _clean_background_terminals,
    _load_codex_sdk,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("CODEX_PRODUCTION_ACCEPTANCE") != "1",
    reason="opt-in real Codex/app-server acceptance",
)


async def _raw_thread_request(thread, method: str, params: dict):
    client = thread._codex._client
    request_raw = getattr(client, "_request_raw", None)
    if callable(request_raw):
        result = request_raw(method, params)
        return await result if hasattr(result, "__await__") else result
    request_raw = client._sync._request_raw
    return await asyncio.to_thread(request_raw, method, params)


async def _list_terminals(thread) -> list[dict]:
    result = await _raw_thread_request(
        thread,
        "thread/backgroundTerminals/list",
        {"threadId": str(thread.id)},
    )
    assert isinstance(result, dict)
    data = result.get("data")
    assert isinstance(data, list)
    return data


def test_real_app_server_cleanup_removes_observed_background_terminal() -> None:
    asyncio.run(_test_real_app_server_cleanup_removes_observed_background_terminal())


async def _test_real_app_server_cleanup_removes_observed_background_terminal() -> None:
    sdk = _load_codex_sdk()
    workspace = Path(tempfile.mkdtemp(prefix="codex-terminal-acceptance-"))
    thread = None
    try:
        async with sdk.AsyncCodex() as codex:
            thread = await codex.thread_start(
                sandbox=sdk.Sandbox.read_only,
                cwd=str(workspace),
            )
            await _raw_thread_request(
                thread,
                "thread/shellCommand",
                {
                    "threadId": str(thread.id),
                    "command": "python -c \"import time; time.sleep(120)\"",
                    "timeoutMs": 120000,
                },
            )

            observed: list[dict] = []
            for _ in range(20):
                observed = await _list_terminals(thread)
                if observed:
                    break
                await asyncio.sleep(0.25)
            if not observed:
                pytest.skip("INCONCLUSIVE: app-server did not expose a background terminal")

            before = await _clean_background_terminals(thread)
            assert before >= 1
            assert await _list_terminals(thread) == []
            await asyncio.sleep(1.0)
            assert await _list_terminals(thread) == []
    finally:
        if thread is not None:
            try:
                await _clean_background_terminals(thread)
            except Exception:
                pass
