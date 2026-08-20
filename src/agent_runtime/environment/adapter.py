"""Shared channel path helpers for adapters."""
from __future__ import annotations

from typing import Any


def walk(root: dict[str, Any], channel: str) -> Any:
    if not channel:
        return root
    cur: Any = root
    for part in channel.split("/"):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def assign(root: dict[str, Any], channel: str, value: Any) -> None:
    parts = [p for p in channel.split("/") if p]
    if not parts:
        return
    cur: Any = root
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
