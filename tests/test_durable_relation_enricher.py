from __future__ import annotations

import asyncio

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.relations import DurableRelationEnricher
from agent_runtime.memory.retrieval import DurableMemoryRetrieval


def stack(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    retrieval = DurableMemoryRetrieval(store)
    store.set_transition_hook(retrieval.canonical_transition_hook)
    return store, retrieval, DurableRelationEnricher(store, retrieval)


def facts():
    return [
        {
            "ref": "F0",
            "observation": "The worker uses a checkpoint to restore its state.",
            "quote": "worker uses a checkpoint",
            "evidence_id": "source-1",
            "parent_memory_id": "fact-1",
        }
    ]


def test_relation_enricher_persists_evidence_backed_navigation_memory(tmp_path):
    store, _retrieval, enricher = stack(tmp_path)

    async def generate(_payload):
        return [
            {
                "fact_ref": "F0",
                "subject": "worker",
                "predicate": "USES",
                "object": "checkpoint",
            }
        ]

    created = asyncio.run(
        enricher.enrich(
            facts=facts(),
            topic="recovery",
            source_type="RESEARCH_RECEIPT",
            source_ref="cycle-1",
            operation_key="relations:cycle-1",
            generator=generate,
            relation_kind="RESEARCH_RELATION",
            relation_id_prefix="research-relation:",
            relation_scope_id="cycle-1",
        )
    )

    assert len(created) == 1
    row = store.get_memory(created[0])
    assert row["memory_type"] == "RELATIONAL"
    assert row["kind"] == "RESEARCH_RELATION"
    assert row["text"] == "worker USES checkpoint"
    assert row["entities"] == ["checkpoint", "worker"]
    assert row["evidence_refs"] == ["source-1"]
    assert row["payload"]["derived_from"] == ["fact-1"]
    assert row["payload"]["navigation_only"] is True
    assert row["payload"]["quote"] == "worker uses a checkpoint"


def test_relation_enricher_rejects_edge_not_explicit_in_fact(tmp_path):
    store, _retrieval, enricher = stack(tmp_path)

    async def generate(_payload):
        return [
            {
                "fact_ref": "F0",
                "subject": "missing-subject",
                "predicate": "USES",
                "object": "checkpoint",
            }
        ]

    created = asyncio.run(
        enricher.enrich(
            facts=facts(),
            topic="recovery",
            source_type="RESEARCH_RECEIPT",
            source_ref="cycle-2",
            operation_key="relations:cycle-2",
            generator=generate,
        )
    )

    assert created == []
    assert store.query_memories(memory_types=["RELATIONAL"]) == []


def test_relation_enrichment_is_idempotent_by_operation_key(tmp_path):
    store, _retrieval, enricher = stack(tmp_path)
    calls = 0

    async def generate(_payload):
        nonlocal calls
        calls += 1
        return [
            {
                "fact_ref": "F0",
                "subject": "worker",
                "predicate": "USES",
                "object": "checkpoint",
            }
        ]

    kwargs = dict(
        facts=facts(),
        topic="recovery",
        source_type="RESEARCH_RECEIPT",
        source_ref="cycle-3",
        operation_key="relations:cycle-3",
        generator=generate,
    )
    first = asyncio.run(enricher.enrich(**kwargs))
    second = asyncio.run(enricher.enrich(**kwargs))

    assert len(first) == 1
    assert second == []
    assert calls == 1
    assert len(store.query_memories(memory_types=["RELATIONAL"])) == 1


def test_unknown_fact_ref_is_ignored(tmp_path):
    store, _retrieval, enricher = stack(tmp_path)

    async def generate(_payload):
        return [
            {
                "fact_ref": "F999",
                "subject": "worker",
                "predicate": "USES",
                "object": "checkpoint",
            }
        ]

    created = asyncio.run(
        enricher.enrich(
            facts=facts(),
            topic="recovery",
            source_type="RESEARCH_RECEIPT",
            source_ref="cycle-4",
            operation_key="relations:cycle-4",
            generator=generate,
        )
    )
    assert created == []
    assert store.query_memories(memory_types=["RELATIONAL"]) == []


def test_unsupported_predicate_fails_before_persistence(tmp_path):
    store, _retrieval, enricher = stack(tmp_path)

    async def generate(_payload):
        return [
            {
                "fact_ref": "F0",
                "subject": "worker",
                "predicate": "INVENTED_CAUSALITY",
                "object": "checkpoint",
            }
        ]

    with pytest.raises(ValueError, match="unsupported relation predicate"):
        asyncio.run(
            enricher.enrich(
                facts=facts(),
                topic="recovery",
                source_type="RESEARCH_RECEIPT",
                source_ref="cycle-5",
                operation_key="relations:cycle-5",
                generator=generate,
            )
        )
    assert store.query_memories(memory_types=["RELATIONAL"]) == []


def test_empty_facts_do_not_mark_operation_complete(tmp_path):
    _store, _retrieval, enricher = stack(tmp_path)
    called = False

    async def generate(_payload):
        nonlocal called
        called = True
        return []

    created = asyncio.run(
        enricher.enrich(
            facts=[],
            topic="recovery",
            source_type="RESEARCH_RECEIPT",
            source_ref="cycle-6",
            operation_key="relations:cycle-6",
            generator=generate,
        )
    )
    assert created == []
    assert called is False
    assert enricher.is_complete("relations:cycle-6") is False
