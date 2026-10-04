from __future__ import annotations

import asyncio
import re

import pytest

from agent_runtime.memory.consolidation import SemanticMemoryConsolidator
from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.retrieval import DurableMemoryRetrieval


class Embeddings:
    model_id = "test-embedding:v1"

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts, *, query=False):
        assert query is False
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            match = re.search(r"(\d+)(?!.*\d)", text)
            value = float(match.group(1)) if match else float(len(text))
            vectors.append([value, value % 7.0, (value * 3.0) % 11.0])
        return vectors


def stack(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    retrieval = DurableMemoryRetrieval(store)
    store.set_transition_hook(retrieval.canonical_transition_hook)
    consolidator = SemanticMemoryConsolidator(retrieval)
    return store, retrieval, consolidator


def seed(store, count=8, topic="recovery"):
    for index in range(count):
        store.append_memory(
            {
                "memory_id": f"fact-{index}",
                "kind": "FACT",
                "text": f"Checkpoint recovery fact {index}",
                "topics": [topic],
            }
        )


def generator_counter():
    calls = []

    async def generate(payload):
        calls.append(payload)
        child_refs = ",".join(row["ref"] for row in payload["children"])
        return {
            "summary": (
                "Evidence-backed checkpoint recovery summary for "
                + child_refs
            ),
            "context_prefix": "Checkpoint recovery evidence hierarchy.",
        }

    return calls, generate


def test_consolidation_returns_none_without_topic_leaves(tmp_path):
    _store, _retrieval, consolidator = stack(tmp_path)
    embeddings = Embeddings()
    calls, generate = generator_counter()

    result = asyncio.run(
        consolidator.consolidate(
            "missing",
            embedding_provider=embeddings,
            generator=generate,
        )
    )

    assert result is None
    assert embeddings.calls == []
    assert calls == []


def test_small_topic_summary_is_cached_by_child_versions(tmp_path):
    store, retrieval, consolidator = stack(tmp_path)
    seed(store, count=4)
    embeddings = Embeddings()
    calls, generate = generator_counter()

    first = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )
    second = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )

    assert first == second
    assert len(calls) == 1
    assert embeddings.calls == []
    root = retrieval.node(first)
    assert root["node_type"] == "summary"
    assert root["metadata"]["topics"] == ["recovery"]
    assert len(retrieval.expand(first)) == 4


def test_large_topic_builds_bounded_semantic_tree(tmp_path):
    store, retrieval, consolidator = stack(tmp_path)
    seed(store, count=26)
    embeddings = Embeddings()
    calls, generate = generator_counter()

    root_id = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )

    assert root_id is not None
    assert len(calls) > 1
    assert embeddings.calls
    with store._connect() as db:
        rows = db.execute(
            """SELECT parent_id,COUNT(*) AS child_count
            FROM memory_dependencies
            WHERE parent_id IN (
                SELECT node_id FROM memory_nodes
                WHERE node_type='summary'
            )
            GROUP BY parent_id"""
        ).fetchall()
    assert rows
    assert all(1 <= row["child_count"] <= 6 for row in rows)
    assert 1 <= len(retrieval.expand(root_id)) <= 6


def test_retracted_child_rebuilds_tree_and_supersedes_old_root(tmp_path):
    store, retrieval, consolidator = stack(tmp_path)
    seed(store, count=9)
    embeddings = Embeddings()
    calls, generate = generator_counter()

    old_root = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )
    old_calls = len(calls)

    store.transition_memory("fact-0", status="RETRACTED")
    new_root = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )

    assert new_root != old_root
    assert len(calls) > old_calls
    assert retrieval.node(old_root)["status"] == "SUPERSEDED"
    assert retrieval.node(new_root)["status"] == "ACTIVE"


def test_vectors_are_reused_when_model_and_node_versions_are_unchanged(tmp_path):
    store, _retrieval, consolidator = stack(tmp_path)
    seed(store, count=13)
    embeddings = Embeddings()
    calls, generate = generator_counter()

    first = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )
    embedded_documents = sum(len(batch) for batch in embeddings.calls)
    assert embedded_documents >= 13

    second = asyncio.run(
        consolidator.consolidate(
            "recovery",
            embedding_provider=embeddings,
            generator=generate,
        )
    )

    assert second == first
    assert sum(len(batch) for batch in embeddings.calls) == embedded_documents
    assert calls


def test_invalid_generated_summary_is_rejected_before_persistence(tmp_path):
    store, _retrieval, consolidator = stack(tmp_path)
    seed(store, count=2)
    embeddings = Embeddings()

    async def bad(_payload):
        return {
            "summary": "short",
            "context_prefix": "also",
        }

    with pytest.raises(ValueError, match="summary must be"):
        asyncio.run(
            consolidator.consolidate(
                "recovery",
                embedding_provider=embeddings,
                generator=bad,
            )
        )

    with store._connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM memory_nodes "
                "WHERE node_type='summary'"
            ).fetchone()[0]
            == 0
        )
