from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.retrieval import DurableMemoryRetrieval
from agent_runtime.memory.sources import DurableEvidenceSources


def source(text="The worker uses a checkpoint to restore state after a restart."):
    return {
        "evidence_id": "source-1",
        "title": "Checkpoint code",
        "summary": text,
        "evidence_text": text,
        "url": "https://example.com/code",
    }


def stack(tmp_path):
    store = CanonicalMemoryStore(
        tmp_path,
        default_subject_id="agent:test",
    )
    retrieval = DurableMemoryRetrieval(store)
    store.set_transition_hook(retrieval.canonical_transition_hook)
    sources = DurableEvidenceSources(store, retrieval)
    return store, retrieval, sources


def test_source_ingest_archives_exact_original_and_builds_chunks(tmp_path):
    _store, retrieval, sources = stack(tmp_path)
    raw = source()

    root = sources.ingest(raw, topic="recovery")

    current = sources.current("source-1")
    assert current is not None
    archived = json.loads(Path(current["archive"]).read_text())
    assert archived == raw
    assert retrieval.node(root)["metadata"]["source_version"] == current["source_version"]
    chunks = retrieval.expand(root)
    assert chunks
    assert retrieval.read(chunks[0]["node_id"])["text"] in raw["evidence_text"]


def test_new_source_revision_disputes_memories_derived_from_old_evidence(tmp_path):
    store, retrieval, sources = stack(tmp_path)
    first = source()
    sources.ingest(
        first,
        topic="recovery",
        observed_at="2026-10-01T00:00:00+00:00",
    )
    store.append_memory({
        "memory_id": "fact",
        "memory_type": "SEMANTIC",
        "kind": "RESEARCH_OBSERVATION",
        "text": "The worker restores from checkpoint.",
        "topics": ["recovery"],
        "evidence_refs": ["source-1"],
    })
    retrieval.sync()

    changed = source(
        "The worker no longer uses a checkpoint and cannot restore after restart."
    )
    sources.ingest(
        changed,
        topic="recovery",
        observed_at="2026-10-02T00:00:00+00:00",
    )

    assert store.get_memory("fact")["status"] == "DISPUTED"
    assert retrieval.node("fact")["status"] == "DISPUTED"
    assert sources.read_original("source-1")["evidence_text"] == changed["evidence_text"]


def test_older_or_inactive_revision_cannot_roll_back_active_pointer(tmp_path):
    _store, _retrieval, sources = stack(tmp_path)
    old = source("Old source body with enough text for an evidence chunk.")
    new = source("New source body with enough text for an evidence chunk.")

    sources.ingest(
        new,
        topic="recovery",
        observed_at="2026-10-03T00:00:00+00:00",
    )
    pointer = dict(sources.current("source-1"))

    sources.ingest(
        old,
        topic="recovery",
        activate=False,
        observed_at="2026-10-04T00:00:00+00:00",
    )
    assert sources.current("source-1")["source_version"] == pointer["source_version"]

    sources.ingest(
        old,
        topic="recovery",
        observed_at="2026-10-01T00:00:00+00:00",
    )
    assert sources.current("source-1")["source_version"] == pointer["source_version"]


def test_source_views_rebuild_from_archived_originals(tmp_path):
    store, retrieval, sources = stack(tmp_path)
    raw = source()
    root = sources.ingest(raw, topic="recovery")
    current = dict(sources.current("source-1"))

    retrieval.clear_canonical_views()
    with store._connect() as db:
        assert db.execute(
            "SELECT 1 FROM memory_nodes WHERE node_id=?",
            (root,),
        ).fetchone() is None

    rebuilt = sources.rebuild_source_views()

    assert rebuilt == 1
    assert retrieval.node(root)["status"] == "ACTIVE"
    assert sources.current("source-1")["archive"] == current["archive"]
    assert sources.read_original("source-1") == raw
