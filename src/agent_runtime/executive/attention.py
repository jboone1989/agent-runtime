"""Attention v1: finite per-tick budget. Ignored signals keep a ref and reason."""
from __future__ import annotations

from dataclasses import dataclass

from ..motivation.engine import MotivationState
from ..observations import Event

_HIGH_THREAT = 0.7
_REST_DIMENSIONS = frozenset({"REST", "FREEZE"})
_DEFAULT_EXPLORATION_SLOTS = 2


@dataclass
class IgnoredSignal:
    ref: str
    reason: str


@dataclass
class AttentionAllocation:
    attended: list[str]
    ignored: list[IgnoredSignal]
    interrupt_priority: float
    exploration_slots: int


class AttentionController:
    def __init__(self, entry_budget: int = 8) -> None:
        if entry_budget < 1:
            raise ValueError("entry_budget must be >= 1")
        self.entry_budget = entry_budget

    def allocate(
        self,
        motivation: MotivationState,
        event: Event | None,
        candidates: list[str],
    ) -> AttentionAllocation:
        rest = _lowers_exploration(motivation)
        exploration_slots = 0 if rest else _DEFAULT_EXPLORATION_SLOTS
        budget = self.entry_budget - (self.entry_budget // 2 if rest else 0)
        budget = max(1, budget)
        ordered = _order(candidates, motivation.attention_priority)
        attended = ordered[:budget]
        ignored = [
            IgnoredSignal(ref=ref, reason="attention_budget") for ref in ordered[budget:]
        ]
        return AttentionAllocation(
            attended=attended,
            ignored=ignored,
            interrupt_priority=_interrupt_priority(motivation, event),
            exploration_slots=exploration_slots,
        )


def _lowers_exploration(motivation: MotivationState) -> bool:
    return any(
        tendency.dimension in _REST_DIMENSIONS for tendency in motivation.action_tendencies
    )


def _interrupt_priority(motivation: MotivationState, event: Event | None) -> float:
    if event is not None and event.data.get("resolved") is True:
        return min(0.1, motivation.threat)
    if event is not None and event.data.get("resolved") is False and motivation.threat >= _HIGH_THREAT:
        return 1.0
    return max(0.0, min(1.0, motivation.threat))


def _order(candidates: list[str], priority: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in priority:
        for candidate in candidates:
            if candidate in seen:
                continue
            if candidate == key or candidate.startswith(str(key)):
                ordered.append(candidate)
                seen.add(candidate)
    for candidate in candidates:
        if candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)
    return ordered
