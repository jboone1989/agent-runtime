from __future__ import annotations

import json
import sqlite3

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.revisions import canonical_time


def proposal(store, revision_id="r1", **changes):
    record = {
        "revision_id": revision_id, "claim_id": "claim:rate",
        "text": "The rate is 4%", "kind": "SOURCE_CLAIM",
        "recorded_at": "2026-09-02T00:00:00Z",
        "valid_from": "2026-09-01T00:00:00Z",
        "evidence_refs": ["evidence:1"],
        "generator": {"name": "test-extractor", "version": "1"},
    }
    record.update(changes)
    store.propose_revision(record)
    return record


def adopt(store, revision_id="r1", decision_id="d1", **changes):
    decision = {
        "revision_id": revision_id, "decision_id": decision_id,
        "status": "ACTIVE", "reason": "source evidence accepted",
        "recorded_at": "2026-09-03T00:00:00Z",
        "evidence_refs": ["evidence:1"],
    }
    decision.update(changes)
    return store.record_adoption(decision)


def test_proposal_is_not_an_adopted_fact(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    assert store.get_memory("claim:rate") is None
    assert store.read_claim("claim:rate") is None
    assert store.revision_history("claim:rate")[0]["adoptions"] == []
    adopt(store)
    assert store.get_memory("claim:rate")["text"] == "The rate is 4%"
    assert store.read_claim("claim:rate")["revision_id"] == "r1"


def test_known_at_prevents_future_knowledge_and_valid_at_selects_world_time(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    adopt(store)
    assert store.read_claim("claim:rate", known_at="2026-09-02T23:59:59Z") is None
    assert store.read_claim("claim:rate", known_at="2026-09-03T00:00:00Z", valid_at="2026-08-31T23:59:59Z") is None
    assert store.read_claim("claim:rate", known_at="2026-09-03T00:00:00Z", valid_at="2026-09-01T00:00:00Z")["revision_id"] == "r1"
    proposal(store, "r2", text="Correction: the rate was 5%", recorded_at="2026-09-10T00:00:00Z")
    adopt(store, "r2", "d2", recorded_at="2026-09-11T00:00:00Z")
    assert store.read_claim("claim:rate", valid_at="2026-09-01T00:00:00Z", known_at="2026-09-09T00:00:00Z")["revision_id"] == "r1"
    assert store.read_claim("claim:rate", valid_at="2026-09-01T00:00:00Z")["revision_id"] == "r2"


def test_disjoint_world_intervals_and_unknown_validity(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store, valid_to="2026-09-10T00:00:00Z")
    adopt(store)
    proposal(store, "r2", recorded_at="2026-09-10T00:00:00Z", valid_from="2026-09-10T00:00:00Z", text="New rate")
    adopt(store, "r2", "d2", recorded_at="2026-09-10T00:00:00Z")
    assert store.read_claim("claim:rate", valid_at="2026-09-09T23:59:59Z")["revision_id"] == "r1"
    assert store.read_claim("claim:rate", valid_at="2026-09-10T00:00:00Z")["revision_id"] == "r2"
    proposal(store, "r3", claim_id="unknown", valid_from=None)
    adopt(store, "r3", "d3")
    assert store.read_claim("unknown", valid_at="2020-01-01T00:00:00Z")["valid_from"] is None


def test_status_filter_cannot_resurrect_withdrawn_revision(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    adopt(store)
    adopt(store, decision_id="d2", status="RETRACTED", recorded_at="2026-09-04T00:00:00Z")
    assert store.query_claims(statuses=["ACTIVE"]) == []
    assert store.read_claim("claim:rate")["status"] == "RETRACTED"
    assert store.read_claim("claim:rate", known_at="2026-09-03T12:00:00Z")["status"] == "ACTIVE"


def test_exact_retries_and_id_collisions(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    record = proposal(store)
    store.propose_revision(record)
    assert adopt(store) == "r1"
    assert adopt(store) == "r1"
    assert len(store.events()) == 1
    assert len(store.revision_history("claim:rate")) == 1
    with pytest.raises(ValueError, match="different content"):
        proposal(store, text="changed")
    with pytest.raises(ValueError, match="different content"):
        adopt(store, status="DISPUTED")


def test_retries_without_caller_time_survive_reopen(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    record = {"revision_id": "r", "claim_id": "claim", "text": "A fact"}
    decision = {"decision_id": "d", "revision_id": "r", "status": "ACTIVE", "reason": "accepted"}
    store.propose_revision(record)
    store.record_adoption(decision)
    reopened = CanonicalMemoryStore(tmp_path)
    reopened.propose_revision(record)
    reopened.record_adoption(decision)
    assert len(reopened.events()) == 1


def test_adoption_and_projection_hook_are_atomic(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    def fail(db, memory_id, status):
        raise RuntimeError("index failed")
    store.set_transition_hook(fail)
    with pytest.raises(RuntimeError, match="index failed"):
        adopt(store)
    assert store.read_claim("claim:rate") is None
    assert store.get_memory("claim:rate") is None
    assert store.events() == []
    assert store.revision_history("claim:rate")[0]["adoptions"] == []
    store.set_transition_hook(None)
    adopt(store)
    assert len(store.events()) == 1


def test_existing_calls_record_state_history(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({"memory_id": "legacy-call", "text": "fact"})
    store.transition_memory("legacy-call", status="DISPUTED")
    history = store.revision_history("legacy-call")
    assert len(history) == 2
    assert history[0]["status"] == "ACTIVE"
    assert history[1]["status"] == "DISPUTED"
    assert store.read_claim("legacy-call")["status"] == "DISPUTED"


@pytest.mark.parametrize("table", ["memory_revisions", "memory_adoptions"])
def test_revision_and_adoption_ledgers_are_immutable(tmp_path, table):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    adopt(store)
    with store._connect() as db:
        for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET recorded_at='2020'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


def test_scope_is_applied_before_read_and_limit(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    adopt(store)
    proposal(store, "other", claim_id="private-other", subject_id="other-agent")
    adopt(store, "other", "d2")
    assert store.read_claim("private-other", subject_id="ferro") is None
    assert store.read_claim("claim:rate", access_scope="SHARED") is None
    assert store.query_claims(subject_id="ferro", limit=1)[0]["claim_id"] == "claim:rate"
    with pytest.raises(ValueError, match="cannot change"):
        proposal(store, "r2", subject_id="other-agent")


def test_legacy_upgrade_preserves_rows_and_ledger_and_is_incremental(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({"memory_id": "old", "text": "old evidence", "created_at": "2020-01-01T00:00:00Z"})
    # Strip just the extension to recreate the exact v1 schema/data.
    with store._connect() as db:
        before_memories = [tuple(r) for r in db.execute("SELECT * FROM memories")]
        before_events = [tuple(r) for r in db.execute("SELECT * FROM memory_events")]
        db.execute("DROP TABLE memory_adoptions")
        db.execute("DROP TABLE memory_revisions")
        db.execute("DELETE FROM schema_meta WHERE key LIKE 'canonical_%'")
    migrated = CanonicalMemoryStore(tmp_path)
    with migrated._connect() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM memories")] == before_memories
        assert [tuple(r) for r in db.execute("SELECT * FROM memory_events")] == before_events
    assert migrated.read_claim("old")["history_origin"] == "LEGACY_SNAPSHOT"
    assert migrated.read_claim("old", known_at="2020-01-02T00:00:00Z") is None
    reopened = CanonicalMemoryStore(tmp_path)
    assert len(reopened.revision_history("old")) == 1
    reopened.append_memory({"memory_id": "new", "text": "new evidence"})
    assert len(reopened.revision_history("new")) == 1


def test_complete_text_and_topic_projection(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store, text="a" * 14000, topics=["rate"], entities=["bank"], valid_from=None)
    adopt(store)
    assert len(store.read_claim("claim:rate")["text"]) == 14000
    assert len(store.get_memory("claim:rate")["text"]) == 14000
    assert store.query_memories(topic="rate")[0]["memory_id"] == "claim:rate"
    proposal(store, "r2", text="changed", topics=["new"], recorded_at="2026-09-04T00:00:00Z")
    adopt(store, "r2", "d2", recorded_at="2026-09-05T00:00:00Z")
    assert store.query_memories(topic="rate") == []
    assert store.query_memories(topic="new")[0]["text"] == "changed"


def test_transition_preserves_complete_v2_text_and_unknown_validity(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store, text="b" * 14000, valid_from=None)
    adopt(store)
    store.transition_memory("claim:rate", status="DISPUTED")
    saved = store.read_claim("claim:rate")
    assert len(saved["text"]) == 14000
    assert saved["valid_from"] is None
    assert saved["observed_at"] is None


def test_v2_rejects_invalid_values_instead_of_clipping(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    with pytest.raises(ValueError, match="importance"):
        proposal(store, importance=2)
    with pytest.raises(ValueError, match="confidence"):
        proposal(store, confidence=-1)
    with pytest.raises(ValueError, match="evidence_refs"):
        proposal(store, evidence_refs="bad")


def test_tenant_and_owner_boundaries_apply_to_revision_reads(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_tenant_id="tenant-a", default_owner_id="owner-a")
    proposal(store)
    adopt(store)
    assert store.read_claim("claim:rate")["tenant_id"] == "tenant-a"
    assert store.read_claim("claim:rate", tenant_id="tenant-b") is None
    assert store.revision_history("claim:rate", owner_id="owner-b") == []
    with pytest.raises(ValueError, match="cannot change"):
        proposal(store, "r2", tenant_id="tenant-b")


def test_time_validation_and_no_backdated_adoption(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    proposal(store)
    with pytest.raises(ValueError, match="precede"):
        adopt(store, recorded_at="2026-09-01T00:00:00Z")
    adopt(store)
    with pytest.raises(ValueError, match="backwards"):
        adopt(store, decision_id="d2", recorded_at="2026-09-02T12:00:00Z")
    with pytest.raises(ValueError, match="later"):
        proposal(store, "bad", valid_to="2026-08-01T00:00:00Z")
    with pytest.raises(ValueError, match="timezone"):
        canonical_time("2026-09-01T00:00:00")
    assert canonical_time("2026-09-01T08:00:00+08:00") == "2026-09-01T00:00:00.000000Z"
    with pytest.raises(ValueError, match="future"):
        proposal(store, "future", recorded_at="2099-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="future"):
        adopt(store, decision_id="future", recorded_at="2099-01-01T00:00:00Z")
