"""Process-safe durable storage for independent wake plans.

Each wake owns one file. Different workers therefore never rewrite a shared
schedule document just to move their own deadline.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .models import PlannedWake


class FilePlanStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _name(wake_id: str) -> str:
        import hashlib
        return hashlib.sha256(str(wake_id).encode("utf-8")).hexdigest() + ".json"

    def _path(self, wake_id: str) -> Path:
        return self.root / self._name(wake_id)

    @staticmethod
    def _payload(wake: PlannedWake) -> dict:
        return {
            "wake_id": wake.wake_id,
            "at": wake.at.astimezone(timezone.utc).isoformat(),
            "reason": wake.reason,
            "task": wake.task,
            "relevant_refs": list(wake.relevant_refs),
            "cancel_conditions": list(wake.cancel_conditions),
            "preemptible": bool(wake.preemptible),
        }

    @staticmethod
    def _wake(payload: dict) -> PlannedWake:
        at = datetime.fromisoformat(str(payload["at"]).replace("Z", "+00:00"))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("wake plan timestamp must be timezone-aware")
        return PlannedWake(
            wake_id=str(payload["wake_id"]),
            at=at.astimezone(timezone.utc),
            reason=str(payload["reason"]),
            task=str(payload["task"]),
            relevant_refs=tuple(payload.get("relevant_refs") or ()),
            cancel_conditions=tuple(payload.get("cancel_conditions") or ()),
            preemptible=bool(payload.get("preemptible", True)),
        )

    def schedule(self, wake: PlannedWake) -> None:
        path = self._path(wake.wake_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._payload(wake), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def cancel(self, wake_id: str) -> bool:
        path = self._path(wake_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def get(self, wake_id: str) -> PlannedWake | None:
        path = self._path(wake_id)
        try:
            return self._wake(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None

    def list(self) -> tuple[PlannedWake, ...]:
        rows: list[PlannedWake] = []
        for path in self.root.glob("*.json"):
            try:
                rows.append(self._wake(json.loads(path.read_text(encoding="utf-8"))))
            except FileNotFoundError:
                continue
        return tuple(sorted(rows, key=lambda row: (row.at, row.wake_id)))


__all__ = ["FilePlanStore"]
