"""Bounded process entry point for a host-configured independent memory worker.

The factory is deployment configuration, never an Agent-provided tool argument.
It supplies explicit providers/models; this runner adds no replacement providers.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import signal
import time


async def run_worker(worker, *, receipt_path, interval=5.0, max_cycles=None):
    if interval <= 0 or (max_cycles is not None and (type(max_cycles) is not int or max_cycles <= 0)):
        raise ValueError("worker interval and cycle limit must be positive")
    path = Path(receipt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    count = 0
    try:
        while not stopped.is_set() and (max_cycles is None or count < max_cycles):
            started = time.time()
            try:
                progress = await worker.run_once()
                result = {"status": "DONE", "progress": progress}
            except Exception as exc:
                # A later scheduled cycle retries saved artifacts. No external
                # action is invoked by this worker and no cursor is reset.
                result = {"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"started_at": started, "finished_at": time.time(),
                                         "pid": os.getpid(), **result}) + "\n")
                handle.flush()
            count += 1
            if max_cycles is None or count < max_cycles:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval)
                except TimeoutError:
                    pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--factory", required=True, help="trusted deployment module:function")
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    module, function = args.factory.split(":", 1)
    factory = getattr(importlib.import_module(module), function)
    asyncio.run(run_worker(factory(), receipt_path=args.receipts,
                           interval=args.interval, max_cycles=args.max_cycles))


if __name__ == "__main__":
    main()
