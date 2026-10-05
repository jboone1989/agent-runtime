"""Opt-in tests against an isolated, authenticated Neo4j Community database."""
import asyncio
import json
from dataclasses import asdict
import os
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
os.environ.setdefault("SEMAPHORE_LIMIT", "1")

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.graph_projection import GraphScope, GraphProjectionLedger, GraphitiAdoptedProjection, create_graphiti, graph_uuid
from test_graph_projection import revision, relation

pytestmark = pytest.mark.skipif(not os.getenv("MEMORY_TEST_NEO4J_URI"), reason="isolated Neo4j endpoint not configured")


def clients():
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    class NoLLM(LLMClient):
        async def _generate_response(self, *args, **kwargs):
            raise AssertionError("adopted projection must not call an LLM")
    saved = json.loads(Path(os.environ["MEMORY_TEST_GRAPH_VECTORS"]).read_text())
    class SavedRealEmbedder(EmbedderClient):
        calls = 0
        async def create(self, *args, **kwargs):
            raise AssertionError("projection batches model requests")
        async def create_batch(self, texts):
            self.calls += 1
            return [saved["texts"][text] for text in texts]
    class NoReranker(CrossEncoderClient):
        async def rank(self, *args, **kwargs):
            raise AssertionError("structural replay must not call a reranker")
    return NoLLM(None), SavedRealEmbedder(), NoReranker(), saved["model_id"]


def test_real_sdk_authority_scope_crash_and_rebuild(tmp_path):
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.nodes import Node
    password = Path(os.environ["MEMORY_TEST_NEO4J_PASSWORD_FILE"]).read_text().strip()
    # Construct driver before entering an event loop, avoiding SDK background index creation.
    driver = Neo4jDriver(os.environ["MEMORY_TEST_NEO4J_URI"], "neo4j", password)
    scope = GraphScope("integration:" + uuid4().hex, "owner", "agent", "PRIVATE_OWNER")
    foreign = GraphScope(scope.tenant_id, "owner", "other-agent", "PRIVATE_OWNER")
    store = CanonicalMemoryStore(tmp_path)
    revision(store, scope=scope)
    revision(store, scope=scope, version="v2", recorded="2025-02-01T00:00:00Z",
             payload={"graph_relations": [relation("甲不再认识乙")]})
    revision(store, scope=foreign, claim="foreign")
    llm, embedder, reranker, model_id = clients()
    graph = create_graphiti(driver=driver, llm_client=llm, embedder=embedder, cross_encoder=reranker)
    p = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, scope), embedding_model_id=model_id)
    other = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, foreign), embedding_model_id=model_id)
    with store._connect() as db:
        before = [tuple(r) for r in db.execute("SELECT revision_id,record_hash FROM memory_revisions ORDER BY revision_id")]
        adoptions_before = [tuple(r) for r in db.execute("SELECT decision_id,decision_hash FROM memory_adoptions ORDER BY adoption_seq")]

    async def run():
        async def graph_snapshot():
            rows, _, _ = await driver.execute_query("""MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
                WHERE e.group_id=$group RETURN n.uuid AS source,m.uuid AS target,properties(e) AS edge
                ORDER BY e.uuid""", group=scope.group_id, routing_="r")
            return [dict(r) for r in rows]
        try:
            await graph.build_indices_and_constraints()
            assert p.ledger.discover() == 2
            class CrashAfterWrite:
                async def save_artifact(self, artifact, partition):
                    await p.save_artifact(artifact, partition)
                    raise ConnectionError("crash after graph commit before SQL acknowledgement")
            with pytest.raises(ConnectionError):
                await p.ledger.process_one(CrashAfterWrite())
            assert p.ledger.snapshot()["indexed_through"] == 0
            assert len(await graph_snapshot()) == 1
            embedded_before_retry = embedder.calls
            assert await p.ledger.process_one(p)
            assert embedder.calls == embedded_before_retry
            assert len(await graph_snapshot()) == 1
            assert await p.ledger.process_one(p)
            assert len(await graph_snapshot()) == 2
            assert other.ledger.discover() == 1
            assert await other.ledger.process_one(other)
            latest = await p.follow_relations("a")
            assert len(latest) == 1 and latest[0]["revision_id"] == "claim:v2"
            old = await p.follow_relations("a", known_at="2025-01-15T00:00:00Z", valid_at="2024-06-01T00:00:00Z")
            assert len(old) == 1 and old[0]["revision_id"] == "claim:v1"
            assert all(item["scope"] == asdict(scope) for item in latest + old)
            snapshot = await graph_snapshot()
            await driver.execute_query("""MATCH ()-[e:RELATES_TO]->() WHERE e.group_id=$group
                SET e.invalid_at=datetime('2025-01-01T00:00:00Z'), e.fact='unauthorized graph mutation'""", group=scope.group_id)
            # Graphiti invalidation never changes canonical adoption, including historical reads.
            unchanged = await p.follow_relations("a")
            assert unchanged[0]["relation"]["fact"] == "甲不再认识乙"
            await Node.delete_by_group_id(driver, scope.group_id)
            assert await p.follow_relations("a") == []
            embedded_before_rebuild = embedder.calls
            rebuild = GraphitiAdoptedProjection(store, graph, GraphProjectionLedger(store, scope, generation="rebuild-1"), embedding_model_id=model_id)
            assert rebuild.ledger.discover() == 2
            while await rebuild.ledger.process_one(rebuild):
                pass
            assert await graph_snapshot() == snapshot
            assert embedder.calls == embedded_before_rebuild
            # Deleting one scope leaves the other agent's projection intact.
            assert len(await other.follow_relations("a")) == 1
            store.record_adoption({"decision_id": "retract-v2", "revision_id": "claim:v2", "status": "RETRACTED",
                                   "reason": "test retraction", "recorded_at": "2025-03-01T00:00:00Z"})
            assert await p.follow_relations("a") == []
            assert len(await p.follow_relations("a", known_at="2025-02-15T00:00:00Z")) == 1
            with store._connect() as db:
                assert [tuple(r) for r in db.execute("SELECT revision_id,record_hash FROM memory_revisions ORDER BY revision_id")] == before
                assert [tuple(r) for r in db.execute("SELECT decision_id,decision_hash FROM memory_adoptions ORDER BY adoption_seq LIMIT 3")] == adoptions_before
        finally:
            await Node.delete_by_group_id(driver, scope.group_id)
            await Node.delete_by_group_id(driver, foreign.group_id)
            await graph.close()
    asyncio.run(run())
