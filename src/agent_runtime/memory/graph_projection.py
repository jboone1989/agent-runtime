"""Rebuildable Graphiti projection of explicitly adopted canonical relations.

Graph extraction is a proposal, never an adoption. This module uses the official
SDK save interfaces to replay structured relations already in a revision's
payload. No model runs during replay and Graphiti cannot invalidate canonical
history. SQL keeps durable jobs and derivation receipts, not another fact store.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import UUID, uuid5

from .revisions import digest, encode, now
from .projection_scope import ProjectionScope

PROVIDER_VERSION = "graphiti-core/0.30.2;projection/1"
_NAMESPACE = UUID("325d9882-9009-4fc9-a5d7-40dd1cf39287")
ASSERTION_TYPES = {"OBSERVATION", "SOURCE_CLAIM", "HYPOTHESIS", "CAUSAL_HYPOTHESIS"}


GraphScope = ProjectionScope


def graph_uuid(kind: str, scope: GraphScope, canonical_id: str) -> str:
    return str(uuid5(_NAMESPACE, encode([kind, asdict(scope), canonical_id])))


def adopted_relations(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate adoption-owned structures; never infer a relation from text."""
    relations = record.get("payload", {}).get("graph_relations", [])
    if not isinstance(relations, list) or len(relations) > 200:
        raise ValueError("graph_relations must be a list of at most 200 relations")
    allowed_refs = set(record.get("source_refs", [])) | set(record.get("evidence_refs", []))
    result, ids = [], set()
    for raw in relations:
        relation = dict(raw)
        for field in ("relation_id", "predicate", "fact"):
            if not isinstance(relation.get(field), str) or not relation[field].strip():
                raise ValueError(f"relation requires {field}")
        if relation["relation_id"] in ids:
            raise ValueError("duplicate relation_id")
        ids.add(relation["relation_id"])
        if relation.get("assertion_type") not in ASSERTION_TYPES:
            raise ValueError("relation requires an explicit assertion_type")
        for endpoint in ("source", "target"):
            entity = relation.get(endpoint)
            if not isinstance(entity, dict) or any(not isinstance(entity.get(k), str) or not entity[k].strip() for k in ("entity_id", "name")):
                raise ValueError("relation endpoints require stable entity_id and name")
        spans = relation.get("source_spans")
        if not isinstance(spans, list) or not spans:
            raise ValueError("relation requires source spans")
        for span in spans:
            if not isinstance(span, dict) or span.get("ref") not in allowed_refs:
                raise ValueError("relation provenance is outside canonical evidence")
            start, end = span.get("start"), span.get("end")
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("source span requires a positive character range")
        confidence = relation.get("confidence", record.get("confidence", 1.0))
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("relation confidence must be between 0 and 1")
        relation["confidence"] = float(confidence)
        result.append(relation)
    return result


class GraphProjectionLedger:
    """Incremental durable jobs keyed by immutable adoption sequence and generation.

    One worker holds an OS lock for this DB while processing a job. A crash before
    acknowledgement replays deterministic SDK UUIDs. The cursor advances only
    after successful graph writes; the scan cursor advances after SQL enqueue.
    """
    def __init__(self, canonical, scope: GraphScope, *, generation: str = "v1"):
        if not generation or not generation.strip():
            raise ValueError("projection generation is required")
        self.canonical, self.scope, self.generation = canonical, scope, generation
        self.key = digest([PROVIDER_VERSION, scope.group_id, generation])
        self.db_path = Path(canonical.db_path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS graph_projection_state (
                    projection_key TEXT PRIMARY KEY, scanned_through INTEGER NOT NULL DEFAULT 0,
                    indexed_through INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS graph_projection_jobs (
                    projection_key TEXT NOT NULL, adoption_seq INTEGER NOT NULL,
                    revision_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(projection_key,adoption_seq));
                CREATE TABLE IF NOT EXISTS graph_projection_derivations (
                    projection_key TEXT NOT NULL, revision_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL, artifact_json TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    PRIMARY KEY(projection_key,revision_id));
                CREATE TABLE IF NOT EXISTS graph_projection_vectors (
                    scope_id TEXT NOT NULL, model_id TEXT NOT NULL, text_hash TEXT NOT NULL,
                    vector_json TEXT NOT NULL, vector_hash TEXT NOT NULL,
                    PRIMARY KEY(scope_id,model_id,text_hash));
                CREATE TRIGGER IF NOT EXISTS graph_vectors_no_update
                    BEFORE UPDATE ON graph_projection_vectors BEGIN
                    SELECT RAISE(ABORT,'graph vectors are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS graph_vectors_no_delete
                    BEFORE DELETE ON graph_projection_vectors BEGIN
                    SELECT RAISE(ABORT,'graph vectors are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS graph_derivations_no_update
                    BEFORE UPDATE ON graph_projection_derivations BEGIN
                    SELECT RAISE(ABORT,'graph derivations are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS graph_derivations_no_delete
                    BEFORE DELETE ON graph_projection_derivations BEGIN
                    SELECT RAISE(ABORT,'graph derivations are immutable'); END;
                CREATE INDEX IF NOT EXISTS graph_jobs_pending_idx
                    ON graph_projection_jobs(projection_key,status,adoption_seq);
            """)
            db.execute("INSERT OR IGNORE INTO graph_projection_state(projection_key) VALUES(?)", (self.key,))

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def discover(self, *, batch_size: int = 100) -> int:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("SELECT scanned_through FROM graph_projection_state WHERE projection_key=?", (self.key,)).fetchone()[0]
            # Scope is applied in SQL before decoding or handing input to a model.
            rows = db.execute("""SELECT a.adoption_seq,r.revision_id,r.record_hash
                FROM memory_adoptions a JOIN memory_revisions r ON r.revision_id=a.revision_id
                WHERE a.adoption_seq>? AND r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                ORDER BY a.adoption_seq LIMIT ?""", (cursor, *asdict(self.scope).values(), batch_size)).fetchall()
            for row in rows:
                db.execute("INSERT OR IGNORE INTO graph_projection_jobs(projection_key,adoption_seq,revision_id,input_hash) VALUES(?,?,?,?)", (self.key, *row))
            if rows:
                db.execute("UPDATE graph_projection_state SET scanned_through=? WHERE projection_key=?", (rows[-1]["adoption_seq"], self.key))
            return len(rows)

    def snapshot(self) -> dict[str, Any]:
        with self.connect() as db:
            state = dict(db.execute("SELECT * FROM graph_projection_state WHERE projection_key=?", (self.key,)).fetchone())
            state["jobs"] = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM graph_projection_jobs WHERE projection_key=? GROUP BY status", (self.key,))}
            return {**state, "provider_version": PROVIDER_VERSION, "generation": self.generation, "scope": asdict(self.scope)}

    def load_artifact(self, job) -> dict[str, Any]:
        with self.connect() as db:
            existing = db.execute("SELECT * FROM graph_projection_derivations WHERE projection_key=? AND revision_id=?", (self.key, job["revision_id"])).fetchone()
            if existing:
                if existing["input_hash"] != job["input_hash"]:
                    raise RuntimeError("immutable revision hash changed")
                artifact = json.loads(existing["artifact_json"])
                if digest(artifact) != existing["artifact_hash"]:
                    raise RuntimeError("graph derivation hash mismatch")
                return artifact
            row = db.execute("""SELECT * FROM memory_revisions WHERE revision_id=?
                AND tenant_id=? AND owner_id=? AND agent_id=? AND access_scope=?""", (job["revision_id"], *asdict(self.scope).values())).fetchone()
            if row is None or row["record_hash"] != job["input_hash"]:
                raise RuntimeError("graph job does not match its canonical revision and scope")
            record = json.loads(row["record_json"])
            artifact = {"revision_id": row["revision_id"], "claim_id": row["claim_id"],
                        "input_hash": row["record_hash"], "provider_version": PROVIDER_VERSION,
                        "scope": asdict(self.scope), "relations": adopted_relations(record)}
            db.execute("INSERT INTO graph_projection_derivations VALUES(?,?,?,?,?,?)", (self.key, job["revision_id"], job["input_hash"], encode(artifact), digest(artifact), now()))
            return artifact

    async def process_one(self, sink) -> bool:
        import fcntl
        with self.db_path.with_suffix(".graph-worker.lock").open("a") as lock:
            # Never hold a SQL transaction over a network/model call.
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            with self.connect() as db:
                job = db.execute("SELECT * FROM graph_projection_jobs WHERE projection_key=? AND status!='DONE' ORDER BY adoption_seq LIMIT 1", (self.key,)).fetchone()
                if job is None:
                    return False
                db.execute("UPDATE graph_projection_jobs SET attempts=attempts+1,status='RUNNING',error='' WHERE projection_key=? AND adoption_seq=?", (self.key, job["adoption_seq"]))
            try:
                artifact = self.load_artifact(job)
                await sink.save_artifact(artifact, self.scope)
            except Exception as exc:
                with self.connect() as db:
                    # Persist exception type only; provider messages can contain evidence.
                    db.execute("UPDATE graph_projection_jobs SET status='FAILED',error=? WHERE projection_key=? AND adoption_seq=?", (type(exc).__name__, self.key, job["adoption_seq"]))
                raise
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE graph_projection_jobs SET status='DONE',error='' WHERE projection_key=? AND adoption_seq=?", (self.key, job["adoption_seq"]))
                db.execute("UPDATE graph_projection_state SET indexed_through=? WHERE projection_key=?", (job["adoption_seq"], self.key))
            return True


class GraphitiAdoptedProjection:
    """Official SDK objects on Neo4j, with canonical authorization on every read."""
    def __init__(self, canonical, graphiti, ledger: GraphProjectionLedger, *, embedding_model_id: str | None = None):
        from importlib.metadata import version
        if version("graphiti-core") != "0.30.2":
            raise RuntimeError("Graphiti SDK does not match the tested dependency lock")
        if ledger.canonical.db_path != canonical.db_path:
            raise ValueError("graph ledger belongs to a different canonical store")
        self.canonical, self.graphiti, self.ledger = canonical, graphiti, ledger
        self.embedding_model_id = embedding_model_id

    async def vectors_for(self, texts: list[str]) -> dict[str, list[float]]:
        """Persist real model vectors before graph writes; replay reuses them."""
        if not self.embedding_model_id:
            raise ValueError("graph writes require an explicit embedding model version")
        vectors, missing = {}, []
        for text in dict.fromkeys(texts):
            with self.ledger.connect() as db:
                row = db.execute("SELECT vector_json,vector_hash FROM graph_projection_vectors WHERE scope_id=? AND model_id=? AND text_hash=?", (self.ledger.scope.group_id, self.embedding_model_id, digest(text))).fetchone()
            if row is None:
                missing.append(text)
            else:
                vector = json.loads(row["vector_json"])
                if digest(vector) != row["vector_hash"]:
                    raise RuntimeError("stored graph vector hash mismatch")
                vectors[text] = vector
        if missing:
            generated = await self.graphiti.embedder.create_batch(missing)
            if len(generated) != len(missing):
                raise ValueError("embedder returned the wrong number of vectors")
            for text, vector in zip(missing, generated, strict=True):
                if not isinstance(vector, list) or not vector or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector) or not any(x != 0 for x in vector):
                    raise ValueError("embedder returned an invalid vector")
                vectors[text] = vector
        if len({len(v) for v in vectors.values()}) > 1:
            raise ValueError("embedding dimensions changed within a model version")
        if missing:
            with self.ledger.connect() as db:
                db.executemany("INSERT INTO graph_projection_vectors VALUES(?,?,?,?,?)", [(self.ledger.scope.group_id, self.embedding_model_id, digest(text), encode(vectors[text]), digest(vectors[text])) for text in missing])
        return vectors

    async def save_artifact(self, artifact: Mapping[str, Any], scope: GraphScope):
        from graphiti_core.nodes import EntityNode
        from graphiti_core.edges import EntityEdge
        if scope != self.ledger.scope or artifact["scope"] != asdict(scope):
            raise ValueError("graph projection scope mismatch")
        with self.ledger.connect() as db:
            row = db.execute("SELECT record_json,recorded_at,record_hash FROM memory_revisions WHERE revision_id=? AND tenant_id=? AND owner_id=? AND agent_id=? AND access_scope=?", (artifact["revision_id"], *asdict(scope).values())).fetchone()
            adopted = db.execute("SELECT 1 FROM memory_adoptions WHERE revision_id=? LIMIT 1", (artifact["revision_id"],)).fetchone()
        if row is None or not adopted or row["record_hash"] != artifact["input_hash"]:
            raise RuntimeError("projection input no longer matches canonical revision")
        record = json.loads(row["record_json"])
        if artifact["claim_id"] != record["claim_id"]:
            raise ValueError("graph artifact claim does not match canonical revision")
        if artifact["relations"] != adopted_relations(record):
            raise ValueError("graph artifact differs from canonical adopted structures")
        texts = [text for relation in artifact["relations"] for text in (relation["source"]["name"], relation["target"]["name"], relation["fact"])]
        vectors = await self.vectors_for(texts) if texts else {}
        timestamp = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        for relation in artifact["relations"]:
            nodes = []
            for endpoint in ("source", "target"):
                entity = relation[endpoint]
                node = EntityNode(uuid=graph_uuid("entity", scope, entity["entity_id"]),
                                  name=entity["name"], group_id=scope.group_id, created_at=timestamp,
                                  name_embedding=vectors[entity["name"]],
                                  attributes={"canonical_entity_id": entity["entity_id"]})
                await node.save(self.graphiti.driver)
                nodes.append(node)
            edge = EntityEdge(uuid=graph_uuid("relation", scope, encode([artifact["revision_id"], relation["relation_id"]])),
                              source_node_uuid=nodes[0].uuid, target_node_uuid=nodes[1].uuid,
                              group_id=scope.group_id, name=relation["predicate"], fact=relation["fact"],
                              fact_embedding=vectors[relation["fact"]],
                              created_at=timestamp,
                              valid_at=datetime.fromisoformat(record["valid_from"].replace("Z", "+00:00")) if record.get("valid_from") else None,
                              invalid_at=datetime.fromisoformat(record["valid_to"].replace("Z", "+00:00")) if record.get("valid_to") else None,
                              attributes={"canonical_claim_id": artifact["claim_id"], "canonical_revision_id": artifact["revision_id"],
                                          "canonical_relation_id": relation["relation_id"], "assertion_type": relation["assertion_type"],
                                          "source_spans_json": encode(relation["source_spans"]), "confidence": relation["confidence"],
                                          "provider_version": PROVIDER_VERSION})
            await edge.save(self.graphiti.driver)

    def normalize(self, edge, *, valid_at: str | None = None, known_at: str | None = None) -> dict[str, Any] | None:
        scope = self.ledger.scope
        if edge.group_id != scope.group_id:
            return None
        attrs = edge.attributes
        claim_id, revision_id = attrs.get("canonical_claim_id"), attrs.get("canonical_revision_id")
        if not claim_id or not revision_id:
            return None
        record = self.canonical.read_claim(claim_id, valid_at=valid_at or now(), known_at=known_at,
                                           **asdict(scope), statuses=["ACTIVE"])
        if record is None or record["revision_id"] != revision_id or not scope.matches(record):
            return None
        # Read provenance from immutable canonical structures, not mutable graph properties.
        relations = adopted_relations(record)
        relation = next((r for r in relations if r["relation_id"] == attrs.get("canonical_relation_id")), None)
        if relation is None:
            return None
        return {"canonical_id": claim_id, "revision_id": revision_id, "kind": record["kind"],
                "excerpt": record["text"][:800], "status": record["status"], "scope": asdict(scope),
                "view": "GRAPH_RELATION", "provider_version": PROVIDER_VERSION,
                "indexed_through": self.ledger.snapshot()["indexed_through"],
                "ranking_reason": "scoped graph relation verified against canonical adoption",
                "relation": relation, "source_refs": record["source_refs"], "evidence_refs": record["evidence_refs"]}

    async def follow_relations(self, entity_id: str, *, limit: int = 20,
                               valid_at: str | None = None, known_at: str | None = None) -> list[dict[str, Any]]:
        from graphiti_core.edges import EntityEdge
        if not entity_id or not 1 <= limit <= 100:
            raise ValueError("entity_id and a limit between 1 and 100 are required")
        scope = self.ledger.scope
        # Bound graph work before canonical normalization, and apply scope on nodes AND edges.
        records, _, _ = await self.graphiti.driver.execute_query("""
            MATCH (n:Entity)-[e:RELATES_TO]-(m:Entity)
            WHERE n.uuid=$uuid AND n.group_id=$group AND m.group_id=$group AND e.group_id=$group
            RETURN DISTINCT e.uuid AS uuid ORDER BY uuid LIMIT $limit
        """, uuid=graph_uuid("entity", scope, entity_id), group=scope.group_id, limit=min(1000, limit * 10), routing_="r")
        if not records:
            return []
        edges = await EntityEdge.get_by_uuids(self.graphiti.driver, [r["uuid"] for r in records])
        result = []
        for edge in edges:
            candidate = self.normalize(edge, valid_at=valid_at, known_at=known_at)
            if candidate is not None:
                result.append(candidate)
            if len(result) >= limit:
                break
        return result


def create_graphiti(*, driver, llm_client, embedder, cross_encoder):
    """Require caller-owned models; importing OpenAI is not an API dependency."""
    from graphiti_core import Graphiti
    if any(client is None for client in (driver, llm_client, embedder, cross_encoder)):
        raise ValueError("Graphiti requires explicit driver, LLM, embedder and reranker")
    return Graphiti(graph_driver=driver, llm_client=llm_client, embedder=embedder,
                    cross_encoder=cross_encoder, max_coroutines=1)
