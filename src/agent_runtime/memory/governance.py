"""Memory governance: provenance, competing facts, scope, consolidation, quarantine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
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
    max_semantic: int = 512
    max_quarantined: int = 100
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
                    item.scope = proposal.scope
                    item.evidence.append(
                        MemoryEvidence(
                            evidence_id=new_id("mev"),
                            source=proposal.source,
                            refs=list(proposal.evidence_refs),
                            confidence=proposal.confidence,
                        )
                    )
                    item.evidence = item.evidence[-32:]
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

    def accepted_value(self, key: str, *, kind: str | None = None) -> Any:
        live = [
            (index, item)
            for index, item in enumerate(self.items)
            if item.key == key
            and not item.quarantined
            and (kind is None or item.kind == kind)
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
        facts = {key: self.accepted_value(key, kind="semantic") for key in keys}
        return MemoryView(
            episodic_count=len(live_ep),
            recent_summaries=[str(item.content) for item in live_ep[-8:]],
            facts={key: value for key, value in facts.items() if value is not None},
        )

    def context_facts(self, query: str, *, limit: int = 32) -> dict[str, Any]:
        """Select a bounded, query-relevant semantic view for an LLM context."""
        if limit <= 0:
            return {}
        accepted: dict[str, tuple[int, MemoryItem]] = {}
        for index, item in enumerate(self.items):
            if item.kind != "semantic" or item.quarantined:
                continue
            previous = accepted.get(item.key)
            if previous is None or (item.confidence, index) >= (
                previous[1].confidence,
                previous[0],
            ):
                accepted[item.key] = (index, item)
        query_terms = _context_terms(query)
        ranked: list[tuple[float, int, MemoryItem]] = []
        for index, item in accepted.values():
            item_terms = _context_terms(f"{item.key} {item.content}")
            overlap = len(query_terms.intersection(item_terms))
            core = item.key in {"who", "next"} or "anchor" in item.key.lower()
            score = 3.0 * overlap + item.confidence + (2.0 if core else 0.0)
            ranked.append((score, index, item))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return {item.key: item.content for _, _, item in ranked[:limit]}

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
        live_semantic = [
            item
            for item in self.items
            if item.kind == "semantic"
            and not item.quarantined
            and item.key not in {"who", "next"}
        ]
        if policy.max_semantic >= 0 and len(live_semantic) > policy.max_semantic:
            overflow = live_semantic[: len(live_semantic) - policy.max_semantic]
            overflow_ids = {item.item_id for item in overflow}
            forgotten.extend(overflow)
            self.items = [item for item in self.items if item.item_id not in overflow_ids]
        quarantined = [item for item in self.items if item.quarantined]
        if policy.max_quarantined >= 0 and len(quarantined) > policy.max_quarantined:
            overflow = quarantined[: len(quarantined) - policy.max_quarantined]
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


def _context_terms(text: str) -> set[str]:
    lowered = str(text).lower()
    terms = set(re.findall(r"[a-z0-9_.-]{2,}", lowered))
    for block in re.findall(r"[\u3400-\u9fff]+", lowered):
        for size in (2, 3):
            if len(block) >= size:
                terms.update(
                    block[index : index + size]
                    for index in range(len(block) - size + 1)
                )
    return terms
