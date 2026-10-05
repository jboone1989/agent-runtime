"""Independent incremental indexing; no model imports in the Runtime supervisor."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .postgres_retrieval import ContentFragment, validate_vector
from .revisions import digest, encode


class ContentIndexWorker:
    """Commit a source-bound derivation before updating the content provider.

    Interruption after the remote commit replays an identical artifact. Each
    worker invocation handles one adoption, not the entire memory history.
    """
    def __init__(self, canonical, projection, documents, embedder):
        if projection.scope != documents.scope:
            raise ValueError("content projection and document parser must use the same scope")
        self.canonical, self.projection, self.documents, self.embedder = canonical, projection, documents, embedder
        self.key = digest([projection.key, documents.key])
        self.db_path = Path(canonical.db_path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS content_worker_state (
                    worker_key TEXT PRIMARY KEY,indexed_through INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS content_worker_artifacts (
                    worker_key TEXT NOT NULL, adoption_seq INTEGER NOT NULL,
                    artifact_json TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                    PRIMARY KEY(worker_key,adoption_seq));
                CREATE TABLE IF NOT EXISTS content_worker_attempts (
                    worker_key TEXT NOT NULL, adoption_seq INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,error TEXT NOT NULL,
                    PRIMARY KEY(worker_key,adoption_seq));
                CREATE TABLE IF NOT EXISTS content_worker_vectors (
                    scope_id TEXT NOT NULL, model_id TEXT NOT NULL, text_hash TEXT NOT NULL,
                    vector_json TEXT NOT NULL, PRIMARY KEY(scope_id,model_id,text_hash));
                CREATE TRIGGER IF NOT EXISTS content_artifacts_no_update
                    BEFORE UPDATE ON content_worker_artifacts BEGIN
                    SELECT RAISE(ABORT,'content artifacts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS content_artifacts_no_delete
                    BEFORE DELETE ON content_worker_artifacts BEGIN
                    SELECT RAISE(ABORT,'content artifacts are immutable'); END;
            """)
            db.execute("INSERT OR IGNORE INTO content_worker_state(worker_key) VALUES(?)", (self.key,))

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def progress(self):
        with self.connect() as db:
            cursor = db.execute("SELECT indexed_through FROM content_worker_state WHERE worker_key=?", (self.key,)).fetchone()[0]
            attempts = [dict(r) for r in db.execute("SELECT adoption_seq,attempts,status,error FROM content_worker_attempts WHERE worker_key=? AND status!='DONE' ORDER BY adoption_seq LIMIT 8", (self.key,))]
        return {"indexed_through": cursor, "pending": attempts, "worker_version": self.key}

    async def artifact(self, row):
        with self.connect() as db:
            saved = db.execute("SELECT artifact_json,artifact_hash FROM content_worker_artifacts WHERE worker_key=? AND adoption_seq=?", (self.key, row["adoption_seq"])).fetchone()
        if saved:
            artifact = json.loads(saved[0])
            if digest(artifact) != saved[1] or artifact["record_hash"] != row["record_hash"]:
                raise RuntimeError("immutable content derivation changed")
            return artifact
        record = json.loads(row["record_json"])
        document = self.documents.parse(row["claim_id"], record["text"], source_version=row["revision_id"])
        parent_ids = {c["parent_id"] for c in document["chunks"] if c["parent_id"]}
        chunks = [c for c in document["chunks"] if c["node_id"] not in parent_ids]
        if not chunks:
            raise ValueError("document parser produced no content fragments")
        vectors, missing = {}, []
        for body in dict.fromkeys(c["body"] for c in chunks):
            with self.connect() as db:
                saved = db.execute("SELECT vector_json FROM content_worker_vectors WHERE scope_id=? AND model_id=? AND text_hash=?", (self.projection.scope.group_id, self.projection.embedding_model_id, digest(body))).fetchone()
            if saved:
                vectors[body] = validate_vector(json.loads(saved[0]), self.projection.dimensions)
            else:
                missing.append(body)
        if missing:
            generated = await self.embedder(missing)
            if len(generated) != len(missing):
                raise ValueError("content embedder returned an incomplete batch")
            valid = [validate_vector(v, self.projection.dimensions) for v in generated]
            with self.connect() as db:
                for body, vector in zip(missing, valid, strict=True):
                    db.execute("INSERT INTO content_worker_vectors VALUES(?,?,?,?)", (self.projection.scope.group_id, self.projection.embedding_model_id, digest(body), encode(vector)))
                    vectors[body] = vector
        fragments = [ContentFragment(canonical_id=row["claim_id"], revision_id=row["revision_id"],
                     fragment_id=c["node_id"], body=c["body"], kind=record["kind"], source_spans=c["source_spans"],
                     known_from=row["adopted_at"], valid_from=record.get("valid_from"), valid_to=record.get("valid_to"),
                     status=row["status"]) for c in chunks]
        artifact = {"adoption_seq": row["adoption_seq"], "record_hash": row["record_hash"],
                    "fragments": [asdict(f) for f in fragments], "vectors": [vectors[f.body] for f in fragments]}
        with self.connect() as db:
            db.execute("INSERT INTO content_worker_artifacts VALUES(?,?,?,?)", (self.key, row["adoption_seq"], encode(artifact), digest(artifact)))
        return artifact

    async def process_one(self):
        import fcntl
        with self.db_path.with_suffix(".content-worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            with self.connect() as db:
                cursor = db.execute("SELECT indexed_through FROM content_worker_state WHERE worker_key=?", (self.key,)).fetchone()[0]
                row = db.execute("""SELECT r.*,a.adoption_seq,a.recorded_at AS adopted_at,a.status
                    FROM memory_adoptions a JOIN memory_revisions r ON r.revision_id=a.revision_id
                    WHERE a.adoption_seq>? AND r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                    ORDER BY a.adoption_seq LIMIT 1""", (cursor, *asdict(self.projection.scope).values())).fetchone()
                if row is None:
                    return False
                db.execute("""INSERT INTO content_worker_attempts VALUES(?,?,1,'RUNNING','')
                    ON CONFLICT(worker_key,adoption_seq) DO UPDATE SET attempts=attempts+1,status='RUNNING',error=''""", (self.key, row["adoption_seq"]))
            try:
                artifact = await self.artifact(row)
                self.projection.write_adoption([ContentFragment(**f) for f in artifact["fragments"]], artifact["vectors"], adoption_seq=row["adoption_seq"])
            except Exception as exc:
                with self.connect() as db:
                    db.execute("UPDATE content_worker_attempts SET status='FAILED',error=? WHERE worker_key=? AND adoption_seq=?", (type(exc).__name__ + ": " + str(exc), self.key, row["adoption_seq"]))
                raise
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE content_worker_attempts SET status='DONE',error='' WHERE worker_key=? AND adoption_seq=?", (self.key, row["adoption_seq"]))
                db.execute("UPDATE content_worker_state SET indexed_through=? WHERE worker_key=?", (row["adoption_seq"], self.key))
            return True


class MemoryIndexWorker:
    """One scheduled worker; provider failures are collected and surfaced.

    A broken graph provider does not prevent the independent topic/content jobs
    from running. There is no substitute provider and no automatic model retry.
    """
    def __init__(self, *, graph=None, topics=None, content=None, summary_generator=None, summary_version=None):
        self.graph, self.topics, self.content = graph, topics, content
        self.summary_generator, self.summary_version = summary_generator, summary_version

    async def run_once(self, *, batch_size=100):
        progress, failures = {}, []
        if self.graph:
            try:
                self.graph.ledger.discover(batch_size=batch_size)
                progress["graph_updated"] = await self.graph.ledger.process_one(self.graph)
            except Exception as exc:
                failures.append(exc)
        if self.topics:
            try:
                progress["topic_adoptions"] = self.topics.update(batch_size=batch_size)
                if not self.topics.progress()["needs_update"] and self.summary_generator:
                    progress["topic_summary_updated"] = await self.topics.refresh_one(self.summary_generator, generator_version=self.summary_version)
            except Exception as exc:
                failures.append(exc)
        if self.content:
            try:
                progress["content_updated"] = await self.content.process_one()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("Memory indexing failed; independently committed progress remains durable", failures)
        return progress
