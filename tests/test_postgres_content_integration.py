"""Opt-in real PostgreSQL/pgvector tests, using a disposable test schema."""
from dataclasses import asdict
import os
from uuid import uuid4

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.postgres_retrieval import ContentFragment, ChineseLexical, PostgresContentProjection
from agent_runtime.memory.projection_scope import ProjectionScope
from test_topic_hierarchy import adopt

pytestmark = pytest.mark.skipif(not os.getenv("MEMORY_TEST_POSTGRES_DSN"), reason="isolated PostgreSQL endpoint not configured")


@pytest.fixture
def provider(tmp_path):
    import psycopg
    from psycopg import sql
    dsn = os.environ["MEMORY_TEST_POSTGRES_DSN"]
    schema = "memory_test_" + uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    def connect():
        return psycopg.connect(dsn, options="-c search_path=" + schema + ",public")
    scope = ProjectionScope("tenant:" + uuid4().hex, "o", "a", "PRIVATE_OWNER")
    canonical = CanonicalMemoryStore(tmp_path)
    def normalize(candidate):
        record = canonical.read_claim(candidate["canonical_id"], **asdict(scope),
            valid_at=candidate["valid_at"], known_at=candidate["known_at"], statuses=["ACTIVE"])
        return candidate if record and record["revision_id"] == candidate["revision_id"] else None
    projection = PostgresContentProjection(connect, scope, dimensions=2, embedding_model_id="fixture/1",
        lexical=ChineseLexical(), lexical_version=ChineseLexical.version, normalize=normalize)
    try:
        projection.initialize()
        yield canonical, projection, connect
    finally:
        with psycopg.connect(dsn) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def fragment(rev, time, status="ACTIVE", *, claim="c", start=None, end=None):
    return ContentFragment(canonical_id=claim, revision_id=rev, fragment_id="f:" + rev,
        body="长期智能体记忆 " + rev, kind="SOURCE_CLAIM", source_spans=[{"ref": claim, "start": 0, "end": 8}],
        known_from=time, valid_from=start or "2024-01-01T00:00:00Z", valid_to=end, status=status)


def test_real_fts_vector_temporal_retraction_and_identical_replay(provider):
    canonical, p, connect = provider
    t1, t2, t3 = "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z", "2025-03-01T00:00:00Z"
    adopt(canonical, "c", "v1", time=t1, scope=p.scope)
    f1 = fragment("v1", t1)
    assert p.write_adoption([f1], [[0.6, 0.8]], adoption_seq=1)
    assert not p.write_adoption([f1], [[0.6, 0.8]], adoption_seq=1)
    with pytest.raises(ValueError, match="replay changed"):
        p.write_adoption([f1], [[0.8, 0.6]], adoption_seq=1)
    adopt(canonical, "c", "v2", time=t2, scope=p.scope)
    p.write_adoption([fragment("v2", t2)], [[0.8, 0.6]], adoption_seq=2)
    assert p.search("记忆", query_vector=[0.6, 0.8])[0]["revision_id"] == "v2"
    assert p.search("记忆", known_at="2025-01-15T00:00:00Z")[0]["revision_id"] == "v1"
    canonical.record_adoption({"decision_id": "withdraw", "revision_id": "v2", "status": "RETRACTED",
                               "recorded_at": t3, "reason": "counterevidence"})
    p.write_adoption([fragment("v2", t3, "RETRACTED")], [[0.8, 0.6]], adoption_seq=3)
    assert p.search("记忆", query_vector=[0.6, 0.8]) == []
    assert p.search("记忆", known_at="2025-02-15T00:00:00Z")[0]["revision_id"] == "v2"
    with connect() as db:
        assert db.execute("SELECT indexed_through FROM memory_content_state WHERE projection_key=%s", (p.key,)).fetchone()[0] == 3


def test_scope_is_applied_before_candidate_limit(provider):
    canonical, p, connect = provider
    time = "2025-01-01T00:00:00Z"
    adopt(canonical, "c", "v1", time=time, scope=p.scope)
    p.write_adoption([fragment("v1", time)], [[0.6, 0.8]], adoption_seq=1)
    other = PostgresContentProjection(connect, ProjectionScope(p.scope.tenant_id, "o", "b", "PRIVATE_OWNER"),
        dimensions=2, embedding_model_id="fixture/1", lexical=ChineseLexical(), lexical_version=ChineseLexical.version,
        normalize=lambda c: c)
    other.initialize()
    for i in range(5):
        other.write_adoption([fragment("private" + str(i), time, claim="private" + str(i))], [[0.6, 0.8]], adoption_seq=i + 1)
    rows = p.search("记忆", query_vector=[0.6, 0.8], candidate_budget=1, limit=1)
    assert len(rows) == 1 and rows[0]["canonical_id"] == "c"


def test_later_adoption_preserves_earlier_world_interval(provider):
    canonical, p, connect = provider
    t1, t2 = "2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z"
    # Distinct valid intervals, not a retroactive correction.
    canonical.propose_revision({"claim_id": "c", "revision_id": "old", "text": "old fact", "recorded_at": t1,
        "valid_from": "2024-01-01T00:00:00Z", "valid_to": "2024-06-01T00:00:00Z", **asdict(p.scope)})
    canonical.record_adoption({"decision_id": "old-d", "revision_id": "old", "recorded_at": t1, "status": "ACTIVE", "reason": "test"})
    canonical.propose_revision({"claim_id": "c", "revision_id": "new", "text": "new fact", "recorded_at": t2,
        "valid_from": "2024-06-01T00:00:00Z", **asdict(p.scope)})
    canonical.record_adoption({"decision_id": "new-d", "revision_id": "new", "recorded_at": t2, "status": "ACTIVE", "reason": "test"})
    p.write_adoption([fragment("old", t1, end="2024-06-01T00:00:00Z")], [[0.6, 0.8]], adoption_seq=1)
    p.write_adoption([fragment("new", t2, start="2024-06-01T00:00:00Z")], [[0.6, 0.8]], adoption_seq=2)
    assert p.search("记忆", valid_at="2024-02-01T00:00:00Z")[0]["revision_id"] == "old"
    assert p.search("记忆", valid_at="2024-07-01T00:00:00Z")[0]["revision_id"] == "new"
