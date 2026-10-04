"""Durable evidence-source archive and revision tracking for memory.

This module stores exact source revisions and projects them into the generic
retrieval graph. It is deterministic and model-free. Research, learning and
contextualization may consume it without owning its persistence rules.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .durable import CanonicalMemoryStore
from .retrieval import DurableMemoryRetrieval, fingerprint


class DurableEvidenceSources:
    def __init__(
        self,
        store: CanonicalMemoryStore,
        retrieval: DurableMemoryRetrieval,
    ) -> None:
        self.store = store
        self.retrieval = retrieval
        self.root = store.root
        self._ensure_schema()
        self.repair_missing_views()

    def _ensure_schema(self) -> None:
        with self.store._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS evidence_sources (
                    source_id TEXT PRIMARY KEY,
                    archive TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    observed_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            columns = {
                str(row["name"])
                for row in db.execute(
                    "PRAGMA table_info(evidence_sources)"
                ).fetchall()
            }
            if "observed_at" not in columns:
                db.execute(
                    "ALTER TABLE evidence_sources "
                    "ADD COLUMN observed_at TEXT NOT NULL DEFAULT ''"
                )

    def current(
        self,
        source_id: str,
    ) -> dict[str, Any] | None:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM evidence_sources WHERE source_id=?",
                (str(source_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def read_original(
        self,
        source_id: str,
    ) -> dict[str, Any] | None:
        row = self.current(source_id)
        if row is None:
            return None
        try:
            value = json.loads(
                Path(row["archive"]).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def ingest(
        self,
        source: Mapping[str, Any],
        *,
        topic: str = "",
        activate: bool = True,
        observed_at: str = "",
    ) -> str:
        body = str(
            source.get("evidence_text")
            or source.get("summary")
            or ""
        )
        source_id = str(source.get("evidence_id") or "")
        if not source_id:
            raise ValueError("source evidence_id is required")
        version = fingerprint(body)
        root_id = "source:" + source_id

        archive = (
            self.root
            / "evidence"
            / (fingerprint(source_id) + "-" + version + ".json")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            archive.write_text(
                json.dumps(dict(source), ensure_ascii=False),
                encoding="utf-8",
            )

        pointer = self.current(source_id)
        try:
            self.retrieval.node(root_id)
            current_view = True
        except KeyError:
            current_view = False

        observed_at = (
            observed_at
            or datetime.now(timezone.utc).isoformat()
        )
        if (
            pointer
            and pointer["source_version"] == version
            and current_view
        ):
            return root_id
        if pointer and pointer["source_version"] != version:
            if not activate or (
                pointer["observed_at"]
                and observed_at < pointer["observed_at"]
            ):
                return root_id
        if pointer and pointer["source_version"] == version:
            observed_at = pointer["observed_at"] or observed_at

        with self.store._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evidence_sources(
                    source_id,archive,topic,source_version,observed_at
                ) VALUES(?,?,?,?,?)""",
                (
                    source_id,
                    str(archive),
                    topic,
                    version,
                    observed_at,
                ),
            )

        prefix = (
            f"Source {source.get('title','')}; "
            f"topic {topic}; "
            f"observed {source.get('published_at','')}; "
            f"URL {source.get('url','')}"
        )
        metadata = {
            "evidence_id": source_id,
            "source_version": version,
            "archive": str(archive),
            "topic": topic,
            "topics": [topic],
            "title": source.get("title", ""),
            "url": source.get("url", ""),
            "source_revision": source.get(
                "source_revision",
                "",
            ),
        }

        chunk_ids: list[str] = []
        for start in range(0, len(body), 550):
            text = body[start : start + 650]
            if len(text) < 12:
                continue
            node_id = (
                "evidence:"
                + fingerprint([source_id, version, start])[:28]
            )
            chunk_ids.append(node_id)
            try:
                self.retrieval.node(node_id)
                exists = True
            except KeyError:
                exists = False
            if not exists:
                self.retrieval.put_node(
                    node_id,
                    "evidence",
                    text,
                    {
                        **metadata,
                        "start": start,
                        "end": start + len(text),
                    },
                    prefix=prefix,
                )

        previous_version: str | None = None
        try:
            previous = self.retrieval.node(root_id)
            previous_version = str(
                previous["metadata"].get("source_version") or ""
            )
        except KeyError:
            previous = None

        if previous is not None and previous_version != version:
            with self.store._connect() as db:
                old = db.execute(
                    "SELECT child_id FROM memory_dependencies "
                    "WHERE parent_id=?",
                    (root_id,),
                ).fetchall()
                for child in old:
                    db.execute(
                        "UPDATE memory_nodes SET status='SUPERSEDED' "
                        "WHERE node_id=?",
                        (child[0],),
                    )
                    self.retrieval.invalidate(db, child[0])
                self.retrieval.invalidate(db, root_id)
                self.retrieval.invalidate(db, source_id)
                disputed = db.execute(
                    """SELECT m.memory_id
                    FROM memories m
                    LEFT JOIN memory_nodes n
                      ON n.node_id=m.memory_id
                    WHERE m.status='ACTIVE'
                    AND (
                        n.status='DIRTY'
                        OR EXISTS (
                            SELECT 1
                            FROM json_each(m.evidence_refs_json)
                            WHERE value=?
                        )
                    )""",
                    (source_id,),
                ).fetchall()
            for row in disputed:
                self.store.transition_memory(
                    row[0],
                    status="DISPUTED",
                    event_type="EVIDENCE_REVISION_INVALIDATED",
                )

        self.retrieval.put_node(
            root_id,
            "source",
            str(
                source.get("summary")
                or source.get("title")
                or ""
            )[:1800],
            metadata,
            children=chunk_ids,
            prefix=prefix,
        )
        return root_id

    def repair_missing_views(self) -> int:
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT s.*
                FROM evidence_sources s
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM memory_nodes n
                    WHERE n.node_id='source:'||s.source_id
                )"""
            ).fetchall()
        repaired = 0
        for row in rows:
            try:
                source = json.loads(
                    Path(row["archive"]).read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(source, dict):
                continue
            self.ingest(
                source,
                topic=row["topic"],
                observed_at=row["observed_at"],
            )
            repaired += 1
        return repaired

    def rebuild_source_views(self) -> int:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM evidence_sources "
                "ORDER BY source_id"
            ).fetchall()
        rebuilt = 0
        for row in rows:
            try:
                source = json.loads(
                    Path(row["archive"]).read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(source, dict):
                continue
            self.ingest(
                source,
                topic=row["topic"],
                observed_at=row["observed_at"],
            )
            rebuilt += 1
        return rebuilt


__all__ = ["DurableEvidenceSources"]
