from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .signal import Signal

Consumer = Callable[[Signal], None]


class SignalBus:
    def __init__(self, maxlen: int = 256) -> None:
        self._recent: list[Signal] = []
        self._subs: dict[str, list[Consumer]] = defaultdict(list)
        self.maxlen = maxlen

    def publish(self, signal: Signal) -> None:
        self._recent.append(signal)
        if len(self._recent) > self.maxlen:
            self._recent = self._recent[-self.maxlen :]
        for consumer in list(self._subs.get(signal.domain, [])) + list(self._subs.get("*", [])):
            consumer(signal)

    def subscribe(self, domain: str, consumer: Consumer) -> None:
        self._subs[domain].append(consumer)

    def recent(self, domain: str | None = None, *, limit: int = 64) -> list[Signal]:
        items = self._recent if domain is None else [s for s in self._recent if s.domain == domain]
        return items[-limit:]
