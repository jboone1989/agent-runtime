"""Model-agnostic durable relation enrichment for memory.

The runtime owns relation validation, provenance and canonical persistence.
A provider callback owns only the model inference that proposes explicit
relations from supplied evidence-backed facts.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .durable import CanonicalMemoryStore
from .retrieval import DurableMemoryRetrieval, fingerprint


RelationGenerator = Callable[
    [Mapping[str, Any]],
    Awaitable[Sequence[Mapping[str, Any]]],
]

DEFAULT_RELATION_PREDICATES = frozenset(
    {
        "USES",
        "DEPENDS_ON",
        "PRECEDES",
        "PART_OF",
        "SUPPORTS",
        "CONTRADICTS",
    }
)


class DurableRelationEnricher:
    """Persist explicit, evidence-backed navigation relations."""

    def __init__(
        self,
        store: CanonicalMemoryStore,
        retrieval: DurableMemoryRetrieval,
        *,
        allowed_predicates: Sequence[str] = tuple(
            sorted(DEFAULT_RELATION_PREDICATES)
        ),
    ) -> None:
        self.store = store
        self.retrieval = retrieval
        self.allowed_predicates = frozenset(
            str(value).strip().upper()
            for value in allowed_predicates
            if str(value).strip()
        )
        if not self.allowed_predicates:
            raise ValueError(
                "allowed_predicates must not be empty"
            )

    def is_complete(self, operation_key: str) -> bool:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT 1 FROM memory_service_meta WHERE key=?",
                (str(operation_key),),
            ).fetchone()
        return row is not None

    def mark_complete(self, operation_key: str) -> None:
        with self.store._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memory_service_meta "
                "VALUES(?,?)",
                (str(operation_key), "done"),
            )

    @staticmethod
    def _normalize_facts(
        facts: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen_refs: set[str] = set()
        for index, raw in enumerate(facts):
            ref = str(raw.get("ref") or f"F{index}").strip()
            if not ref:
                raise ValueError("fact ref must be non-empty")
            if ref in seen_refs:
                raise ValueError(
                    f"duplicate fact ref: {ref}"
                )
            seen_refs.add(ref)
            observation = str(
                raw.get("observation") or ""
            ).strip()
            quote = str(raw.get("quote") or "").strip()
            evidence_id = str(
                raw.get("evidence_id") or ""
            ).strip()
            parent_memory_id = str(
                raw.get("parent_memory_id") or ""
            ).strip()
            if not observation:
                raise ValueError(
                    f"fact {ref} observation is required"
                )
            if not quote:
                raise ValueError(
                    f"fact {ref} quote is required"
                )
            if not evidence_id:
                raise ValueError(
                    f"fact {ref} evidence_id is required"
                )
            if not parent_memory_id:
                raise ValueError(
                    f"fact {ref} parent_memory_id is required"
                )
            normalized.append(
                {
                    "ref": ref,
                    "observation": observation,
                    "quote": quote,
                    "evidence_id": evidence_id,
                    "parent_memory_id": parent_memory_id,
                }
            )
        return normalized

    @staticmethod
    def request(
        facts: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        normalized = DurableRelationEnricher._normalize_facts(
            facts
        )
        payload = {
            "facts": [
                {
                    "ref": fact["ref"],
                    "observation": fact["observation"],
                    "quote": fact["quote"],
                }
                for fact in normalized
            ]
        }
        return normalized, payload

    def _validate_edge(
        self,
        edge: Mapping[str, Any],
        *,
        facts_by_ref: Mapping[str, Mapping[str, str]],
    ) -> dict[str, str] | None:
        fact_ref = str(
            edge.get("fact_ref") or ""
        ).strip()
        fact = facts_by_ref.get(fact_ref)
        if fact is None:
            return None

        subject = str(edge.get("subject") or "").strip()
        predicate = str(
            edge.get("predicate") or ""
        ).strip().upper()
        object_value = str(
            edge.get("object") or ""
        ).strip()

        if not subject or not object_value:
            raise ValueError(
                "relation subject and object must be non-empty"
            )
        if predicate not in self.allowed_predicates:
            raise ValueError(
                f"unsupported relation predicate: {predicate}"
            )

        source_text = (
            fact["quote"] + " " + fact["observation"]
        ).casefold()
        if (
            subject.casefold() not in source_text
            or object_value.casefold() not in source_text
        ):
            return None

        return {
            "fact_ref": fact_ref,
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
        }

    async def enrich(
        self,
        *,
        facts: Sequence[Mapping[str, Any]],
        topic: str,
        source_type: str,
        source_ref: str,
        operation_key: str,
        generator: RelationGenerator,
        relation_kind: str = "RELATION",
        relation_id_prefix: str = "relation:",
        relation_scope_id: str | None = None,
    ) -> list[str]:
        if self.is_complete(operation_key):
            return []

        normalized, payload = self.request(facts)
        if not normalized:
            return []

        proposed = await generator(payload)
        if not isinstance(proposed, Sequence) or isinstance(
            proposed,
            (str, bytes),
        ):
            raise ValueError(
                "relation generator must return a sequence"
            )

        by_ref = {
            fact["ref"]: fact
            for fact in normalized
        }
        created: list[str] = []
        scope = (
            str(relation_scope_id)
            if relation_scope_id is not None
            else str(source_ref)
        )

        for raw_edge in proposed:
            if not isinstance(raw_edge, Mapping):
                raise ValueError(
                    "relation generator items must be mappings"
                )
            edge = self._validate_edge(
                raw_edge,
                facts_by_ref=by_ref,
            )
            if edge is None:
                continue
            fact = by_ref[edge["fact_ref"]]
            relation_id = (
                relation_id_prefix
                + fingerprint(
                    [scope, edge]
                )[:28]
            )
            record = {
                "memory_id": relation_id,
                "memory_type": "RELATIONAL",
                "kind": str(
                    relation_kind or "RELATION"
                ).strip().upper(),
                "text": (
                    edge["subject"]
                    + " "
                    + edge["predicate"]
                    + " "
                    + edge["object"]
                ),
                "entities": [
                    edge["subject"],
                    edge["object"],
                ],
                "topics": [str(topic)],
                "source_type": str(source_type),
                "source_ref": str(source_ref),
                "evidence_refs": [
                    fact["evidence_id"]
                ],
                "payload": {
                    **edge,
                    "derived_from": [
                        fact["parent_memory_id"]
                    ],
                    "navigation_only": True,
                    "quote": fact["quote"],
                },
            }
            if self.store.append_memory(record):
                created.append(relation_id)

        self.retrieval.sync()
        self.mark_complete(operation_key)
        return created


__all__ = [
    "DEFAULT_RELATION_PREDICATES",
    "DurableRelationEnricher",
    "RelationGenerator",
]
