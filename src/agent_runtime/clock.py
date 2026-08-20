"""Monotonic ids and injectable clocks."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


@dataclass
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        import time

        if seconds > 0:
            time.sleep(seconds)


@dataclass
class FrozenClock:
    t: datetime

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.t = self.t + timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        return
