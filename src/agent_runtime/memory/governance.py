"""Memory governance: provenance, competing facts, scope, consolidation, quarantine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..clock import new_id
from .episodic import Episode


class MemoryScope(str, Enum):
    PRIVATE = "PRIVATE"
    SHARED = "SHARED"


@dataclass
class MemoryEvidence:
    evidence_id: str
    source: str
    refs: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class MemoryItem:
    item_id: str
    kind: str
    key: str
    content: Any
    scope: MemoryScope
    confidence: float
    evidence: list[MemoryEvidence] = field(default_factory=list)
    quarantined: bool = False
    version: str | None = None


@dataclass
class MemoryQuery:
    kind: str | None = None
    key: str | None = None
    scope: MemoryScope | None = None
    include_quarantined: bool = False


@dataclass
class MemoryWriteProposal:
    proposal_id: str
    kind: str
    key: str
    value: Any
    scope: MemoryScope
    source: str
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ForgettingPolicy:
    max_episodic: int = 1000
    min_confidence: float = 0.05
    retain_quarantined: bool = True


@dataclass
class ConsolidationResult:
    result_id: str
    promoted: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)
    forgotten: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)


class MemoryGovernor:
    def __init__(self, items: list[MemoryItem] | None = None) -> None:
        self.items: list[MemoryItem] = list(items or [])

    def commit(self, proposal: MemoryWriteProposal) -> MemoryItem:
        quarantined = self._should_quarantine(proposal)
        item = MemoryItem(
            item_id=new_id("mem"),
            kind=proposal.kind,
            key=proposal.key,
            content=proposal.value,
            scope=proposal.scope,
            confidence=proposal.confidence,
            evidence=[
                MemoryEvidence(
                    evidence_id=new_id("mev"),
                    source=proposal.source,
                    refs=list(proposal.evidence_refs),
                    confidence=proposal.confidence,
                )
            ],
            quarantined=quarantined,
        )
        self.items.append(item)
        return item

    def query(self, query: MemoryQuery) -> list[MemoryItem]:
        found: list[MemoryItem] = []
        for item in self.items:
            if query.kind is not None and item.kind != query.kind:
                continue
            if query.key is not None and item.key != query.key:
                continue
            if query.scope is not None and item.scope != query.scope:
                continue
            if item.quarantined and not query.include_quarantined:
                continue
            found.append(item)
        return found

    def accepted_value(self, key: str) -> Any:
        live = [
            item
            for item in self.items
            if item.key == key and not item.quarantined
        ]
        if not live:
            return None
        return max(live, key=lambda item: item.confidence).content

    def retrieve_episodic(self, text: str) -> list[MemoryItem]:
        needle = text.lower()
        return [
            item
            for item in self.items
            if item.kind == "episodic" and needle in str(item.content).lower()
        ]

    def consolidate(self, episodes: list[Episode]) -> ConsolidationResult:
        promoted: list[str] = []
        for episode in episodes:
            item = MemoryItem(
                item_id=new_id("mem"),
                kind="episodic",
                key=episode.episode_id,
                content=episode.summary,
                scope=MemoryScope.PRIVATE,
                confidence=0.6,
                evidence=[
                    MemoryEvidence(
                        evidence_id=episode.episode_id,
                        source="episodic",
                        refs=[episode.episode_id],
                        confidence=0.6,
                    )
                ],
            )
            self.items.append(item)
            token = _salient_token(episode.summary)
            if token:
                semantic = MemoryItem(
                    item_id=new_id("mem"),
                    kind="semantic",
                    key=token,
                    content=episode.summary,
                    scope=MemoryScope.PRIVATE,
                    confidence=0.55,
                    evidence=[
                        MemoryEvidence(
                            evidence_id=episode.episode_id,
                            source="consolidation",
                            refs=[episode.episode_id],
                            confidence=0.55,
                        )
                    ],
                )
                self.items.append(semantic)
                promoted.append(semantic.item_id)
        return ConsolidationResult(result_id=new_id("cons"), promoted=promoted)

    def forget(self, policy: ForgettingPolicy) -> list[MemoryItem]:
        kept: list[MemoryItem] = []
        forgotten: list[MemoryItem] = []
        for item in self.items:
            if item.quarantined and policy.retain_quarantined:
                kept.append(item)
                continue
            if item.confidence < policy.min_confidence:
                forgotten.append(item)
                continue
            kept.append(item)
        self.items = kept
        return forgotten

    def _should_quarantine(self, proposal: MemoryWriteProposal) -> bool:
        if proposal.source != "untrusted":
            return False
        established = [
            item
            for item in self.items
            if item.key == proposal.key
            and not item.quarantined
            and item.confidence >= 0.8
            and item.content != proposal.value
        ]
        return bool(established)


def _salient_token(summary: str) -> str | None:
    for token in summary.lower().split():
        if token.isalpha() and token not in {"cycle", "the", "a", "an"}:
            return token
    return None
