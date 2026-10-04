from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.memory.contextualization import EvidenceContextualizer
from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.retrieval import DurableMemoryRetrieval
from agent_runtime.memory.sources import DurableEvidenceSources


def stack(tmp_path):
    store = CanonicalMemoryStore(
        tmp_path,
        default_subject_id="agent:test",
    )
    retrieval = DurableMemoryRetrieval(store)
    store.set_transition_hook(retrieval.canonical_transition_hook)
    sources = DurableEvidenceSources(store, retrieval)
    contextualizer = EvidenceContextualizer(retrieval)
    return store, retrieval, sources, contextualizer


def source():
    return {
        "evidence_id": "source-1",
        "title": "Checkpoint source",
        "summary": (
            "The worker uses a checkpoint to restore state after restart. "
            "The checkpoint is persisted before the host exits."
        ),
        "evidence_text": (
            "The worker uses a checkpoint to restore state after restart. "
            "The checkpoint is persisted before the host exits."
        ),
        "url": "https://example.com/checkpoint",
    }


def evidence_ids(retrieval, root_id):
    return [
        row["node_id"]
        for row in retrieval.expand(root_id)
        if row["type"] == "evidence"
    ]


def test_contextualizer_builds_bounded_provider_request(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    nodes, payload = contextualizer.request(
        evidence_ids(retrieval, root),
        limit=8,
    )

    assert len(nodes) == 1
    assert payload["chunks"][0]["ref"] == "C0"
    assert "checkpoint" in payload["chunks"][0]["text"]
    assert len(payload["chunks"][0]["source_context"]) <= 300


def test_contextualization_preserves_original_and_invalidates_vector(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    node_id = evidence_ids(retrieval, root)[0]
    before = retrieval.node(node_id)

    with retrieval.store._connect() as db:
        db.execute(
            """UPDATE memory_nodes
            SET vector=?,model_id=?,vector_revision=?
            WHERE node_id=?""",
            (json.dumps([1.0, 0.0]), "model-x", "rev-x", node_id),
        )

    async def generate(payload):
        assert payload["chunks"][0]["text"] == before["body"]
        return {
            "C0": "Checkpoint recovery implementation context."
        }

    changed = asyncio.run(
        contextualizer.contextualize([node_id], generate)
    )
    after = retrieval.node(node_id)

    assert changed == 1
    assert after["body"] == before["body"]
    assert after["version"] == before["version"]
    assert after["prefix"] == "Checkpoint recovery implementation context."
    assert after["metadata"]["contextualized"] is True
    assert after["vector"] is None
    assert after["model_id"] == ""
    assert after["vector_revision"] == ""


def test_contextualized_node_is_not_requested_again(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    node_id = evidence_ids(retrieval, root)[0]

    async def generate(_payload):
        return {"C0": "Stable checkpoint recovery context."}

    assert asyncio.run(
        contextualizer.contextualize([node_id], generate)
    ) == 1

    called = False

    async def should_not_run(_payload):
        nonlocal called
        called = True
        return {}

    assert asyncio.run(
        contextualizer.contextualize([node_id], should_not_run)
    ) == 0
    assert called is False


def test_contextualization_rejects_missing_or_extra_refs_without_mutation(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    node_id = evidence_ids(retrieval, root)[0]
    before = retrieval.node(node_id)

    async def bad(_payload):
        return {"WRONG": "This prefix is long enough to pass length validation."}

    with pytest.raises(ValueError, match="exactly one prefix"):
        asyncio.run(
            contextualizer.contextualize([node_id], bad)
        )

    after = retrieval.node(node_id)
    assert after["prefix"] == before["prefix"]
    assert not after["metadata"].get("contextualized")


def test_contextualization_rejects_stale_node_version(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    node_id = evidence_ids(retrieval, root)[0]

    async def generate(_payload):
        current = retrieval.node(node_id)
        retrieval.put_node(
            node_id,
            current["node_type"],
            current["body"] + " changed",
            current["metadata"],
            prefix=current["prefix"],
        )
        return {"C0": "Checkpoint recovery implementation context."}

    with pytest.raises(ValueError, match="changed during contextualization"):
        asyncio.run(
            contextualizer.contextualize([node_id], generate)
        )


def test_contextualization_updates_fts_with_new_context(tmp_path):
    _store, retrieval, sources, contextualizer = stack(tmp_path)
    root = sources.ingest(source(), topic="recovery")
    node_id = evidence_ids(retrieval, root)[0]

    async def generate(_payload):
        return {"C0": "RareContextToken checkpoint recovery context."}

    asyncio.run(
        contextualizer.contextualize([node_id], generate)
    )
    hits = retrieval.search(
        "RareContextToken",
        node_types=["evidence"],
        limit=4,
    )
    assert [row["node_id"] for row in hits] == [node_id]
