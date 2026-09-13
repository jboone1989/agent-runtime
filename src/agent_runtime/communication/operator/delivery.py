"""Durable delivery bookkeeping. Restart must not resend a delivered item."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ...clock import Clock, SystemClock, new_id
from .models import (
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_RETRYING,
    DeliveryRecord,
)
from ._persist import atomic_json, read_json, utc_now


class DeliveryStore:
    def __init__(self, state_dir: Path, *, clock: Clock | None = None) -> None:
        self.root = Path(state_dir) / "operator" / "deliveries"
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()

    def get(
        self,
        source_type: str,
        source_id: str,
        channel: str,
    ) -> DeliveryRecord | None:
        path = self._path(source_type, source_id, channel)
        try:
            return _record_from_json(read_json(path))
        except FileNotFoundError:
            return None

    def get_or_create(
        self,
        *,
        source_type: str,
        source_id: str,
        channel: str,
    ) -> DeliveryRecord:
        existing = self.get(source_type, source_id, channel)
        if existing is not None:
            return existing
        record = DeliveryRecord(
            delivery_id=new_id("delivery"),
            source_type=source_type,
            source_id=source_id,
            channel=channel,
            status=DELIVERY_PENDING,
            attempts=0,
            created_at=_stamp(self.clock),
        )
        self._write(record)
        return record

    def undelivered(self) -> list[DeliveryRecord]:
        found: list[DeliveryRecord] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                record = _record_from_json(read_json(path))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if record.status != DELIVERY_DELIVERED:
                found.append(record)
        return found

    def begin_attempt(self, record: DeliveryRecord) -> DeliveryRecord:
        status = DELIVERY_RETRYING if record.attempts > 0 else record.status
        if record.status == DELIVERY_FAILED:
            status = DELIVERY_RETRYING
        updated = DeliveryRecord(
            **{
                **asdict(record),
                "status": status,
                "last_attempt_at": _stamp(self.clock),
            }
        )
        self._write(updated)
        return updated

    def record_result(
        self,
        record: DeliveryRecord,
        *,
        ok: bool,
        error: str | None = None,
    ) -> DeliveryRecord:
        now = _stamp(self.clock)
        if ok:
            updated = DeliveryRecord(
                **{
                    **asdict(record),
                    "status": DELIVERY_DELIVERED,
                    "attempts": record.attempts + 1,
                    "last_attempt_at": now,
                    "delivered_at": now,
                    "last_error": None,
                }
            )
        else:
            updated = DeliveryRecord(
                **{
                    **asdict(record),
                    "status": DELIVERY_FAILED,
                    "attempts": record.attempts + 1,
                    "last_attempt_at": now,
                    "last_error": error,
                }
            )
        self._write(updated)
        return updated

    def _write(self, record: DeliveryRecord) -> None:
        atomic_json(
            self._path(record.source_type, record.source_id, record.channel),
            asdict(record),
        )

    def _path(self, source_type: str, source_id: str, channel: str) -> Path:
        return self.root / f"{_safe(source_type)}__{_safe(source_id)}__{_safe(channel)}.json"


_ILLEGAL_FILENAME_CHARS = str.maketrans({char: "_" for char in '<>:"/\\|?*'})


def _safe(segment: str) -> str:
    """Keep raw ids in JSON, but never let them break filesystem paths."""
    return str(segment).translate(_ILLEGAL_FILENAME_CHARS)


def _record_from_json(value: dict) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=str(value["delivery_id"]),
        source_type=str(value["source_type"]),
        source_id=str(value["source_id"]),
        channel=str(value["channel"]),
        status=str(value["status"]),
        attempts=int(value["attempts"]),
        created_at=str(value["created_at"]),
        last_attempt_at=(
            None if value.get("last_attempt_at") is None else str(value["last_attempt_at"])
        ),
        delivered_at=(
            None if value.get("delivered_at") is None else str(value["delivered_at"])
        ),
        last_error=None if value.get("last_error") is None else str(value["last_error"]),
    )


def _stamp(clock: Clock) -> str:
    try:
        return clock.now().isoformat()
    except Exception:
        return utc_now()
