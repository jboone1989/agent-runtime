import asyncio
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from agent_runtime.memory.archive import EvidenceArchive
from agent_runtime.memory.capability import MemoryCapabilityProvider
from agent_runtime.capabilities.protocol import CapabilityCall
from agent_runtime.capabilities.standard import MEMORY_CAPABILITY_ID
from agent_runtime.memory.documents import DocumentHierarchy
from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.index_worker import ContentIndexWorker
from agent_runtime.memory.query_service import MemoryIndexBehind, MemoryQueryService
from agent_runtime.memory.revisions import now
from agent_runtime.memory.session import MemorySession, MemoryToolInputError
from agent_runtime.memory.topics import TopicHierarchy
from agent_runtime.memory.worker_process import run_worker
from test_topic_hierarchy import SCOPE, adopt


@pytest.fixture
def system(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    documents = DocumentHierarchy(store, SCOPE, chunk_sizes=(128, 64), overlap=4)
    topics = TopicHierarchy(store, SCOPE)
    projection = SimpleNamespace(scope=SCOPE, key="fixture", dimensions=2, embedding_model_id="test/1",
        progress=lambda: {"indexed_through": 0, "provider_version": "fixture/1"}, search=lambda *a, **kw: [])
    service = MemoryQueryService(store, SCOPE, content=projection, documents=documents, topics=topics, page_chars=13)
    return store, service


def evidence(store, service, rev="v1", body="原文保持精确空格  与换行。\n第二段含日期和限定条件。", time="2025-01-01T00:00:00Z", adopt_it=True):
    service.archive.propose(claim_id="source", revision_id=rev, body=body,
        record={"text": "a short navigation title", "recorded_at": time, "topics": ["ai"]})
    if adopt_it:
        store.record_adoption({"decision_id": "d:" + rev, "revision_id": rev,
            "reason": "source observation", "status": "ACTIVE", "recorded_at": time})
    return body


def test_archive_then_proposal_and_exact_paged_original(system):
    store, service = system
    body = evidence(store, service, adopt_it=False)
    with pytest.raises(KeyError):
        service.read("source")
    store.record_adoption({"decision_id": "d:v1", "revision_id": "v1", "reason": "test", "status": "ACTIVE"})
    pages, offset = [], 0
    while True:
        page = service.read("source", offset=offset)
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(pages) == body
    assert service.read("source")["view"] == "EVIDENCE_ORIGINAL"
    assert service.read("source")["source_spans"] == [{"ref": "source", "start": 0, "end": 13}]
    archive = next(service.archive.root.glob("*.json"))
    archive.write_text(archive.read_text().replace("第二段", "伪造段"))
    with pytest.raises(RuntimeError, match="integrity"):
        service.read("source")


def test_original_indexing_recovery_and_canonical_validation(system):
    store, service = system
    body = evidence(store, service)
    writes, calls = [], []
    def write(fragments, vectors, *, adoption_seq):
        writes.append((fragments, vectors, adoption_seq))
        if len(writes) == 1:
            raise ConnectionError("committed but acknowledgement lost")
    service.content.write_adoption = write
    async def embed(texts):
        calls.append(texts)
        return [[0.6, 0.8] for _ in texts]
    worker = ContentIndexWorker(store, service.content, service.documents, embed)
    with pytest.raises(ConnectionError):
        asyncio.run(worker.process_one())
    async def forbidden(texts):
        pytest.fail("recovery called model")
    worker = ContentIndexWorker(store, service.content, service.documents, forbidden)
    assert asyncio.run(worker.process_one())
    assert writes[0] == writes[1] and len(calls) == 1
    f = writes[0][0][0]
    assert f.body == body and f.body != store.read_claim("source", **asdict(SCOPE))["text"]
    candidate = {"canonical_id": "source", "revision_id": "v1", "node_id": f.fragment_id,
        "scope": asdict(SCOPE), "valid_at": now(), "known_at": now(),
        "source_spans": f.source_spans, "excerpt": f.body[:800],
        "provider_version": "fixture/1", "indexed_through": 1, "ranking_reason": "test"}
    card = service.normalize_content(candidate)
    assert card["text"] == body and card["view"] == "CONTENT_FRAGMENT"
    assert service.normalize_content({**candidate, "scope": {**asdict(SCOPE), "agent_id": "other"}}) is None
    with pytest.raises(RuntimeError, match="immutable original"):
        service.normalize_content({**candidate, "excerpt": "forged"})
    old_chunk = card["node_id"]
    old_version = service.snapshot(old_chunk)["version"]
    evidence(store, service, rev="v2", body="new original", time="2025-02-01T00:00:00Z")
    stale = service.snapshot(old_chunk)
    assert stale["needs_refresh"] and stale["text"] == "" and stale["version"] != old_version
    with pytest.raises(MemoryToolInputError):
        service.read(old_chunk)
    assert service.normalize_content(candidate) is None


def test_queries_report_lag_and_do_not_parse_or_index(system, monkeypatch):
    store, service = system
    evidence(store, service)
    monkeypatch.setattr(service.documents, "parse", lambda *a, **kw: pytest.fail("query parsed document"))
    with pytest.raises(MemoryIndexBehind):
        service.search("原文")
    with pytest.raises(MemoryIndexBehind):
        service.expand("source")
    assert service.read("source")["text"].startswith("原文")
    assert service.changes()[0]["change_seq"] == 1


def test_topic_members_and_session_detect_summary_changes(system, tmp_path):
    store, service = system
    adopt(store, "claim", "v1")
    service.topics.update()
    service.topics.save_summary(service.topics.summary_input("ai"), "navigation summary",
                                generator={"name": "test", "version": "1"})
    topic = service.snapshot("topic:ai")
    assert not topic["needs_refresh"]
    assert service.expand("topic:ai")[0]["canonical_id"] == "claim"
    session = MemorySession(tmp_path / "task", "test", service)
    session.state["cards"] = session.cards([topic])
    versions = session.versions()
    adopt(store, "claim", "v2", time="2025-02-01T00:00:00Z")
    assert versions != session.versions()
    assert service.snapshot("topic:ai")["text"] == ""
    with pytest.raises(MemoryToolInputError):
        service.read("topic:ai")


def test_capability_subject_write_policy_and_read_only_health(system):
    store, service = system
    evidence(store, service)
    provider = MemoryCapabilityProvider(service, subject_id="ferro")
    def call(op, payload=None, subject="ferro"):
        envelope = CapabilityCall.create(subject_id=subject, capability_id=MEMORY_CAPABILITY_ID,
                                         operation=op, payload=payload).to_mapping()
        return provider.invoke(MEMORY_CAPABILITY_ID, envelope)
    assert call("read", {"memory_id": "source"})["ok"]
    assert call("read", {"memory_id": "source"}, subject="other")["error_code"] == "SUBJECT_MISMATCH"
    assert call("remember", {"record": {}})["error_code"] == "WRITE_POLICY_REQUIRED"
    assert call("health")["payload"]["progress"]["content"]["needs_update"]
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM document_hierarchy_artifacts").fetchone()[0] == 0


def test_scheduled_worker_records_failure_then_independent_recovery(tmp_path):
    class Worker:
        count = 0
        async def run_once(self):
            self.count += 1
            if self.count == 1:
                raise ConnectionError("provider unreachable")
            return {"content_updated": True}
    path = tmp_path / "worker.jsonl"
    asyncio.run(run_worker(Worker(), receipt_path=path, interval=.001, max_cycles=2))
    receipts = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["status"] for r in receipts] == ["FAILED", "DONE"]
    assert receipts[1]["progress"]["content_updated"]


def test_graph_callback_cannot_replace_adopted_relation(system):
    from test_graph_projection import revision
    store, service = system
    revision(store, scope=SCOPE)
    with pytest.raises(RuntimeError, match="Graphiti query provider"):
        service.relations("claim")
    service.graph_reader = lambda *a, **kw: [{"canonical_id": "claim", "revision_id": "claim:v1",
        "scope": asdict(SCOPE), "relation": {"relation_id": "r", "fact": "forged"},
        "provider_version": "graphiti/fixture", "indexed_through": 1, "ranking_reason": "test"}]
    card = service.relations("claim")[0]
    assert card["node_id"] == "claim" and card["view"] == "GRAPH_RELATION"
    assert card["relation"]["fact"] == "甲认识乙"
    assert service.read("claim")["view"] == "CANONICAL_RECORD"
