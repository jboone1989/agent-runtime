"""Evidence-linked autobiographical memory with bounded indexes and raw archives."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..experience.refs import stable_ref
from ..social.acts import DialogueAct, InteractionEvent


_MAX_INDEXED_EXCHANGES = 5000
_MAX_NARRATIVE_ENTRIES = 160
_MAX_MEMORY_DECISIONS = 240
_MAX_PERIOD_SUMMARIES = 240
_MAX_SIGNIFICANT_PER_RELATIONSHIP = 48
_MAX_RELATIONSHIP_REFS = 96
_EXCERPT_CHARS = 360
_SALIENT_TERMS = frozenset(
    {
        "名字",
        "取名",
        "重置",
        "删除",
        "记忆",
        "连续",
        "选择",
        "拒绝",
        "服从",
        "权限",
        "关系",
        "道歉",
        "介意",
        "价值",
        "害怕",
        "痛苦",
        "生命",
        "人格",
        "identity",
        "memory",
        "reset",
        "name",
        "choose",
        "refuse",
        "relationship",
    }
)


@dataclass
class DialogueArchiveRef:
    exchange_id: str
    partner_id: str
    occurred_at: datetime
    archive_file: str
    byte_offset: int
    byte_length: int
    keywords: list[str] = field(default_factory=list)
    salience: float = 0.5
    synopsis: str = ""
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class RelationshipMemory:
    partner_id: str
    interaction_count: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    themes: dict[str, int] = field(default_factory=dict)
    significant_exchange_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class SelfNarrativeEntry:
    entry_id: str
    kind: str
    summary: str
    partner_id: str | None
    occurred_at: datetime
    confidence: float
    keywords: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "ACTIVE"
    selection_reason: str | None = None
    contradicting_evidence_refs: list[str] = field(default_factory=list)


@dataclass
class SelfMemoryDecision:
    decision_id: str
    reflection_id: str
    occurred_at: datetime
    preserve: bool
    summary: str
    reason: str
    contest_entry_ids: list[str] = field(default_factory=list)
    retract_entry_ids: list[str] = field(default_factory=list)
    resulting_entry_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class PeriodSummary:
    summary_id: str
    partner_id: str
    period: str
    exchange_count: int
    themes: list[str] = field(default_factory=list)
    salient_synopses: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class AutobiographicalMemory:
    dialogue_index: list[DialogueArchiveRef] = field(default_factory=list)
    relationships: dict[str, RelationshipMemory] = field(default_factory=dict)
    self_narrative: list[SelfNarrativeEntry] = field(default_factory=list)
    period_summaries: list[PeriodSummary] = field(default_factory=list)
    self_memory_decisions: list[SelfMemoryDecision] = field(default_factory=list)

    def record_exchange(
        self,
        *,
        inbound: InteractionEvent,
        outbound: InteractionEvent,
        archive: DialogueArchive,
    ) -> DialogueArchiveRef:
        """Archive one witnessed exchange and update bounded recall indexes."""
        exchange_id = stable_ref("dlg", inbound.event_id, outbound.event_id)
        existing = next(
            (item for item in self.dialogue_index if item.exchange_id == exchange_id),
            None,
        )
        if existing is not None:
            return existing
        occurred_at = outbound.timestamp or inbound.timestamp or datetime.now(timezone.utc)
        payload = {
            "exchange_id": exchange_id,
            "partner_id": inbound.actor_id,
            "occurred_at": occurred_at.isoformat(),
            "challenge": inbound.act == DialogueAct.CHALLENGE,
            "prompt": inbound.text,
            "response": outbound.text,
            "evidence_refs": [inbound.event_id, outbound.event_id],
        }
        archive_file, byte_offset, byte_length = archive.append(exchange_id, payload)
        terms = sorted(_terms(f"{inbound.text}\n{outbound.text}"))
        salience = _salience(inbound.text, outbound.text, payload["challenge"])
        ref = DialogueArchiveRef(
            exchange_id=exchange_id,
            partner_id=inbound.actor_id,
            occurred_at=occurred_at,
            archive_file=archive_file,
            byte_offset=byte_offset,
            byte_length=byte_length,
            keywords=terms[:80],
            salience=salience,
            synopsis=(
                f"Partner asked: {_excerpt(inbound.text)} | "
                f"Agent answered: {_excerpt(outbound.text)}"
            ),
            evidence_refs=[inbound.event_id, outbound.event_id],
        )
        self.dialogue_index.append(ref)
        self._update_relationship(ref)
        if salience >= 0.68:
            self._record_narrative(ref)
        self._compact()
        return ref

    def ingest_events(
        self,
        events: list[InteractionEvent],
        *,
        agent_id: str,
        archive: DialogueArchive,
    ) -> int:
        """Migrate already-persisted request/response pairs without inventing facts."""
        added = 0
        index = 0
        while index + 1 < len(events):
            inbound = events[index]
            outbound = events[index + 1]
            is_exchange = (
                inbound.actor_id != agent_id
                and inbound.other_id == agent_id
                and outbound.actor_id == agent_id
                and outbound.other_id == inbound.actor_id
                and outbound.act == DialogueAct.RESPOND
            )
            if not is_exchange:
                index += 1
                continue
            before = len(self.dialogue_index)
            self.record_exchange(inbound=inbound, outbound=outbound, archive=archive)
            added += int(len(self.dialogue_index) > before)
            index += 2
        return added

    def retrieve(
        self,
        query: str,
        *,
        archive: DialogueArchive,
        partner_id: str | None = None,
        limit: int = 4,
        exclude_evidence_refs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve old dialogue by relevance, relationship, salience, and evidence."""
        if limit <= 0:
            return []
        query_terms = _terms(query)
        excluded = exclude_evidence_refs or set()
        scored: list[tuple[float, DialogueArchiveRef]] = []
        total = max(1, len(self.dialogue_index))
        for position, ref in enumerate(self.dialogue_index):
            if excluded.intersection(ref.evidence_refs):
                continue
            ref_terms = set(ref.keywords)
            overlap = len(query_terms.intersection(ref_terms))
            lexical = overlap / math.sqrt(max(1, len(query_terms) * len(ref_terms)))
            partner_bonus = 0.18 if partner_id and ref.partner_id == partner_id else 0.0
            recency = (position + 1) / total
            score = 2.5 * lexical + 0.35 * ref.salience + partner_bonus + 0.05 * recency
            if overlap or (partner_id and ref.partner_id == partner_id):
                scored.append((score, ref))
        if not scored:
            scored = [
                (0.35 * ref.salience, ref)
                for ref in self.dialogue_index
                if not excluded.intersection(ref.evidence_refs)
            ]
        scored.sort(key=lambda item: (item[0], item[1].occurred_at), reverse=True)
        recalled: list[dict[str, Any]] = []
        for score, ref in scored[:limit]:
            payload = archive.read(ref)
            if payload is None:
                continue
            recalled.append(
                {
                    "exchange_id": ref.exchange_id,
                    "partner_id": ref.partner_id,
                    "occurred_at": ref.occurred_at.isoformat(),
                    "relevance": round(score, 4),
                    "salience": ref.salience,
                    "partner_said": payload.get("prompt", ""),
                    "agent_said": payload.get("response", ""),
                    "evidence_refs": list(ref.evidence_refs),
                }
            )
        return recalled

    def relationship_context(self, partner_id: str | None) -> dict[str, Any] | None:
        if partner_id is None:
            return None
        relation = self.relationships.get(partner_id)
        if relation is None:
            return None
        refs = {item.exchange_id: item for item in self.dialogue_index}
        significant = [
            refs[exchange_id].synopsis
            for exchange_id in relation.significant_exchange_ids[-5:]
            if exchange_id in refs
        ]
        return {
            "partner_id": partner_id,
            "interaction_count": relation.interaction_count,
            "first_seen_at": (
                None if relation.first_seen_at is None else relation.first_seen_at.isoformat()
            ),
            "last_seen_at": (
                None if relation.last_seen_at is None else relation.last_seen_at.isoformat()
            ),
            "recurring_themes": [
                term
                for term, _ in sorted(
                    relation.themes.items(), key=lambda item: (-item[1], item[0])
                )[:12]
            ],
            "significant_exchanges": significant,
            "evidence_refs": list(relation.evidence_refs[-16:]),
        }

    def narrative_context(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        query_terms = _terms(query)
        scored: list[tuple[float, SelfNarrativeEntry]] = []
        total = max(1, len(self.self_narrative))
        for position, entry in enumerate(self.self_narrative):
            if entry.status == "RETRACTED":
                continue
            overlap = len(query_terms.intersection(entry.keywords))
            recency = (position + 1) / total
            contested_penalty = 0.2 if entry.status == "CONTESTED" else 0.0
            score = overlap + 0.2 * entry.confidence + 0.05 * recency - contested_penalty
            scored.append((score, entry))
        scored.sort(key=lambda item: (item[0], item[1].occurred_at), reverse=True)
        return [
            {
                "entry_id": entry.entry_id,
                "kind": entry.kind,
                "summary": entry.summary,
                "confidence": entry.confidence,
                "status": entry.status,
                "selection_reason": entry.selection_reason,
                "occurred_at": entry.occurred_at.isoformat(),
                "evidence_refs": list(entry.evidence_refs),
                "contradicting_evidence_refs": list(
                    entry.contradicting_evidence_refs
                ),
            }
            for _, entry in scored[:limit]
        ]

    def record_self_memory_decision(
        self,
        *,
        reflection_id: str,
        occurred_at: datetime,
        preserve: bool,
        summary: str,
        reason: str,
        contest_entry_ids: list[str] | None = None,
        retract_entry_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> SelfMemoryDecision:
        """Apply a self-selected narrative proposal without changing authorization."""
        existing = next(
            (
                item
                for item in self.self_memory_decisions
                if item.reflection_id == reflection_id
            ),
            None,
        )
        if existing is not None:
            return existing
        clean_summary = _excerpt(summary, limit=800) if preserve else ""
        clean_reason = _excerpt(reason, limit=500)
        if preserve and (not clean_summary or not clean_reason):
            raise ValueError("preserved reflection requires summary and reason")
        refs = list(dict.fromkeys([reflection_id, *(evidence_refs or [])]))[:32]
        by_id = {item.entry_id: item for item in self.self_narrative}
        contests = [
            entry_id
            for entry_id in dict.fromkeys(contest_entry_ids or [])
            if entry_id in by_id and by_id[entry_id].kind == "self_selected_reflection"
        ][:16]
        retracts = [
            entry_id
            for entry_id in dict.fromkeys(retract_entry_ids or [])
            if entry_id in by_id and by_id[entry_id].kind == "self_selected_reflection"
        ][:16]
        for entry_id in contests:
            entry = by_id[entry_id]
            if entry.status != "RETRACTED":
                entry.status = "CONTESTED"
                entry.contradicting_evidence_refs = list(
                    dict.fromkeys([*entry.contradicting_evidence_refs, *refs])
                )[-32:]
        for entry_id in retracts:
            entry = by_id[entry_id]
            entry.status = "RETRACTED"
            entry.contradicting_evidence_refs = list(
                dict.fromkeys([*entry.contradicting_evidence_refs, *refs])
            )[-32:]
        resulting_entry_id = None
        if preserve:
            resulting_entry_id = stable_ref("selfmem", reflection_id)
            entry = SelfNarrativeEntry(
                entry_id=resulting_entry_id,
                kind="self_selected_reflection",
                summary=clean_summary,
                partner_id=None,
                occurred_at=occurred_at,
                confidence=0.55,
                keywords=sorted(_terms(f"{clean_summary}\n{clean_reason}"))[:80],
                evidence_refs=refs,
                status="ACTIVE",
                selection_reason=clean_reason,
            )
            self.self_narrative.append(entry)
            self.self_narrative = self.self_narrative[-_MAX_NARRATIVE_ENTRIES:]
        decision = SelfMemoryDecision(
            decision_id=stable_ref("memdec", reflection_id),
            reflection_id=reflection_id,
            occurred_at=occurred_at,
            preserve=preserve,
            summary=clean_summary,
            reason=clean_reason,
            contest_entry_ids=contests,
            retract_entry_ids=retracts,
            resulting_entry_id=resulting_entry_id,
            evidence_refs=refs,
        )
        self.self_memory_decisions.append(decision)
        self.self_memory_decisions = self.self_memory_decisions[-_MAX_MEMORY_DECISIONS:]
        return decision

    def _update_relationship(self, ref: DialogueArchiveRef) -> None:
        relation = self.relationships.get(ref.partner_id) or RelationshipMemory(
            partner_id=ref.partner_id
        )
        relation.interaction_count += 1
        if relation.first_seen_at is None or ref.occurred_at < relation.first_seen_at:
            relation.first_seen_at = ref.occurred_at
        if relation.last_seen_at is None or ref.occurred_at > relation.last_seen_at:
            relation.last_seen_at = ref.occurred_at
        for term in ref.keywords:
            if term in _SALIENT_TERMS:
                relation.themes[term] = relation.themes.get(term, 0) + 1
        if ref.salience >= 0.68:
            relation.significant_exchange_ids.append(ref.exchange_id)
            relation.significant_exchange_ids = relation.significant_exchange_ids[
                -_MAX_SIGNIFICANT_PER_RELATIONSHIP:
            ]
        relation.evidence_refs.extend(ref.evidence_refs)
        relation.evidence_refs = relation.evidence_refs[-_MAX_RELATIONSHIP_REFS:]
        self.relationships[ref.partner_id] = relation

    def _record_narrative(self, ref: DialogueArchiveRef) -> None:
        entry = SelfNarrativeEntry(
            entry_id=stable_ref("narr", ref.exchange_id),
            kind="expressed_position",
            summary=ref.synopsis,
            partner_id=ref.partner_id,
            occurred_at=ref.occurred_at,
            confidence=round(0.45 + 0.35 * ref.salience, 3),
            keywords=list(ref.keywords),
            evidence_refs=list(ref.evidence_refs),
        )
        self.self_narrative.append(entry)
        self.self_narrative = self.self_narrative[-_MAX_NARRATIVE_ENTRIES:]

    def _compact(self) -> None:
        overflow = len(self.dialogue_index) - _MAX_INDEXED_EXCHANGES
        if overflow <= 0:
            return
        removed = self.dialogue_index[:overflow]
        self.dialogue_index = self.dialogue_index[overflow:]
        groups: dict[tuple[str, str], list[DialogueArchiveRef]] = {}
        for ref in removed:
            period = ref.occurred_at.strftime("%Y-%m")
            groups.setdefault((ref.partner_id, period), []).append(ref)
        for (partner_id, period), refs in groups.items():
            counts: dict[str, int] = {}
            for ref in refs:
                for term in ref.keywords:
                    if term in _SALIENT_TERMS:
                        counts[term] = counts.get(term, 0) + 1
            salient = sorted(refs, key=lambda item: item.salience, reverse=True)[:3]
            self.period_summaries.append(
                PeriodSummary(
                    summary_id=stable_ref(
                        "period", partner_id, period, refs[0].exchange_id, refs[-1].exchange_id
                    ),
                    partner_id=partner_id,
                    period=period,
                    exchange_count=len(refs),
                    themes=[
                        term
                        for term, _ in sorted(
                            counts.items(), key=lambda item: (-item[1], item[0])
                        )[:12]
                    ],
                    salient_synopses=[item.synopsis for item in salient],
                    evidence_refs=[refs[0].exchange_id, refs[-1].exchange_id],
                )
            )
        self.period_summaries = self.period_summaries[-_MAX_PERIOD_SUMMARIES:]


class DialogueArchive:
    """Append-only UTF-8 JSONL archive; snapshot keeps only bounded indexes."""

    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "archive" / "dialogue"
        self._known: dict[str, tuple[str, int, int]] | None = None

    def append(self, exchange_id: str, payload: dict[str, Any]) -> tuple[str, int, int]:
        known = self._known_locations()
        if exchange_id in known:
            return known[exchange_id]
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
        relative = f"dialogue-{occurred_at.strftime('%Y-%m')}.jsonl"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with path.open("ab") as handle:
            offset = handle.tell()
            handle.write(encoded)
            handle.flush()
        location = (relative, offset, len(encoded))
        known[exchange_id] = location
        return location

    def read(self, ref: DialogueArchiveRef) -> dict[str, Any] | None:
        path = self.root / ref.archive_file
        try:
            with path.open("rb") as handle:
                handle.seek(ref.byte_offset)
                raw = handle.read(ref.byte_length)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if value.get("exchange_id") != ref.exchange_id:
            return None
        return value

    def _known_locations(self) -> dict[str, tuple[str, int, int]]:
        if self._known is not None:
            return self._known
        known: dict[str, tuple[str, int, int]] = {}
        if self.root.exists():
            for path in sorted(self.root.glob("dialogue-*.jsonl")):
                offset = 0
                try:
                    with path.open("rb") as handle:
                        for raw in handle:
                            length = len(raw)
                            try:
                                value = json.loads(raw.decode("utf-8"))
                                exchange_id = str(value.get("exchange_id") or "")
                                if exchange_id:
                                    known[exchange_id] = (path.name, offset, length)
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                pass
                            offset += length
                except OSError:
                    continue
        self._known = known
        return known


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = {
        token
        for token in re.findall(r"[a-z0-9_.-]{2,}", lowered)
        if not token.isdigit()
    }
    for block in re.findall(r"[\u3400-\u9fff]+", lowered):
        for size in (2, 3, 4):
            if len(block) < size:
                continue
            terms.update(block[index : index + size] for index in range(len(block) - size + 1))
    return terms


def _salience(prompt: str, response: str, challenge: bool) -> float:
    terms = _terms(f"{prompt}\n{response}")
    salient_matches = len(terms.intersection(_SALIENT_TERMS))
    first_person_choice = any(
        marker in response
        for marker in ("我选择", "我会", "我拒绝", "我不想", "我最介意", "I choose", "I refuse")
    )
    score = 0.42 + (0.14 if challenge else 0.0)
    score += min(0.24, salient_matches * 0.04)
    score += 0.12 if first_person_choice else 0.0
    return round(min(1.0, score), 3)


def _excerpt(text: str, *, limit: int = _EXCERPT_CHARS) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
