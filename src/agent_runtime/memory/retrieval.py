"""Deterministic, rebuildable retrieval views over canonical durable memory.

This module owns no embedding model and no ANN implementation. Callers may
supply a dense-search callback; lexical FTS, structured filtering, validity,
graph navigation and reciprocal-rank fusion remain provider-neutral.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

from .durable import CanonicalMemoryStore


DenseSearch = Callable[..., list[tuple[str, float]]]


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def terms(text: str) -> list[str]:
    english = re.findall(r"[a-z0-9_]{2,}", text.casefold())
    chinese = "".join(re.findall(r"[㐀-鿿]", text))
    return list(
        dict.fromkeys(
            english
            + [
                chinese[index : index + 2]
                for index in range(len(chinese) - 1)
            ]
        )
    )


def memory_document(row: dict[str, Any]) -> str:
    """Build bounded derived recall text from one canonical memory row."""
    parts = [
        str(row.get("text") or ""),
        str(row.get("kind") or ""),
        " ".join(str(item) for item in row.get("topics") or []),
        " ".join(str(item) for item in row.get("entities") or []),
    ]
    payload = (
        row.get("payload")
        if isinstance(row.get("payload"), dict)
        else {}
    )
    for key in ("goal", "decision", "prediction", "outcome", "lesson"):
        value = payload.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    for key in (
        "observations",
        "actions",
        "tool_outcomes",
        "preconditions",
        "steps",
        "recovery",
        "verification",
        "failure_modes",
        "capabilities",
    ):
        values = (
            payload.get(key)
            if isinstance(payload.get(key), list)
            else []
        )
        for value in values[:64]:
            if isinstance(value, dict):
                for field in (
                    "text",
                    "summary",
                    "action",
                    "result",
                    "status",
                ):
                    if value.get(field) not in (None, ""):
                        parts.append(str(value[field]))
            elif value not in (None, ""):
                parts.append(str(value))
    attributes = (
        payload.get("attributes")
        if isinstance(payload.get("attributes"), dict)
        else {}
    )
    for key in (
        "experience_kind",
        "source",
        "domain",
        "topic",
        "provenance_kind",
    ):
        if attributes.get(key) not in (None, ""):
            parts.append(str(attributes[key]))
    return " ".join(parts)[:12000]


class DurableMemoryRetrieval:
    """Rebuildable deterministic views over CanonicalMemoryStore."""

    def __init__(self, store: CanonicalMemoryStore) -> None:
        self.store = store
        self._ensure_schema()
        self.sync()

    def _ensure_schema(self) -> None:
        with self.store._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    body TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    vector TEXT,
                    model_id TEXT NOT NULL DEFAULT '',
                    updated_seq INTEGER NOT NULL DEFAULT 0,
                    vector_revision TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS memory_dependencies (
                    parent_id TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    PRIMARY KEY(parent_id, child_id)
                );
                CREATE INDEX IF NOT EXISTS memory_dependency_child_idx
                    ON memory_dependencies(child_id);
                CREATE INDEX IF NOT EXISTS memory_nodes_status_idx
                    ON memory_nodes(status,node_type);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_nodes_fts
                    USING fts5(node_id UNINDEXED, document);
                CREATE TABLE IF NOT EXISTS memory_node_changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_service_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute(
                    "PRAGMA table_info(memory_nodes)"
                ).fetchall()
            }
            if "vector_revision" not in columns:
                db.execute(
                    "ALTER TABLE memory_nodes ADD COLUMN "
                    "vector_revision TEXT NOT NULL DEFAULT ''"
                )

    def node(self, node_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM memory_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
        if row is None:
            raise KeyError(node_id)
        node = dict(row)
        node["metadata"] = json.loads(node["metadata"])
        return node

    def put_node(
        self,
        node_id: str,
        node_type: str,
        body: str,
        metadata: dict[str, Any],
        *,
        children: Sequence[str] = (),
        prefix: str = "",
        status: str = "ACTIVE",
    ) -> bool:
        if status == "ACTIVE" and children:
            with self.store._connect() as db:
                stale = db.execute(
                    """SELECT 1 FROM memory_nodes
                    WHERE node_id IN (SELECT value FROM json_each(?))
                    AND (
                        status!='ACTIVE'
                        OR julianday(json_extract(metadata,'$.valid_until'))
                           <= julianday('now')
                    )
                    LIMIT 1""",
                    (json.dumps(list(children)),),
                ).fetchone()
            if stale:
                status = "DIRTY"
        version = fingerprint(
            [body, metadata, list(children), prefix, status]
        )
        with self.store._connect() as db:
            previous = db.execute(
                "SELECT version,status FROM memory_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
            if (
                previous
                and previous["version"] == version
                and previous["status"] == status
            ):
                return False
            if previous:
                self.invalidate(db, node_id)
            seq = db.execute(
                "INSERT INTO memory_node_changes(node_id,status,version) "
                "VALUES(?,?,?)",
                (node_id, status, version),
            ).lastrowid
            db.execute(
                """INSERT INTO memory_nodes(
                    node_id,node_type,body,prefix,metadata,version,status,updated_seq
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    body=excluded.body,
                    prefix=excluded.prefix,
                    metadata=excluded.metadata,
                    version=excluded.version,
                    status=excluded.status,
                    vector=NULL,
                    model_id='',
                    vector_revision='',
                    updated_seq=excluded.updated_seq""",
                (
                    node_id,
                    node_type,
                    body,
                    prefix,
                    json.dumps(metadata, ensure_ascii=False),
                    version,
                    status,
                    seq,
                ),
            )
            db.execute(
                "DELETE FROM memory_nodes_fts WHERE node_id=?",
                (node_id,),
            )
            if status == "ACTIVE":
                document = prefix + " " + body
                db.execute(
                    "INSERT INTO memory_nodes_fts VALUES(?,?)",
                    (
                        node_id,
                        " ".join(terms(document)) + " " + document,
                    ),
                )
            db.execute(
                "DELETE FROM memory_dependencies WHERE parent_id=?",
                (node_id,),
            )
            db.executemany(
                "INSERT OR IGNORE INTO memory_dependencies VALUES(?,?)",
                [(node_id, child) for child in children],
            )
        return True

    @staticmethod
    def invalidate(
        db: sqlite3.Connection,
        child_id: str,
    ) -> None:
        rows = db.execute(
            """WITH RECURSIVE affected(id) AS (
                SELECT parent_id
                FROM memory_dependencies
                WHERE child_id=?
                UNION
                SELECT d.parent_id
                FROM memory_dependencies d
                JOIN affected a ON d.child_id=a.id
            )
            SELECT n.node_id,n.version
            FROM memory_nodes n
            JOIN affected a ON a.id=n.node_id""",
            (child_id,),
        ).fetchall()
        for row in rows:
            seq = db.execute(
                "INSERT INTO memory_node_changes(node_id,status,version) "
                "VALUES(?,?,?)",
                (row["node_id"], "DIRTY", row["version"]),
            ).lastrowid
            db.execute(
                "UPDATE memory_nodes SET status='DIRTY',updated_seq=? "
                "WHERE node_id=?",
                (seq, row["node_id"]),
            )

    def canonical_transition_hook(
        self,
        db: sqlite3.Connection,
        memory_id: str,
        status: str,
    ) -> None:
        self.invalidate(db, memory_id)
        db.execute(
            "UPDATE memory_nodes SET status=? WHERE node_id=?",
            (status, memory_id),
        )

    def sync(self) -> None:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT value FROM memory_service_meta "
                "WHERE key='event_cursor'"
            ).fetchone()
            cursor = int(row[0]) if row else 0
            events = db.execute(
                "SELECT event_seq,memory_id FROM memory_events "
                "WHERE event_seq>? ORDER BY event_seq",
                (cursor,),
            ).fetchall()
        for event in events:
            memory = self.store.get_memory(event["memory_id"])
            if memory is None:
                continue
            payload = memory["payload"]
            metadata = {
                "memory_id": memory["memory_id"],
                "text": memory["text"],
                "kind": memory["kind"],
                "memory_type": memory["memory_type"],
                "topics": memory["topics"],
                "entities": memory["entities"],
                "payload": payload,
                "observed_at": memory["observed_at"],
                "valid_from": memory["valid_from"],
                "valid_until": memory["valid_until"],
                "confidence": memory["confidence"],
                "canonical_version": memory["content_hash"],
            }
            prefix = (
                f"{memory['memory_type']} {memory['kind']}; "
                f"topics: {', '.join(memory['topics'])}; "
                f"entities: {', '.join(memory['entities'])}; "
                f"observed {memory['observed_at']}"
            )
            self.put_node(
                memory["memory_id"],
                "memory",
                memory_document(memory),
                metadata,
                children=list(
                    dict.fromkeys(
                        [
                            *memory["evidence_refs"],
                            *payload.get("derived_from", []),
                        ]
                    )
                ),
                prefix=prefix,
                status=memory["status"],
            )
        if events:
            with self.store._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO memory_service_meta "
                    "VALUES('event_cursor',?)",
                    (str(events[-1]["event_seq"]),),
                )
        with self.store._connect() as db:
            expired = db.execute(
                """SELECT node_id,version
                FROM memory_nodes
                WHERE status='ACTIVE'
                AND julianday(json_extract(metadata,'$.valid_until'))
                    <= julianday('now')"""
            ).fetchall()
            for row in expired:
                self.invalidate(db, row["node_id"])
                seq = db.execute(
                    "INSERT INTO memory_node_changes(node_id,status,version) "
                    "VALUES(?,?,?)",
                    (
                        row["node_id"],
                        "EXPIRED",
                        row["version"],
                    ),
                ).lastrowid
                db.execute(
                    "UPDATE memory_nodes SET status='EXPIRED',updated_seq=? "
                    "WHERE node_id=?",
                    (seq, row["node_id"]),
                )

    def valid(self, node: dict[str, Any]) -> bool:
        if node["status"] != "ACTIVE":
            return False
        until = node["metadata"].get("valid_until")
        start = node["metadata"].get("valid_from")

        def date(value: str) -> datetime:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
            return (
                parsed
                if parsed.tzinfo
                else parsed.replace(tzinfo=timezone.utc)
            )

        now = datetime.now(timezone.utc)
        return (
            (not until or date(until) > now)
            and (not start or date(start) <= now)
        )

    def card(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_id": node["node_id"],
            "type": node["node_type"],
            "text": node["body"][:700],
            "context": node["prefix"][:300],
            "version": node["version"],
            "status": node["status"],
            "topics": node["metadata"].get("topics", []),
            "change_seq": node["updated_seq"],
        }

    def search(
        self,
        query: str,
        *,
        topic: str = "",
        limit: int = 8,
        node_types: Sequence[str] = (),
        source_ids: Sequence[str] = (),
        dense_search: DenseSearch | None = None,
    ) -> list[dict[str, Any]]:
        self.sync()
        words = terms(query)
        filter_sql = "n.status='ACTIVE'"
        filter_params: list[Any] = []
        if node_types:
            filter_sql += (
                " AND n.node_type IN ("
                + ",".join("?" for _ in node_types)
                + ")"
            )
            filter_params.extend(node_types)
        if source_ids:
            filter_sql += (
                " AND json_extract(n.metadata,'$.evidence_id') "
                "IN (SELECT value FROM json_each(?))"
            )
            filter_params.append(json.dumps(list(source_ids)))
        match = " OR ".join(
            '"' + word.replace('"', '""') + '"'
            for word in words[:32]
        )
        with self.store._connect() as db:
            lexical = (
                db.execute(
                    "SELECT memory_nodes_fts.node_id,"
                    "bm25(memory_nodes_fts) score "
                    "FROM memory_nodes_fts "
                    "JOIN memory_nodes n "
                    "ON n.node_id=memory_nodes_fts.node_id "
                    "WHERE memory_nodes_fts MATCH ? AND "
                    + filter_sql
                    + " ORDER BY score LIMIT 32",
                    [match, *filter_params],
                ).fetchall()
                if match
                else []
            )
        dense = (
            dense_search(
                query,
                filter_sql=filter_sql,
                filter_params=tuple(filter_params),
                limit=32,
            )
            if dense_search is not None
            else []
        )
        scores: dict[str, float] = {}
        for ranking in (
            [(row["node_id"], row["score"]) for row in lexical],
            dense,
        ):
            for rank, (node_id, _score) in enumerate(ranking, 1):
                scores[node_id] = scores.get(node_id, 0.0) + 1 / (
                    60 + rank
                )
        nodes: list[dict[str, Any]] = []
        for node_id, _score in sorted(
            scores.items(),
            key=lambda row: -row[1],
        ):
            node = self.node(node_id)
            if self.valid(node) and (
                not node_types
                or node["node_type"] in node_types
            ):
                nodes.append(node)
        if topic and not source_ids:
            with self.store._connect() as db:
                ids = db.execute(
                    """SELECT node_id FROM memory_nodes
                    WHERE status='ACTIVE'
                    AND EXISTS (
                        SELECT 1
                        FROM json_each(metadata,'$.topics')
                        WHERE value=?
                    )
                    LIMIT 16""",
                    (topic,),
                ).fetchall()
            present = {node["node_id"] for node in nodes}
            for row in ids:
                node = self.node(row[0])
                if (
                    node["node_id"] not in present
                    and self.valid(node)
                    and (
                        not node_types
                        or node["node_type"] in node_types
                    )
                ):
                    nodes.append(node)
        if not nodes and source_ids:
            with self.store._connect() as db:
                staged = db.execute(
                    "SELECT node_id FROM memory_nodes n WHERE "
                    + filter_sql
                    + " ORDER BY "
                    "COALESCE(json_extract(metadata,'$.start'),0),"
                    "node_id LIMIT 32",
                    filter_params,
                ).fetchall()
            nodes = [self.node(row[0]) for row in staged]
            nodes = [node for node in nodes if self.valid(node)]
        nodes = nodes[:32]
        return [
            self.card(node)
            for node in nodes[:limit]
        ]

    def read(
        self,
        node_id: str,
        *,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.sync()
        node = self.node(node_id)
        if not self.valid(node):
            return {
                "status": node["status"],
                "needs_refresh": True,
                "version": node["version"],
            }
        body = node["body"]
        return {
            "type": node["node_type"],
            "text": body[offset : offset + 900],
            "offset": offset,
            "next_offset": (
                offset + 900
                if offset + 900 < len(body)
                else None
            ),
            "version": node["version"],
            "context": node["prefix"][:300],
            "evidence_id": node["metadata"].get("evidence_id"),
            "source_start": node["metadata"].get("start"),
            "node_id": node_id,
        }

    def expand(
        self,
        node_id: str,
        *,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.sync()
        node = self.node(node_id)
        if not self.valid(node):
            raise ValueError(
                "summary or source version is stale; search again"
            )
        with self.store._connect() as db:
            children = db.execute(
                """SELECT d.child_id
                FROM memory_dependencies d
                JOIN memory_nodes n ON n.node_id=d.child_id
                WHERE parent_id=?
                ORDER BY
                    COALESCE(json_extract(n.metadata,'$.start'),0),
                    child_id
                LIMIT 8 OFFSET ?""",
                (node_id, offset),
            ).fetchall()
        nodes = [self.node(row[0]) for row in children]
        return [
            self.card(child)
            for child in nodes
            if self.valid(child)
        ]

    def relations(
        self,
        node_id: str,
        *,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.sync()
        node = self.node(node_id)
        if not self.valid(node):
            raise ValueError(
                "relationship origin is stale; search again"
            )
        entities = node["metadata"].get("entities", [])
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT DISTINCT n.node_id
                FROM memory_nodes n
                WHERE n.status='ACTIVE' AND (
                    n.node_id IN (
                        SELECT child_id
                        FROM memory_dependencies
                        WHERE parent_id=?
                    )
                    OR n.node_id IN (
                        SELECT parent_id
                        FROM memory_dependencies
                        WHERE child_id=?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM json_each(n.metadata,'$.entities') e
                        WHERE e.value IN (
                            SELECT value FROM json_each(?)
                        )
                    )
                )
                ORDER BY n.updated_seq DESC
                LIMIT 8 OFFSET ?""",
                (
                    node_id,
                    node_id,
                    json.dumps(entities),
                    offset,
                ),
            ).fetchall()
        nodes = [
            self.node(row[0])
            for row in rows
            if row[0] != node_id
        ]
        return [
            self.card(candidate)
            for candidate in nodes
            if self.valid(candidate)
        ]

    def changes(
        self,
        since: int = 0,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        self.sync()
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_node_changes "
                "WHERE seq>? ORDER BY seq LIMIT ?",
                (since, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                card = self.card(self.node(row["node_id"]))
            except KeyError:
                card = {
                    "node_id": row["node_id"],
                    "type": "removed_view",
                    "text": "",
                    "status": "NEEDS_REBUILD",
                }
            result.append(
                {
                    **card,
                    "change_seq": row["seq"],
                    "event_status": row["status"],
                    "event_version": row["version"],
                }
            )
        return result

    def clear_canonical_views(self) -> None:
        """Clear only generic rebuildable views; canonical memory survives."""
        with self.store._connect() as db:
            for table in (
                "memory_nodes",
                "memory_dependencies",
                "memory_nodes_fts",
            ):
                db.execute("DELETE FROM " + table)
            db.execute(
                "DELETE FROM memory_service_meta "
                "WHERE key='event_cursor'"
            )
        self.sync()


__all__ = [
    "DenseSearch",
    "DurableMemoryRetrieval",
    "fingerprint",
    "memory_document",
    "terms",
]
