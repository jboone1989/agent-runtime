import asyncio
from dataclasses import asdict
from types import SimpleNamespace
import sqlite3

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.graph_projection import (
    GraphScope, GraphProjectionLedger, GraphitiAdoptedProjection,
    adopted_relations, graph_uuid, create_graphiti,
)

SCOPE = GraphScope("tenant-a", "owner-a", "agent-a", "PRIVATE_OWNER")


def relation(fact="甲认识乙"):
    return {"relation_id": "r", "source": {"entity_id": "a", "name": "甲"},
            "target": {"entity_id": "b", "name": "乙"}, "predicate": "KNOWS", "fact": fact,
            "assertion_type": "SOURCE_CLAIM", "source_spans": [{"ref": "evidence:1", "start": 0, "end": 5}]}


def revision(store, *, scope=SCOPE, version="v1", claim="claim", recorded="2025-01-01T00:00:00Z", payload=None, adopt=True):
    rid = claim + ":" + version
    store.propose_revision({"revision_id": rid, "claim_id": claim, "text": "甲认识乙，来源为证据一。",
                            "subject_id": scope.agent_id, **asdict(scope), "recorded_at": recorded,
                            "valid_from": "2024-01-01T00:00:00Z", "source_refs": ["evidence:1"],
                            "payload": payload if payload is not None else {"graph_relations": [relation()]}})
    if adopt:
        store.record_adoption({"decision_id": rid + ":adopt", "revision_id": rid, "status": "ACTIVE",
                               "reason": "test adoption", "recorded_at": recorded})
    return rid


@pytest.fixture
def store(tmp_path):
    return CanonicalMemoryStore(tmp_path)


def edge(revision_id="claim:v1", scope=SCOPE, **attributes):
    return SimpleNamespace(group_id=scope.group_id,
                           attributes={"canonical_claim_id": "claim", "canonical_revision_id": revision_id,
                                       "canonical_relation_id": "r", **attributes})


def projection(store, scope=SCOPE, generation="v1"):
    ledger = GraphProjectionLedger(store, scope, generation=generation)
    return GraphitiAdoptedProjection(store, SimpleNamespace(driver=None), ledger)


def test_scope_is_explicit_and_provider_uuid_stable():
    with pytest.raises(ValueError):
        GraphScope("", "owner", "agent", "PRIVATE_OWNER")
    assert graph_uuid("entity", SCOPE, "a") == graph_uuid("entity", SCOPE, "a")
    other = GraphScope("tenant-a", "owner-a", "agent-b", "PRIVATE_OWNER")
    assert graph_uuid("entity", SCOPE, "a") != graph_uuid("entity", other, "a")


def test_unadopted_and_foreign_rows_never_enter_queue(store):
    revision(store, adopt=False)
    revision(store, claim="foreign", scope=GraphScope("tenant-b", "owner-a", "agent-a", "PRIVATE_OWNER"))
    revision(store, claim="different-agent", scope=GraphScope("tenant-a", "owner-a", "agent-b", "PRIVATE_OWNER"))
    revision(store, claim="shared", scope=GraphScope("tenant-a", "owner-a", "agent-a", "SHARED"))
    p = projection(store)
    assert p.ledger.discover() == 0
    assert p.normalize(edge()) is None


def test_scope_filter_applies_before_artifact_decode(store):
    revision(store)
    revision(store, claim="foreign", scope=GraphScope("tenant-b", "owner-b", "agent-b", "PRIVATE_OWNER"),
             payload={"graph_relations": [{"invalid_foreign_structure": True}]})
    p = projection(store)
    assert p.ledger.discover() == 1
    with p.ledger.connect() as db:
        job = db.execute("SELECT * FROM graph_projection_jobs").fetchone()
    artifact = p.ledger.load_artifact(job)
    assert artifact["claim_id"] == "claim"


def test_bitemporal_read_and_status_are_canonical(store):
    revision(store)
    revision(store, version="v2", recorded="2025-02-01T00:00:00Z",
             payload={"graph_relations": [relation("甲不再认识乙")]})
    p = projection(store)
    assert p.normalize(edge()) is None
    old = p.normalize(edge(), known_at="2025-01-15T00:00:00Z", valid_at="2024-02-01T00:00:00Z")
    assert old["revision_id"] == "claim:v1"
    assert "uuid" not in old
    assert p.normalize(edge("claim:v2"), known_at="2025-01-15T00:00:00Z") is None
    assert p.normalize(edge("claim:v2"), valid_at="2023-01-01T00:00:00Z") is None
    store.record_adoption({"decision_id": "retract", "revision_id": "claim:v2", "status": "RETRACTED",
                           "reason": "contradicted", "recorded_at": "2025-03-01T00:00:00Z"})
    assert p.normalize(edge("claim:v2")) is None
    assert p.normalize(edge("claim:v2"), known_at="2025-02-15T00:00:00Z") is not None


def test_graph_mutations_cannot_change_fact_provenance_or_adoption(store):
    revision(store)
    p = projection(store)
    result = p.normalize(edge(fact="伪造事实", assertion_type="OBSERVATION", source_spans_json="[]"))
    assert result["relation"]["assertion_type"] == "SOURCE_CLAIM"
    assert result["relation"]["fact"] == "甲认识乙"
    assert result["relation"]["source_spans"][0]["ref"] == "evidence:1"
    assert p.normalize(edge(scope=GraphScope("tenant-b", "owner-a", "agent-a", "PRIVATE_OWNER"))) is None
    assert p.normalize(edge(canonical_relation_id="unknown")) is None


def test_failed_write_keeps_cursor_and_resume_reuses_artifact(store):
    revision(store)
    p = projection(store)
    p.ledger.discover()
    written = []
    class Crash:
        async def save_artifact(self, artifact, scope):
            written.append(artifact)
            raise ConnectionError("disconnected after write")
    with pytest.raises(ConnectionError):
        asyncio.run(p.ledger.process_one(Crash()))
    assert p.ledger.snapshot()["indexed_through"] == 0
    reopened = projection(store)
    class Success:
        async def save_artifact(self, artifact, scope):
            assert artifact == written[0]
    assert asyncio.run(reopened.ledger.process_one(Success()))
    assert not asyncio.run(reopened.ledger.process_one(Success()))
    assert reopened.ledger.discover() == 0
    assert reopened.ledger.snapshot()["jobs"] == {"DONE": 1}
    with reopened.ledger.connect() as db:
        assert db.execute("SELECT attempts FROM graph_projection_jobs").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM graph_projection_derivations").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE graph_projection_derivations SET artifact_json='{}'")


def test_no_inferred_relations_and_no_default_models(store):
    revision(store, payload={})
    p = projection(store)
    p.ledger.discover()
    class NoRelations:
        async def save_artifact(self, artifact, scope):
            assert artifact["relations"] == []
    assert asyncio.run(p.ledger.process_one(NoRelations()))
    with pytest.raises(ValueError):
        create_graphiti(driver=None, llm_client=None, embedder=None, cross_encoder=None)


@pytest.mark.parametrize("change", [
    {"source_spans": [{"ref": "foreign", "start": 0, "end": 4}]},
    {"source_spans": [{"ref": "evidence:1", "start": 3, "end": 2}]},
    {"source_spans": []}, {"assertion_type": "FACT"}, {"confidence": 2},
    {"confidence": float("nan")}, {"source": {"name": "missing id"}},
])
def test_invalid_adopted_relation_fails_explicitly(change):
    record = {"source_refs": ["evidence:1"], "payload": {"graph_relations": [{**relation(), **change}]}}
    with pytest.raises(ValueError):
        adopted_relations(record)


def test_new_generation_replays_without_modifying_canonical(store):
    revision(store)
    p = projection(store)
    p.ledger.discover()
    artifacts = []
    class Sink:
        async def save_artifact(self, artifact, scope):
            artifacts.append(artifact)
    asyncio.run(p.ledger.process_one(Sink()))
    rebuild = projection(store, generation="rebuild-1")
    rebuild.ledger.discover()
    asyncio.run(rebuild.ledger.process_one(Sink()))
    assert artifacts[0] == artifacts[1]
    assert store.read_claim("claim", **asdict(SCOPE))["revision_id"] == "claim:v1"


def test_vectors_reused_across_retry_and_rebuild_but_scoped(store):
    class Embedder:
        calls = []
        async def create_batch(self, texts):
            self.calls.append(texts)
            return [[0.6, 0.8] for text in texts]
    model = Embedder()
    graph = SimpleNamespace(embedder=model)
    p = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, SCOPE), embedding_model_id="fixture-embedder-v1")
    assert asyncio.run(p.vectors_for(["a", "a", "b"])) == {"a": [0.6, 0.8], "b": [0.6, 0.8]}
    assert model.calls == [["a", "b"]]
    rebuild = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, SCOPE, generation="rebuild-2"), embedding_model_id="fixture-embedder-v1")
    asyncio.run(rebuild.vectors_for(["a", "b"]))
    assert model.calls == [["a", "b"]]
    foreign = GraphScope("another-tenant", SCOPE.owner_id, SCOPE.agent_id, SCOPE.access_scope)
    other = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, foreign), embedding_model_id="fixture-embedder-v1")
    asyncio.run(other.vectors_for(["a"]))
    assert model.calls == [["a", "b"], ["a"]]


@pytest.mark.parametrize("vectors", [[[0, 0]], [[float("nan"), 1]], [[True, 1]], [[1]], [[1, 0], [1, 0, 0]]])
def test_invalid_embedding_batch_is_not_partially_saved(store, vectors):
    class Embedder:
        async def create_batch(self, texts):
            return vectors
    p = GraphitiAdoptedProjection(store, SimpleNamespace(embedder=Embedder()), GraphProjectionLedger(store, SCOPE), embedding_model_id="invalid-model-v1")
    with pytest.raises(ValueError):
        asyncio.run(p.vectors_for(["a", "b"]))
    with p.ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM graph_projection_vectors").fetchone()[0] == 0
