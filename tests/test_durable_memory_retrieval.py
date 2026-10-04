from __future__ import annotations

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.retrieval import (
    DurableMemoryRetrieval,
    memory_document,
)


def _seed(store):
    store.append_memory({
        "memory_id": "checkpoint",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "A checkpoint restores worker state after restart.",
        "topics": ["recovery"],
        "entities": ["worker"],
        "payload": {"lesson": "persist before restart"},
    })
    store.append_memory({
        "memory_id": "payment",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "Payment reconciliation needs an idempotency key.",
        "topics": ["payments"],
        "entities": ["payment"],
    })


def test_retrieval_builds_rebuildable_views_from_canonical_store(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    _seed(store)
    retrieval = DurableMemoryRetrieval(store)

    result = retrieval.search("checkpoint restart", limit=3)

    assert result
    assert result[0]["node_id"] == "checkpoint"
    assert result[0]["type"] == "memory"
    assert retrieval.read("checkpoint")["text"].startswith("A checkpoint")


def test_topic_fallback_keeps_relevant_memory_visible_without_lexical_overlap(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    _seed(store)
    retrieval = DurableMemoryRetrieval(store)

    result = retrieval.search("unrelated-token", topic="payments", limit=3)

    assert [row["node_id"] for row in result] == ["payment"]


def test_dense_search_is_injected_not_owned_by_runtime(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    _seed(store)
    retrieval = DurableMemoryRetrieval(store)
    calls = []

    def dense(query, *, filter_sql, filter_params, limit):
        calls.append((query, filter_sql, filter_params, limit))
        return [("payment", 0.99)]

    result = retrieval.search(
        "semantic-only-query",
        limit=2,
        dense_search=dense,
    )

    assert result[0]["node_id"] == "payment"
    assert calls and calls[0][0] == "semantic-only-query"
    assert calls[0][3] == 32


def test_transition_hook_invalidates_derived_ancestors(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    _seed(store)
    retrieval = DurableMemoryRetrieval(store)
    store.set_transition_hook(retrieval.canonical_transition_hook)
    retrieval.put_node(
        "summary",
        "summary",
        "Recovery summary",
        {"topics": ["recovery"]},
        children=["checkpoint"],
    )

    store.transition_memory("checkpoint", status="RETRACTED")

    assert retrieval.node("checkpoint")["status"] == "RETRACTED"
    assert retrieval.node("summary")["status"] == "DIRTY"
    assert any(
        row["node_id"] == "summary" and row["event_status"] == "DIRTY"
        for row in retrieval.changes(0, limit=100)
    )


def test_clear_views_never_deletes_canonical_memory(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    _seed(store)
    retrieval = DurableMemoryRetrieval(store)

    retrieval.clear_canonical_views()

    assert store.get_memory("checkpoint") is not None
    assert retrieval.search("checkpoint")[0]["node_id"] == "checkpoint"


def test_memory_document_projects_useful_payload_without_becoming_canonical():
    document = memory_document({
        "text": "Observed behavior",
        "kind": "EPISODE",
        "topics": ["recovery"],
        "entities": ["worker"],
        "payload": {
            "prediction": "worker resumes",
            "outcome": "worker resumed",
            "observations": [{"text": "checkpoint loaded"}],
        },
    })

    assert "Observed behavior" in document
    assert "worker resumes" in document
    assert "checkpoint loaded" in document
