"""Bounded snapshot retention backed by append-only diagnostic history."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..codec import encode


@dataclass(frozen=True)
class HistoryRetention:
    per_cycle: int = 128
    actions: int = 256
    self_cognition: int = 128
    capability_usage: int = 256
    episodic: int = 256
    learning: int = 512


class RuntimeHistoryArchive:
    """Archive overflow records once, allowing the live snapshot to stay bounded."""

    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "archive" / "runtime"
        self._known: set[str] | None = None

    def trim(self, stream: str, values: list[Any], *, retain: int) -> list[Any]:
        if retain < 0:
            raise ValueError("history retention must be non-negative")
        overflow = len(values) - retain
        if overflow <= 0:
            return values
        old = values[:overflow]
        for item in old:
            self.append_once(stream, item)
        return values[overflow:]

    def append_once(self, stream: str, item: Any) -> str:
        encoded = encode(item)
        canonical = json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record_ref = "hist_" + hashlib.sha256(
            f"{stream}\n{canonical}".encode("utf-8")
        ).hexdigest()[:24]
        known = self._known_refs()
        if record_ref in known:
            return record_ref
        now = datetime.now(timezone.utc)
        path = self.root / f"runtime-history-{now.strftime('%Y-%m')}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "record_ref": record_ref,
            "stream": stream,
            "archived_at": now.isoformat(),
            "record": encoded,
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
        known.add(record_ref)
        return record_ref

    def _known_refs(self) -> set[str]:
        if self._known is not None:
            return self._known
        known: set[str] = set()
        if self.root.exists():
            for path in sorted(self.root.glob("runtime-history-*.jsonl")):
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            try:
                                value = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            record_ref = str(value.get("record_ref") or "")
                            if record_ref:
                                known.add(record_ref)
                except OSError:
                    continue
        self._known = known
        return known
