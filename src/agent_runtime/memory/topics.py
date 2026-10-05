"""Independent SQL topic navigation and versioned, source-bound summaries.

Memberships reference canonical revisions. A summary is a derived navigation
artifact and never a new claim. Updates consume adopted revisions in bounded
batches; only the separate worker invokes the caller-owned summary model.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from typing import Any

from .projection_scope import ProjectionScope
from .revisions import canonical_time, digest, encode, now


class TopicBudgetExceeded(RuntimeError):
    pass


class TopicInputChanged(RuntimeError):
    pass


class TopicHierarchy:
    def __init__(self, canonical, scope: ProjectionScope):
        self.canonical, self.scope = canonical, scope
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS topic_projection_state (
                    scope_id TEXT PRIMARY KEY, indexed_through INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS topic_nodes (
                    scope_id TEXT NOT NULL, topic_id TEXT NOT NULL, name TEXT NOT NULL,
                    kind TEXT NOT NULL, parent_id TEXT, dirty_seq INTEGER NOT NULL DEFAULT 0,
                    summary_id TEXT, refresh_after TEXT, PRIMARY KEY(scope_id,topic_id));
                CREATE TABLE IF NOT EXISTS topic_memberships (
                    scope_id TEXT NOT NULL, topic_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    PRIMARY KEY(scope_id,topic_id,revision_id));
                CREATE INDEX IF NOT EXISTS topic_members_claim
                    ON topic_memberships(scope_id,claim_id);
                CREATE INDEX IF NOT EXISTS topic_children
                    ON topic_nodes(scope_id,parent_id);
                CREATE TABLE IF NOT EXISTS topic_summary_revisions (
                    summary_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL, input_version TEXT NOT NULL,
                    text TEXT NOT NULL, bindings_json TEXT NOT NULL,
                    generator_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS topic_summaries_no_update
                    BEFORE UPDATE ON topic_summary_revisions BEGIN
                    SELECT RAISE(ABORT,'summary revisions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS topic_summaries_no_delete
                    BEFORE DELETE ON topic_summary_revisions BEGIN
                    SELECT RAISE(ABORT,'summary revisions are immutable'); END;
            """)
            db.execute("INSERT OR IGNORE INTO topic_projection_state(scope_id) VALUES(?)", (scope.group_id,))
            if "refresh_after" not in {r[1] for r in db.execute("PRAGMA table_info(topic_nodes)")}:
                db.execute("ALTER TABLE topic_nodes ADD COLUMN refresh_after TEXT")

    def connect(self):
        db = sqlite3.connect(self.canonical.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def define(self, topic_id: str, name: str, *, kind: str = "TOPIC", parent_id: str | None = None):
        if not topic_id.strip() or not name.strip() or kind not in {"DOMAIN", "TOPIC", "SUBTOPIC"}:
            raise ValueError("topic identity, name and a supported kind are required")
        scope_id = self.scope.group_id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE topic_nodes SET summary_id=NULL,refresh_after=NULL WHERE scope_id=? AND refresh_after<=?", (scope_id, now()))
            existing = db.execute("SELECT * FROM topic_nodes WHERE scope_id=? AND topic_id=?", (scope_id, topic_id)).fetchone()
            if existing:
                if (existing["name"], existing["kind"], existing["parent_id"]) != (name, kind, parent_id):
                    raise ValueError("topic definitions are stable; define a new node to change topology")
                return
            if parent_id is not None and not db.execute("SELECT 1 FROM topic_nodes WHERE scope_id=? AND topic_id=?", (scope_id, parent_id)).fetchone():
                raise KeyError(parent_id)
            db.execute("INSERT INTO topic_nodes(scope_id,topic_id,name,kind,parent_id) VALUES(?,?,?,?,?)", (scope_id, topic_id, name, kind, parent_id))
            # Adding a child changes navigation even when no evidence changed.
            if parent_id:
                self._dirty(db, [parent_id], 0)

    def _dirty(self, db, topics, seq):
        for topic_id in topics:
            db.execute("""WITH RECURSIVE ancestors(id) AS (
                SELECT topic_id FROM topic_nodes WHERE scope_id=? AND topic_id=?
                UNION SELECT n.parent_id FROM topic_nodes n JOIN ancestors a ON n.topic_id=a.id
                    WHERE n.scope_id=? AND n.parent_id IS NOT NULL)
                UPDATE topic_nodes SET dirty_seq=MAX(dirty_seq,?),summary_id=NULL
                WHERE scope_id=? AND topic_id IN (SELECT id FROM ancestors)""",
                (self.scope.group_id, topic_id, self.scope.group_id, seq, self.scope.group_id))

    def update(self, *, batch_size: int = 100) -> int:
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        scope_id = self.scope.group_id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE topic_nodes SET summary_id=NULL,refresh_after=NULL WHERE scope_id=? AND refresh_after<=?", (scope_id, now()))
            cursor = db.execute("SELECT indexed_through FROM topic_projection_state WHERE scope_id=?", (scope_id,)).fetchone()[0]
            rows = db.execute("""SELECT a.adoption_seq,r.claim_id,r.revision_id,r.record_json
                FROM memory_adoptions a JOIN memory_revisions r ON r.revision_id=a.revision_id
                WHERE a.adoption_seq>? AND r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                ORDER BY a.adoption_seq LIMIT ?""", (cursor, *asdict(self.scope).values(), batch_size)).fetchall()
            for row in rows:
                record = json.loads(row["record_json"])
                old = [r[0] for r in db.execute("SELECT DISTINCT topic_id FROM topic_memberships WHERE scope_id=? AND claim_id=?", (scope_id, row["claim_id"]))]
                topics = record.get("topics", [])
                for topic in topics:
                    db.execute("INSERT OR IGNORE INTO topic_nodes(scope_id,topic_id,name,kind) VALUES(?,?,?,'TOPIC')", (scope_id, topic, topic))
                    db.execute("INSERT OR IGNORE INTO topic_memberships VALUES(?,?,?,?)", (scope_id, topic, row["claim_id"], row["revision_id"]))
                self._dirty(db, set(old) | set(topics), row["adoption_seq"])
            if rows:
                db.execute("UPDATE topic_projection_state SET indexed_through=? WHERE scope_id=?", (rows[-1]["adoption_seq"], scope_id))
            return len(rows)

    def progress(self):
        with self.connect() as db:
            cursor = db.execute("SELECT indexed_through FROM topic_projection_state WHERE scope_id=?", (self.scope.group_id,)).fetchone()[0]
            current = db.execute("""SELECT COALESCE(MAX(a.adoption_seq),0) FROM memory_adoptions a
                JOIN memory_revisions r ON r.revision_id=a.revision_id
                WHERE r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?""", tuple(asdict(self.scope).values())).fetchone()[0]
        return {"indexed_through": cursor, "canonical_through": current, "needs_update": cursor < current,
                "provider_version": "sql-topics/1", "scope": asdict(self.scope)}

    def members(self, topic_id: str, *, offset: int = 0, limit: int = 20,
                valid_at: str | None = None, known_at: str | None = None):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("membership paging requires nonnegative offset and limit 1..1000")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM topic_nodes WHERE scope_id=? AND topic_id=?", (self.scope.group_id, topic_id)).fetchone():
                raise KeyError(topic_id)
            # Select the canonical adoption before pagination, so stale memberships
            # and withdrawn claims cannot consume a page or hide live records.
            known = canonical_time(known_at) if known_at else now()
            valid_at = valid_at or now()
            valid_sql, params = "", [self.scope.group_id, topic_id, self.scope.group_id,
                                      *asdict(self.scope).values(), known, known]
            if valid_at is not None:
                valid_sql = " AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_to IS NULL OR r.valid_to>?)"
                params.extend([canonical_time(valid_at)] * 2)
            params.extend([self.scope.group_id, limit, offset])
            rows = db.execute("""WITH RECURSIVE descendants(id) AS (
                SELECT topic_id FROM topic_nodes WHERE scope_id=? AND topic_id=?
                UNION SELECT n.topic_id FROM topic_nodes n JOIN descendants d ON n.parent_id=d.id WHERE n.scope_id=?),
                ranked AS (SELECT r.*,a.status, ROW_NUMBER() OVER (
                    PARTITION BY r.claim_id ORDER BY a.recorded_at DESC,a.adoption_seq DESC) AS rank
                    FROM memory_revisions r JOIN memory_adoptions a ON a.revision_id=r.revision_id
                    WHERE r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                        AND r.recorded_at<=? AND a.recorded_at<=?""" + valid_sql + """)
                SELECT DISTINCT r.claim_id FROM ranked r JOIN topic_memberships m ON m.revision_id=r.revision_id
                WHERE r.rank=1 AND r.status='ACTIVE' AND m.scope_id=? AND m.topic_id IN (SELECT id FROM descendants)
                ORDER BY r.claim_id LIMIT ? OFFSET ?""", params).fetchall()
        return [self.canonical.read_claim(row[0], **asdict(self.scope), valid_at=valid_at,
                                          known_at=known_at, statuses=["ACTIVE"]) for row in rows]

    def summary_input(self, topic_id: str, *, max_members: int = 100, max_chars: int = 24000):
        if not 1 <= max_members < 1000 or max_chars <= 0:
            raise ValueError("summary budgets must be explicit positive values")
        # Every refresh rereads current canonical records, including conflicts in
        # their payload; it never repeatedly compresses an old summary.
        records = self.members(topic_id, limit=max_members + 1)
        if any(record is None for record in records):
            raise TopicInputChanged("canonical adoption changed while preparing summary")
        if len(records) > max_members:
            raise TopicBudgetExceeded("TOPIC_MEMBER_BUDGET_EXHAUSTED; add subtopics or increase the disclosed budget")
        with self.connect() as db:
            node = dict(db.execute("SELECT * FROM topic_nodes WHERE scope_id=? AND topic_id=?", (self.scope.group_id, topic_id)).fetchone())
        bindings = [{"claim_id": r["claim_id"], "revision_id": r["revision_id"], "decision_id": r["decision_id"]} for r in records]
        with self.connect() as db:
            # Include validity transitions of adopted members that are not yet
            # valid. Time passing can change a summary without another write.
            boundaries = db.execute("""WITH RECURSIVE descendants(id) AS (
                SELECT topic_id FROM topic_nodes WHERE scope_id=? AND topic_id=?
                UNION SELECT n.topic_id FROM topic_nodes n JOIN descendants d ON n.parent_id=d.id WHERE n.scope_id=?)
                SELECT r.valid_from,r.valid_to FROM memory_revisions r
                JOIN topic_memberships m ON m.revision_id=r.revision_id
                WHERE m.scope_id=? AND m.topic_id IN (SELECT id FROM descendants)""",
                (self.scope.group_id, topic_id, self.scope.group_id, self.scope.group_id)).fetchall()
        instant = now()
        next_boundaries = [t for r in boundaries for t in r if t and t > instant]
        request = {"topic_id": topic_id, "scope": asdict(self.scope), "members": records,
                   "bindings": bindings, "dirty_seq": node["dirty_seq"],
                   "refresh_after": min(next_boundaries) if next_boundaries else None}
        request["input_version"] = digest([bindings, node["dirty_seq"]])
        if len(encode(request)) > max_chars:
            raise TopicBudgetExceeded("TOPIC_INPUT_BUDGET_EXHAUSTED; no source was truncated")
        return request

    def save_summary(self, request: dict[str, Any], text: str, *, generator: dict[str, str]):
        if request["scope"] != asdict(self.scope) or not text.strip() or not generator.get("name") or not generator.get("version"):
            raise ValueError("summary requires matching scope, text and generator version")
        sid = "topic-summary:" + digest([self.scope.group_id, request["topic_id"], request["input_version"], text, generator])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            node = db.execute("SELECT dirty_seq FROM topic_nodes WHERE scope_id=? AND topic_id=?", (self.scope.group_id, request["topic_id"])).fetchone()
            if node is None or node[0] != request["dirty_seq"] or self.progress()["needs_update"]:
                raise TopicInputChanged("topic changed while generating summary")
            # Canonical writes need the same SQLite writer lock. Validation and
            # summary commit therefore cannot straddle a concurrent adoption.
            for binding in request["bindings"]:
                current = self.canonical.read_claim(binding["claim_id"], **asdict(self.scope), valid_at=now(), statuses=["ACTIVE"])
                if current is None or any(current[k] != v for k, v in binding.items()):
                    raise TopicInputChanged("summary source adoption changed")
            current_members = self.members(request["topic_id"], limit=len(request["bindings"]) + 1)
            current_bindings = [{k: r[k] for k in ("claim_id", "revision_id", "decision_id")} for r in current_members if r]
            if current_bindings != request["bindings"]:
                raise TopicInputChanged("summary membership changed")
            if digest([request["bindings"], request["dirty_seq"]]) != request["input_version"]:
                raise ValueError("summary input fingerprint mismatch")
            db.execute("INSERT OR IGNORE INTO topic_summary_revisions VALUES(?,?,?,?,?,?,?,?)",
                       (sid, self.scope.group_id, request["topic_id"], request["input_version"], text,
                        encode(request["bindings"]), encode(generator), now()))
            db.execute("UPDATE topic_nodes SET summary_id=?,refresh_after=? WHERE scope_id=? AND topic_id=?", (sid, request["refresh_after"], self.scope.group_id, request["topic_id"]))
        return sid

    def navigate(self, topic_id: str | None = None, *, offset: int = 0, limit: int = 20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("navigation paging requires offset>=0 and limit 1..100")
        progress = self.progress()
        with self.connect() as db:
            rows = db.execute("""SELECT n.*,s.text,s.bindings_json,s.generator_json FROM topic_nodes n
                LEFT JOIN topic_summary_revisions s ON s.summary_id=n.summary_id
                WHERE n.scope_id=? AND n.parent_id IS ? ORDER BY n.topic_id LIMIT ? OFFSET ?""",
                (self.scope.group_id, topic_id, limit, offset)).fetchall()
        result = []
        for r in rows:
            bindings = json.loads(r["bindings_json"] or "[]")
            dirty = progress["needs_update"] or r["summary_id"] is None
            if not dirty:
                current = self.members(r["topic_id"], limit=len(bindings) + 1)
                current_bindings = [{k: m[k] for k in ("claim_id", "revision_id", "decision_id")} for m in current if m]
                dirty = current_bindings != bindings
            result.append({"canonical_id": r["topic_id"], "node_id": "topic:" + r["topic_id"], "kind": r["kind"],
                           "view": "TOPIC_SUMMARY", "name": r["name"], "summary_id": r["summary_id"],
                           "excerpt": "" if dirty else (r["text"] or "")[:800], "needs_refresh": dirty,
                           "bindings": bindings, **progress})
        return result

    async def refresh_one(self, generator, *, generator_version: dict[str, str], max_members=100, max_chars=24000):
        if self.progress()["needs_update"]:
            raise TopicInputChanged("update topic memberships before generating summaries")
        with self.connect() as db:
            row = db.execute("SELECT topic_id FROM topic_nodes WHERE scope_id=? AND summary_id IS NULL ORDER BY dirty_seq,topic_id LIMIT 1", (self.scope.group_id,)).fetchone()
        if row is None:
            return False
        request = self.summary_input(row[0], max_members=max_members, max_chars=max_chars)
        text = await generator(request)
        self.save_summary(request, text, generator=generator_version)
        return True
