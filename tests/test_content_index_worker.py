import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.documents import DocumentHierarchy
from agent_runtime.memory.index_worker import ContentIndexWorker, MemoryIndexWorker
from agent_runtime.memory.postgres_retrieval import ChineseLexical, ContentFragment, validate_vector
from agent_runtime.memory.projection_scope import ProjectionScope
from test_topic_hierarchy import adopt

SCOPE = ProjectionScope("t", "o", "a", "PRIVATE_OWNER")


def test_write_then_disconnect_replays_saved_derivation_without_models(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    adopt(store, "claim", "r1")
    documents = DocumentHierarchy(store, SCOPE, chunk_sizes=(128, 64), overlap=4)
    class Projection:
        scope = SCOPE
        key = "fixture-projection"
        embedding_model_id = "fixture-embedder/1"
        dimensions = 2
        writes = []
        fail = True
        def write_adoption(self, fragments, vectors, *, adoption_seq):
            self.writes.append([fragments, vectors, adoption_seq])
            if self.fail:
                self.fail = False
                raise ConnectionError("lost acknowledgement after commit")
            assert self.writes[-1] == self.writes[0]
    model_calls = []
    async def embedder(texts):
        model_calls.append(texts)
        return [[0.6, 0.8] for _ in texts]
    projection = Projection()
    worker = ContentIndexWorker(store, projection, documents, embedder)
    with pytest.raises(ConnectionError):
        asyncio.run(worker.process_one())
    assert worker.progress()["indexed_through"] == 0
    assert worker.progress()["pending"][0]["status"] == "FAILED"
    async def forbidden(texts):
        pytest.fail("recovery repeated an embedding model call")
    reopened = ContentIndexWorker(store, projection, documents, forbidden)
    assert asyncio.run(reopened.process_one())
    assert not asyncio.run(reopened.process_one())
    assert len(model_calls) == 1
    assert reopened.progress()["pending"] == []
    assert reopened.progress()["indexed_through"] == 1


def test_provider_failure_does_not_stop_independent_index_updates():
    completed = []
    class Broken:
        async def process_one(self):
            raise ConnectionError("graph down")
    graph = SimpleNamespace(ledger=SimpleNamespace(discover=lambda **kw: None,
                  process_one=lambda sink: Broken().process_one()))
    class Topics:
        def update(self, **kw):
            completed.append("topic")
            return 1
        def progress(self):
            return {"needs_update": False}
    class Content:
        async def process_one(self):
            completed.append("content")
            return True
    with pytest.raises(ExceptionGroup) as error:
        asyncio.run(MemoryIndexWorker(graph=graph, topics=Topics(), content=Content()).run_once())
    assert completed == ["topic", "content"]
    assert isinstance(error.value.exceptions[0], ConnectionError)


def test_chinese_search_terms_are_segmented_without_joining_separate_phrases():
    tokenize = ChineseLexical()
    assert "记忆" in tokenize("长期智能体记忆系统")
    assert "bitcoin" in tokenize("Bitcoin 与 AI")
    assert "密货" not in tokenize("加密，货币")


@pytest.mark.parametrize("vector", [[0, 0], [1], [True, 1], [float("nan"), 1], [float("inf"), 1]])
def test_invalid_vectors_fail_before_network_write(vector):
    with pytest.raises(ValueError):
        validate_vector(vector, 2)


def test_fragments_require_original_provenance_and_valid_time_bounds():
    base = dict(canonical_id="e", revision_id="v", fragment_id="f", body="text", kind="EVIDENCE",
                source_spans=[{"ref": "e", "start": 0, "end": 4}], known_from="2025-01-01T00:00:00Z")
    assert ContentFragment(**base).canonical_id == "e"
    with pytest.raises(ValueError):
        ContentFragment(**{**base, "source_spans": []})
    with pytest.raises(ValueError):
        ContentFragment(**base, valid_from="2025-02-01T00:00:00Z", valid_to="2025-01-01T00:00:00Z")
