from __future__ import annotations

import sqlite3

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore


def test_durable_store_is_idempotent_and_persists_metadata(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    row = {
        "memory_id": "memory:1",
        "memory_type": "SEMANTIC",
        "kind": "FACT",
        "text": "A checkpoint restores worker state.",
        "topics": ["recovery"],
        "entities": ["worker"],
    }

    assert store.append_memory(row) is True
    assert store.append_memory(row) is False

    restored = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    saved = restored.get_memory("memory:1")
    assert saved is not None
    assert saved["subject_id"] == "agent:test"
    assert saved["topics"] == ["recovery"]
    assert saved["entities"] == ["worker"]
    assert restored.events()[0]["event_type"] == "MEMORY_CREATED"


def test_durable_store_keeps_ferro_default_for_existing_state_compatibility(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "legacy",
        "kind": "FACT",
        "text": "Existing Ferro state keeps its historical default subject.",
    })
    assert store.get_memory("legacy")["subject_id"] == "ferro"


def test_duplicate_id_with_different_content_is_rejected(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    store.append_memory({
        "memory_id": "stable",
        "kind": "FACT",
        "text": "Stable fact",
    })
    with pytest.raises(ValueError, match="different content"):
        store.append_memory({
            "memory_id": "stable",
            "kind": "FACT",
            "text": "Changed fact",
        })


def test_event_ledger_is_append_only(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="agent:test")
    store.append_memory({
        "memory_id": "event",
        "kind": "FACT",
        "text": "Append only",
    })
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE memory_events SET event_type='CHANGED'")
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM memory_events")
    connection.close()


def test_transition_hook_updates_derived_view_in_same_transaction(tmp_path):
    calls = []

    def on_transition(db, memory_id, status):
        calls.append((memory_id, status))
        db.execute(
            "UPDATE derived_memory_view SET status=? WHERE memory_id=?",
            (status, memory_id),
        )

    store = CanonicalMemoryStore(
        tmp_path,
        default_subject_id="agent:test",
        on_transition=on_transition,
    )
    store.append_memory({
        "memory_id": "fact",
        "kind": "FACT",
        "text": "A fact with a derived view.",
    })
    with store._connect() as db:
        db.execute(
            "CREATE TABLE derived_memory_view(memory_id TEXT PRIMARY KEY, status TEXT)"
        )
        db.execute(
            "INSERT INTO derived_memory_view(memory_id,status) VALUES('fact','ACTIVE')"
        )

    result = store.transition_memory("fact", status="RETRACTED")

    assert result["status"] == "RETRACTED"
    assert calls == [("fact", "RETRACTED")]
    with store._connect() as db:
        view = db.execute(
            "SELECT status FROM derived_memory_view WHERE memory_id='fact'"
        ).fetchone()
    assert view["status"] == "RETRACTED"


def test_transition_hook_failure_rolls_back_canonical_transition(tmp_path):
    def fail(_db, _memory_id, _status):
        raise RuntimeError("derived view failed")

    store = CanonicalMemoryStore(
        tmp_path,
        default_subject_id="agent:test",
        on_transition=fail,
    )
    store.append_memory({
        "memory_id": "fact",
        "kind": "FACT",
        "text": "Transition must be atomic with derived invalidation.",
    })
    before_events = len(store.events())

    with pytest.raises(RuntimeError, match="derived view failed"):
        store.transition_memory("fact", status="RETRACTED")

    assert store.get_memory("fact")["status"] == "ACTIVE"
    assert len(store.events()) == before_events
