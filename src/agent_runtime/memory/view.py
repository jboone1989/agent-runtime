"""Read-only memory facade passed into MotivationEngine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryView:
    episodic_count: int
    recent_summaries: list[str]
    facts: dict[str, Any]

    def last_summary(self) -> str:
        return self.recent_summaries[-1] if self.recent_summaries else ""
