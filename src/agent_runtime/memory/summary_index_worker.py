"""Incrementally index SQL summaries as derived content, with durable replay."""
from __future__ import annotations

from dataclasses import asdict
import fcntl
import json
import sqlite3

from .postgres_retrieval import validate_vector
from .revisions import digest, encode


class SummaryIndexWorker:
    def __init__(self, canonical, projection, topics, embedder):
        if projection.scope != topics.scope:
            raise ValueError("summary and content providers must share a scope")
        self.canonical, self.projection, self.topics, self.embedder = canonical, projection, topics, embedder
        self.key = digest([projection.key, "sql-summary-content/1"])
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS summary_content_state (
                    worker_key TEXT PRIMARY KEY,indexed_through INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS summary_content_artifacts (
                    worker_key TEXT NOT NULL,summary_seq INTEGER NOT NULL,
                    artifact_json TEXT NOT NULL,artifact_hash TEXT NOT NULL,
                    PRIMARY KEY(worker_key,summary_seq));
                CREATE TABLE IF NOT EXISTS summary_content_attempts (
                    worker_key TEXT NOT NULL,summary_seq INTEGER NOT NULL,
                    attempts INTEGER NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL,
                    PRIMARY KEY(worker_key,summary_seq));
                CREATE TABLE IF NOT EXISTS content_worker_vectors (
                    scope_id TEXT NOT NULL,model_id TEXT NOT NULL,text_hash TEXT NOT NULL,
                    vector_json TEXT NOT NULL,PRIMARY KEY(scope_id,model_id,text_hash));
                CREATE TRIGGER IF NOT EXISTS summary_content_no_update
                    BEFORE UPDATE ON summary_content_artifacts BEGIN
                    SELECT RAISE(ABORT,'summary artifacts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS summary_content_no_delete
                    BEFORE DELETE ON summary_content_artifacts BEGIN
                    SELECT RAISE(ABORT,'summary artifacts are immutable'); END;
            """)
            db.execute("INSERT OR IGNORE INTO summary_content_state VALUES(?,0)", (self.key,))

    def connect(self):
        db = sqlite3.connect(self.canonical.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def progress(self):
        with self.connect() as db:
            cursor = db.execute("SELECT indexed_through FROM summary_content_state WHERE worker_key=?", (self.key,)).fetchone()[0]
            pending = [dict(r) for r in db.execute("SELECT summary_seq,attempts,status,error FROM summary_content_attempts WHERE worker_key=? AND status!='DONE' ORDER BY summary_seq LIMIT 8", (self.key,))]
        return {"indexed_through": cursor, "pending": pending, "worker_version": self.key}

    async def artifact(self, row):
        seq = row["summary_seq"]
        summary = {"summary_seq": seq, "summary_id": row["summary_id"], "topic_id": row["topic_id"],
                   "scope": asdict(self.projection.scope), "text": row["text"],
                   "bindings": json.loads(row["bindings_json"]), "generator": json.loads(row["generator_json"]),
                   "input_version": row["input_version"], "recorded_at": row["recorded_at"]}
        with self.connect() as db:
            saved = db.execute("SELECT artifact_json,artifact_hash FROM summary_content_artifacts WHERE worker_key=? AND summary_seq=?", (self.key, seq)).fetchone()
        if saved:
            result = json.loads(saved[0])
            if digest(result) != saved[1] or result["summary"] != summary:
                raise RuntimeError("immutable summary indexing artifact changed")
            return result
        cache_key = (self.projection.scope.group_id, self.projection.embedding_model_id, digest(summary["text"]))
        with self.connect() as db:
            cached = db.execute("SELECT vector_json FROM content_worker_vectors WHERE scope_id=? AND model_id=? AND text_hash=?", cache_key).fetchone()
        if cached:
            vector = validate_vector(json.loads(cached[0]), self.projection.dimensions)
        else:
            generated = await self.embedder([summary["text"]])
            if len(generated) != 1:
                raise ValueError("summary embedder returned an incomplete batch")
            vector = validate_vector(generated[0], self.projection.dimensions)
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO content_worker_vectors VALUES(?,?,?,?)", (*cache_key, encode(vector)))
                vector = json.loads(db.execute("SELECT vector_json FROM content_worker_vectors WHERE scope_id=? AND model_id=? AND text_hash=?", cache_key).fetchone()[0])
        result = {"summary": summary, "vector": vector}
        with self.connect() as db:
            db.execute("INSERT INTO summary_content_artifacts VALUES(?,?,?,?)", (self.key, seq, encode(result), digest(result)))
        return result

    async def process_one(self):
        with self.canonical.db_path.with_suffix(".summary-content.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            with self.connect() as db:
                cursor = db.execute("SELECT indexed_through FROM summary_content_state WHERE worker_key=?", (self.key,)).fetchone()[0]
                row = db.execute("SELECT rowid AS summary_seq,* FROM topic_summary_revisions WHERE scope_id=? AND rowid>? ORDER BY rowid LIMIT 1",
                                 (self.projection.scope.group_id, cursor)).fetchone()
                if row is None:
                    return False
                seq = row["summary_seq"]
                db.execute("""INSERT INTO summary_content_attempts VALUES(?,?,1,'RUNNING','')
                    ON CONFLICT(worker_key,summary_seq) DO UPDATE SET attempts=attempts+1,status='RUNNING',error=''""", (self.key, seq))
            try:
                artifact = await self.artifact(row)
                self.projection.write_summary(artifact["summary"], artifact["vector"])
            except Exception as exc:
                with self.connect() as db:
                    db.execute("UPDATE summary_content_attempts SET status='FAILED',error=? WHERE worker_key=? AND summary_seq=?", (type(exc).__name__ + ": " + str(exc), self.key, seq))
                raise
            with self.connect() as db:
                db.execute("UPDATE summary_content_attempts SET status='DONE',error='' WHERE worker_key=? AND summary_seq=?", (self.key, seq))
                db.execute("UPDATE summary_content_state SET indexed_through=? WHERE worker_key=?", (seq, self.key))
            return True
