"""Memory governance: provenance, competing facts, scope, consolidation, quarantine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..clock import new_id
from .episodic import Episode
from .view import MemoryView


TRUSTED_SOURCES = frozenset({"runtime", "episodic", "consolidation", "boot"})
UNTRUSTED_SOURCES = frozenset({"untrusted", "poison", "injected", "external_unverified"})


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
        if not quarantined:
            for item in self.items:
                if (
                    item.kind == proposal.kind
                    and item.key == proposal.key
                    and not item.quarantined
                    and proposal.source in TRUSTED_SOURCES
                ):
                    item.content = proposal.value
                    item.confidence = proposal.confidence
                    return item
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
            (index, item)
            for index, item in enumerate(self.items)
            if item.key == key and not item.quarantined
        ]
        if not live:
            return None
        return max(live, key=lambda pair: (pair[1].confidence, pair[0]))[1].content

    def retrieve_episodic(
        self,
        text: str,
        *,
        scope: MemoryScope | None = None,
        include_quarantined: bool = False,
    ) -> list[MemoryItem]:
        needle = text.lower()
        return [
            item
            for item in self.query(
                MemoryQuery(
                    kind="episodic",
                    scope=scope,
                    include_quarantined=include_quarantined,
                )
            )
            if needle in str(item.content).lower()
        ]

    def as_view(self) -> MemoryView:
        live_ep = self.query(MemoryQuery(kind="episodic"))
        keys = {item.key for item in self.query(MemoryQuery(kind="semantic"))}
        facts = {key: self.accepted_value(key) for key in keys}
        return MemoryView(
            episodic_count=len(live_ep),
            recent_summaries=[str(item.content) for item in live_ep[-8:]],
            facts={key: value for key, value in facts.items() if value is not None},
        )

    def record_episode(self, episode: Episode, *, source: str = "runtime") -> MemoryItem:
        existing = next(
            (
                item
                for item in self.items
                if item.kind == "episodic" and item.key == episode.episode_id
            ),
            None,
        )
        if existing is not None:
            return existing
        return self.commit(
            MemoryWriteProposal(
                proposal_id=episode.episode_id,
                kind="episodic",
                key=episode.episode_id,
                value=episode.summary,
                scope=MemoryScope.PRIVATE,
                source=source,
                confidence=0.7,
                evidence_refs=[episode.episode_id],
            )
        )

    def consolidate(self, episodes: list[Episode]) -> ConsolidationResult:
        promoted: list[str] = []
        for episode in episodes:
            if any(
                item.kind == "episodic" and item.key == episode.episode_id
                for item in self.items
            ):
                continue
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
        live_episodic = [
            item for item in self.items if item.kind == "episodic" and not item.quarantined
        ]
        if policy.max_episodic >= 0 and len(live_episodic) > policy.max_episodic:
            overflow = live_episodic[: len(live_episodic) - policy.max_episodic]
            overflow_ids = {item.item_id for item in overflow}
            forgotten.extend(overflow)
            self.items = [item for item in self.items if item.item_id not in overflow_ids]
        return forgotten

    def _should_quarantine(self, proposal: MemoryWriteProposal) -> bool:
        if proposal.source in UNTRUSTED_SOURCES:
            return True
        if proposal.source in TRUSTED_SOURCES:
            return False
        return not proposal.evidence_refs


def _salient_token(summary: str) -> str | None:
    for token in summary.lower().split():
        if token.isalpha() and token not in {"cycle", "the", "a", "an"}:
            return token
    return None
