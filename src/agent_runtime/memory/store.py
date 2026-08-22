from __future__ import annotations

from datetime import datetime

from ..clock import new_id
from .episodic import Episode, EpisodicMemory
from .governance import MemoryGovernor, MemoryQuery, MemoryScope, MemoryWriteProposal
from .procedural import ProceduralMemory
from .semantic import SemanticMemory
from .view import MemoryView


class MemoryStore:
    def __init__(self, governor: MemoryGovernor | None = None) -> None:
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self.governor = governor

    def attach(self, governor: MemoryGovernor) -> None:
        self.governor = governor
        self.sync_from_governor()

    def view(self) -> MemoryView:
        if self.governor is not None:
            return self.governor.as_view()
        summaries = [e.summary for e in self.episodic.episodes[-8:]]
        return MemoryView(
            episodic_count=len(self.episodic.episodes),
            recent_summaries=summaries,
            facts=dict(self.semantic.facts),
        )

    def commit(self, *, timestamp: datetime, summary: str, payload: dict | None = None) -> None:
        episode = Episode(
            episode_id=new_id("ep"),
            timestamp=timestamp,
            summary=summary,
            payload=payload or {},
        )
        self.episodic.append(episode)
        if self.governor is not None:
            self.governor.record_episode(episode, source="runtime")
            self.sync_from_governor()

    def put_semantic(self, key: str, value, *, source: str = "runtime") -> None:
        if self.governor is None:
            self.semantic.put(key, value)
            return
        self.governor.commit(
            MemoryWriteProposal(
                proposal_id=new_id("w"),
                kind="semantic",
                key=key,
                value=value,
                scope=MemoryScope.PRIVATE,
                source=source,
                confidence=0.9,
                evidence_refs=[key],
            )
        )
        self.sync_from_governor()

    def sync_from_governor(self) -> None:
        if self.governor is None:
            return
        live = self.governor.query(MemoryQuery(kind="episodic"))
        known = {episode.episode_id: episode for episode in self.episodic.episodes}
        rebuilt: list[Episode] = []
        for item in live:
            existing = known.get(item.key)
            if existing is not None:
                rebuilt.append(existing)
            else:
                rebuilt.append(
                    Episode(
                        episode_id=item.key,
                        timestamp=datetime.min,
                        summary=str(item.content),
                        payload={},
                    )
                )
        self.episodic.episodes = rebuilt
        keys = {item.key for item in self.governor.query(MemoryQuery(kind="semantic"))}
        self.semantic.facts = {
            key: value
            for key in keys
            if (value := self.governor.accepted_value(key)) is not None
        }
