from __future__ import annotations

from datetime import datetime

from ..clock import new_id
from .episodic import Episode, EpisodicMemory
from .procedural import ProceduralMemory
from .semantic import SemanticMemory
from .view import MemoryView


class MemoryStore:
    def __init__(self) -> None:
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()

    def view(self) -> MemoryView:
        summaries = [e.summary for e in self.episodic.episodes[-8:]]
        return MemoryView(
            episodic_count=len(self.episodic.episodes),
            recent_summaries=summaries,
            facts=dict(self.semantic.facts),
        )

    def commit(self, *, timestamp: datetime, summary: str, payload: dict | None = None) -> None:
        self.episodic.append(
            Episode(
                episode_id=new_id("ep"),
                timestamp=timestamp,
                summary=summary,
                payload=payload or {},
            )
        )
