"""Immutable canonical revisions and adoption history for the SQLite adapter.

The legacy memories table is a compatibility view of the latest adoption.
It is never the source for a historical read. Indexes remain outside this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Protocol


CANONICAL_CONTRACT_VERSION = 2


def canonical_time(value: str) -> str:
    """Compare instants consistently, including offsets and fractional seconds."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("canonical time requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def now() -> str:
    return canonical_time(datetime.now(timezone.utc).isoformat())


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class CanonicalRevisionStore(Protocol):
    """Provider-independent revision/adoption contract; no SDK-specific objects."""

    def propose_revision(self, revision: Mapping[str, Any]) -> str: ...
    def record_adoption(self, decision: Mapping[str, Any]) -> str: ...
    def read_claim(self, claim_id: str, *, valid_at: str | None = None,
                   known_at: str | None = None, subject_id: str | None = None,
                   access_scope: str | None = None, tenant_id: str | None = None,
                   owner_id: str | None = None, agent_id: str | None = None) -> dict[str, Any] | None: ...


class CanonicalRevisionMixin:
    """Used by CanonicalMemoryStore; all mutations share its SQLite transaction."""

    def _ensure_revision_schema(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_revisions (
                    revision_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    revision_seq INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    subject_id TEXT NOT NULL,
                    access_scope TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    UNIQUE(claim_id, revision_seq)
                );
                CREATE TABLE IF NOT EXISTS memory_adoptions (
                    adoption_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    revision_id TEXT NOT NULL REFERENCES memory_revisions(revision_id),
                    recorded_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    decision_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS revisions_scope_claim_idx
                    ON memory_revisions(tenant_id, owner_id, subject_id, access_scope, claim_id, recorded_at);
                CREATE INDEX IF NOT EXISTS revisions_claim_idx
                    ON memory_revisions(claim_id, revision_seq);
                CREATE INDEX IF NOT EXISTS adoptions_revision_time_idx
                    ON memory_adoptions(revision_id, recorded_at, adoption_seq);
                CREATE TRIGGER IF NOT EXISTS revisions_no_update
                BEFORE UPDATE ON memory_revisions BEGIN
                    SELECT RAISE(ABORT, 'memory_revisions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete
                BEFORE DELETE ON memory_revisions BEGIN
                    SELECT RAISE(ABORT, 'memory_revisions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS adoptions_no_update
                BEFORE UPDATE ON memory_adoptions BEGIN
                    SELECT RAISE(ABORT, 'memory_adoptions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS adoptions_no_delete
                BEFORE DELETE ON memory_adoptions BEGIN
                    SELECT RAISE(ABORT, 'memory_adoptions are append-only'); END;
            """)
            # Serialize first migration with writers; reopen is constant work.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT value FROM schema_meta WHERE key='canonical_contract_version'"
            ).fetchone()
            if version is not None:
                if int(version["value"]) != CANONICAL_CONTRACT_VERSION:
                    raise RuntimeError(f"unsupported canonical contract {version['value']}")
                return
            migrated_at = now()
            count = 0
            for row in db.execute("SELECT rowid, * FROM memories ORDER BY rowid"):
                record = self._decode_row(db, row)
                record["history_origin"] = "LEGACY_SNAPSHOT"
                record["history_available_from"] = migrated_at
                revision_id = "memory-revision:legacy:" + digest(record["memory_id"])
                self._insert_revision(db, revision_id, record, migrated_at)
                self._insert_adoption(db, {
                    "decision_id": "memory-adoption:legacy:" + digest(record["memory_id"]),
                    "revision_id": revision_id,
                    "status": record["status"],
                    "reason": "Preserve legacy current state; earlier full snapshots are unavailable.",
                    "recorded_at": migrated_at,
                    "evidence_refs": record["evidence_refs"],
                    "generator": {"name": "canonical-v2-migration", "version": "1"},
                })
                count += 1
            db.executemany("INSERT INTO schema_meta(key,value) VALUES(?,?)", [
                ("canonical_contract_version", str(CANONICAL_CONTRACT_VERSION)),
                ("canonical_v2_migrated_at", migrated_at),
                ("canonical_v2_legacy_count", str(count)),
            ])

    def _insert_revision(self, db, revision_id: str, record: Mapping[str, Any],
                         recorded_at: str) -> str:
        data = dict(record)
        data.pop("content_hash", None)
        data.pop("revision_id", None)
        data.pop("revision_seq", None)
        data["claim_id"] = str(data.get("claim_id") or data["memory_id"])
        identity = db.execute(
            "SELECT subject_id,access_scope,tenant_id,owner_id,agent_id FROM memory_revisions WHERE claim_id=? LIMIT 1",
            (data["claim_id"],),
        ).fetchone()
        for field, default in (("tenant_id", self.default_tenant_id), ("owner_id", self.default_owner_id), ("agent_id", data["subject_id"])):
            data[field] = str(data.get(field) or (identity[field] if identity else default))
        data["scope_origin"] = "LEGACY_UNKNOWN" if "legacy" in (data["tenant_id"], data["owner_id"]) else "EXPLICIT"
        data["schema_version"] = CANONICAL_CONTRACT_VERSION
        data["occurred_at"] = data.get("occurred_at") or data.get("observed_at")
        data["recorded_at"] = canonical_time(recorded_at)
        if data["recorded_at"] > now():
            raise ValueError("revision recorded_at cannot be in the future")
        # Legacy validity is retained, including unknown bounds. No new inference.
        data["valid_from"] = canonical_time(data["valid_from"]) if data.get("valid_from") else None
        data["valid_to"] = canonical_time(data.get("valid_to") or data["valid_until"]) if data.get("valid_to") or data.get("valid_until") else None
        if data["valid_from"] and data["valid_to"] and data["valid_from"] >= data["valid_to"]:
            raise ValueError("valid_to must be later than valid_from")
        record_hash = digest(data)
        existing = db.execute("SELECT * FROM memory_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if existing is not None:
            if existing["record_hash"] != record_hash:
                raise ValueError("revision_id already exists with different content")
            return revision_id
        scope_fields = ("subject_id", "access_scope", "tenant_id", "owner_id", "agent_id")
        if identity is not None and any(identity[f] != data[f] for f in scope_fields):
            raise ValueError("a claim cannot change its subject or access scope")
        seq = db.execute("SELECT COALESCE(MAX(revision_seq),0)+1 FROM memory_revisions WHERE claim_id=?", (data["claim_id"],)).fetchone()[0]
        db.execute("""INSERT INTO memory_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            revision_id, data["claim_id"], seq, data["recorded_at"],
            data["valid_from"], data["valid_to"], data["subject_id"],
            data["access_scope"], data["tenant_id"], data["owner_id"], data["agent_id"], encode(data), record_hash,
        ))
        return revision_id

    def _insert_adoption(self, db, decision: Mapping[str, Any]) -> bool:
        from .durable import MEMORY_STATUSES
        data = dict(decision)
        data["schema_version"] = CANONICAL_CONTRACT_VERSION
        data["recorded_at"] = canonical_time(data["recorded_at"])
        if data["recorded_at"] > now():
            raise ValueError("adoption recorded_at cannot be in the future")
        data["status"] = str(data["status"]).upper()
        if data["status"] not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {data['status']}")
        if not str(data.get("reason") or "").strip():
            raise ValueError("adoption reason is required")
        revision = db.execute("SELECT * FROM memory_revisions WHERE revision_id=?", (data["revision_id"],)).fetchone()
        if revision is None:
            raise KeyError(data["revision_id"])
        if data["recorded_at"] < revision["recorded_at"]:
            raise ValueError("adoption cannot precede its revision")
        data["claim_id"] = revision["claim_id"]
        decision_hash = digest(data)
        existing = db.execute("SELECT decision_hash FROM memory_adoptions WHERE decision_id=?", (data["decision_id"],)).fetchone()
        if existing is not None:
            if existing["decision_hash"] != decision_hash:
                raise ValueError("decision_id already exists with different content")
            return False
        latest = db.execute("""SELECT MAX(a.recorded_at) FROM memory_adoptions a
            JOIN memory_revisions r ON r.revision_id=a.revision_id WHERE r.claim_id=?""", (revision["claim_id"],)).fetchone()[0]
        if latest and data["recorded_at"] < latest:
            raise ValueError("adoption recorded_at cannot move backwards")
        db.execute("""INSERT INTO memory_adoptions(
            decision_id,revision_id,recorded_at,status,decision_json,decision_hash
            ) VALUES(?,?,?,?,?,?)""", (data["decision_id"], data["revision_id"], data["recorded_at"], data["status"], encode(data), decision_hash))
        return True

    def _record_compatibility_revision(self, db, record: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        recorded_at = now()
        revision_id = "memory-revision:" + event["event_id"]
        self._insert_revision(db, revision_id, record, recorded_at)
        self._insert_adoption(db, {
            "decision_id": "memory-adoption:" + event["event_id"],
            "revision_id": revision_id, "recorded_at": recorded_at,
            "status": record["status"], "reason": event["event_type"],
            "evidence_refs": record["evidence_refs"],
            "generator": {"name": "legacy-memory-api", "version": "2"},
        })

    def propose_revision(self, revision: Mapping[str, Any]) -> str:
        from .durable import MEMORY_STATUSES, MEMORY_TYPES, memory_type_for_kind
        revision_id = str(revision.get("revision_id") or "").strip()
        claim_id = str(revision.get("claim_id") or "").strip()
        if not revision_id or not claim_id:
            raise ValueError("revision_id and claim_id are required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT recorded_at FROM memory_revisions WHERE revision_id=?", (revision_id,)).fetchone()
            recorded_at = canonical_time(revision["recorded_at"]) if revision.get("recorded_at") else existing["recorded_at"] if existing else now()
            raw = dict(revision)
            raw["memory_id"] = claim_id
            # Stable defaults permit an exact retry after process restart.
            raw.setdefault("created_at", recorded_at)
            kind = str(raw.get("kind") or "UNSPECIFIED").strip().upper()
            memory_type = str(raw.get("memory_type") or memory_type_for_kind(kind)).upper()
            status = str(raw.get("status") or "ACTIVE").upper()
            if memory_type not in MEMORY_TYPES or status not in MEMORY_STATUSES:
                raise ValueError("invalid memory type or status")
            text = str(raw.get("text") or "").strip()
            if not text:
                raise ValueError("memory text is required")
            record = {
                "memory_id": claim_id, "claim_id": claim_id,
                "memory_type": memory_type, "kind": kind, "text": text,
                "subject_id": str(raw.get("subject_id") or self.default_subject_id),
                "created_at": canonical_time(raw["created_at"]),
                "observed_at": raw.get("observed_at"),
                "occurred_at": raw.get("occurred_at") or raw.get("observed_at"),
                "valid_from": raw.get("valid_from"),
                "valid_until": raw.get("valid_to") or raw.get("valid_until"),
                "status": status, "source_type": str(raw.get("source_type") or "UNKNOWN").upper(),
                "source_ref": str(raw.get("source_ref") or ""),
                "supersedes": str(raw.get("supersedes") or ""),
                "superseded_by": str(raw.get("superseded_by") or ""),
                "access_scope": str(raw.get("access_scope") or "PRIVATE_OWNER").upper(),
                "payload": dict(raw.get("payload") or {}),
                "generator": dict(raw.get("generator") or {}),
            }
            for field, default in (("importance", 0.7), ("confidence", 1.0)):
                value = float(raw.get(field, default))
                if not 0 <= value <= 1:
                    raise ValueError(f"{field} must be between 0 and 1")
                record[field] = value
            for field in ("source_refs", "evidence_refs", "entities", "topics"):
                items = raw.get(field) or []
                if not isinstance(items, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in items):
                    raise ValueError(f"{field} requires non-empty string references")
                record[field] = list(dict.fromkeys(items))
            if record["source_ref"] and record["source_ref"] not in record["source_refs"]:
                record["source_refs"].insert(0, record["source_ref"])
            for field in ("tenant_id", "owner_id", "agent_id"):
                if raw.get(field):
                    record[field] = str(raw[field])
            return self._insert_revision(db, revision_id, record, recorded_at)

    def record_adoption(self, decision: Mapping[str, Any]) -> str:
        data = dict(decision)
        if not str(data.get("decision_id") or "").strip() or not str(data.get("revision_id") or "").strip():
            raise ValueError("decision_id and revision_id are required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT recorded_at FROM memory_adoptions WHERE decision_id=?", (data["decision_id"],)).fetchone()
            data["recorded_at"] = canonical_time(data["recorded_at"]) if data.get("recorded_at") else existing["recorded_at"] if existing else now()
            if not self._insert_adoption(db, data):
                return data["revision_id"]
            revision = db.execute("SELECT record_json FROM memory_revisions WHERE revision_id=?", (data["revision_id"],)).fetchone()
            record = json.loads(revision["record_json"])
            record["status"] = str(data["status"]).upper()
            event_id = "memory-event:adoption:" + digest(data["decision_id"])
            self._project_adopted_revision(db, record)
            db.execute("""INSERT INTO memory_events(
                event_id,memory_id,event_type,occurred_at,record_hash,payload_json
                ) VALUES(?,?,?,?,?,?)""", (event_id, record["memory_id"], "MEMORY_ADOPTED", data["recorded_at"], digest(record), encode({"revision_id": data["revision_id"], "decision_id": data["decision_id"]})))
            if self._on_transition is not None:
                self._on_transition(db, record["memory_id"], record["status"])
        self.repair_event_mirror()
        return data["revision_id"]

    def _project_adopted_revision(self, db, record: Mapping[str, Any]) -> None:
        # Keep the old read path functioning while providers migrate. This is
        # a projection in the same database, not a second canonical writer.
        from .durable import MEMORY_SCHEMA_VERSION
        row = dict(record)
        row["valid_until"] = record.get("valid_to")
        row["schema_version"] = MEMORY_SCHEMA_VERSION
        row["content_hash"] = digest({k: v for k, v in row.items() if k != "content_hash"})
        columns = ["memory_id", "memory_type", "kind", "subject_id", "text", "created_at", "observed_at", "valid_from", "valid_until", "status", "importance", "confidence", "source_type", "source_ref", "source_refs_json", "evidence_refs_json", "supersedes", "superseded_by", "access_scope", "payload_json", "content_hash", "schema_version"]
        values = [encode(row[c.removesuffix("_json")]) if c.endswith("_json") else row[c] for c in columns]
        updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != "memory_id")
        db.execute(f"INSERT INTO memories({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(memory_id) DO UPDATE SET {updates}", values)
        for table, field in (("memory_entities", "entity"), ("memory_topics", "topic")):
            db.execute(f"DELETE FROM {table} WHERE memory_id=?", (row["memory_id"],))
            members = row["entities" if field == "entity" else "topics"]
            db.executemany(f"INSERT INTO {table}(memory_id,{field}) VALUES(?,?)", [(row["memory_id"], item) for item in members])

    def query_claims(self, *, claim_id: str | None = None,
                     valid_at: str | None = None, known_at: str | None = None,
                     subject_id: str | None = None, access_scope: str | None = None,
                     tenant_id: str | None = None, owner_id: str | None = None,
                     agent_id: str | None = None,
                     statuses: list[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        clauses, params = [], []
        clauses.extend(["r.tenant_id=?", "r.owner_id=?"])
        params.extend([tenant_id or self.default_tenant_id, owner_id or self.default_owner_id])
        for column, value in (("claim_id", claim_id), ("subject_id", subject_id), ("access_scope", access_scope), ("agent_id", agent_id)):
            if value is not None:
                clauses.append(f"r.{column}=?")
                params.append(value)
        known = canonical_time(known_at) if known_at else now()
        clauses.extend(["r.recorded_at<=?", "a.recorded_at<=?"])
        params.extend([known, known])
        if valid_at is not None:
            valid = canonical_time(valid_at)
            clauses.extend(["(r.valid_from IS NULL OR r.valid_from<=?)", "(r.valid_to IS NULL OR r.valid_to>?)"])
            params.extend([valid, valid])
        status_clause = ""
        if statuses:
            status_clause = " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(str(s).upper() for s in statuses)
        sql = """WITH ranked AS (
            SELECT r.*,a.status,a.decision_id,a.recorded_at AS adopted_at,
                a.adoption_seq,ROW_NUMBER() OVER (
                    PARTITION BY r.claim_id ORDER BY a.recorded_at DESC,a.adoption_seq DESC
                ) AS rank FROM memory_revisions r
                JOIN memory_adoptions a ON a.revision_id=r.revision_id
            WHERE """ + " AND ".join(clauses) + ") SELECT * FROM ranked WHERE rank=1" + status_clause + " ORDER BY adopted_at DESC,adoption_seq DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            return [self._decode_revision(row) for row in db.execute(sql, params)]

    @staticmethod
    def _decode_revision(row) -> dict[str, Any]:
        record = json.loads(row["record_json"])
        record.update({key: row[key] for key in ("revision_id", "revision_seq", "status", "decision_id", "adopted_at")})
        return record

    def read_claim(self, claim_id: str, **filters) -> dict[str, Any] | None:
        rows = self.query_claims(claim_id=claim_id, limit=1, **filters)
        return rows[0] if rows else None

    def revision_history(self, claim_id: str, *, subject_id: str | None = None,
                         access_scope: str | None = None, tenant_id: str | None = None,
                         owner_id: str | None = None, agent_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        clauses, params = ["r.claim_id=?"], [claim_id]
        clauses.extend(["r.tenant_id=?", "r.owner_id=?"])
        params.extend([tenant_id or self.default_tenant_id, owner_id or self.default_owner_id])
        for column, value in (("subject_id", subject_id), ("access_scope", access_scope), ("agent_id", agent_id)):
            if value is not None:
                clauses.append(f"r.{column}=?")
                params.append(value)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute("SELECT r.* FROM memory_revisions r WHERE " + " AND ".join(clauses) + " ORDER BY revision_seq LIMIT ?", params)
            result = []
            for row in rows:
                record = json.loads(row["record_json"])
                record.update({"revision_id": row["revision_id"], "revision_seq": row["revision_seq"]})
                record["adoptions"] = [json.loads(a[0]) for a in db.execute("SELECT decision_json FROM memory_adoptions WHERE revision_id=? ORDER BY adoption_seq", (row["revision_id"],))]
                result.append(record)
            return result
