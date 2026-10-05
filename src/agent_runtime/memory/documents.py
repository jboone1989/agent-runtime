"""LlamaIndex document-local hierarchy, saved as deterministic source spans.

No model or global LlamaIndex Settings is used. SDK IDs never leave the adapter.
An immutable artifact makes interruption/rebuild independent of parser changes.
"""
from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import version
import json
import sqlite3

from .projection_scope import ProjectionScope
from .revisions import digest, encode


class DocumentHierarchy:
    def __init__(self, canonical, scope: ProjectionScope, *, chunk_sizes=(2048, 512, 128), overlap=20,
                 max_source_chars=200000):
        if (not chunk_sizes or any(type(n) is not int or n <= overlap for n in chunk_sizes)
                or any(a <= b for a, b in zip(chunk_sizes, chunk_sizes[1:])) or overlap < 0 or max_source_chars <= 0):
            raise ValueError("document chunk sizes must descend and exceed overlap; source budget must be positive")
        self.canonical, self.scope = canonical, scope
        self.config = {"chunk_sizes": list(chunk_sizes), "overlap": overlap, "max_source_chars": max_source_chars}
        self.provider_version = "llama-index-core/" + version("llama-index-core") + ";source-spans/1"
        self.key = digest([self.provider_version, self.config])
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS document_hierarchy_artifacts (
                scope_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version TEXT NOT NULL,
                parser_version TEXT NOT NULL, artifact_json TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                PRIMARY KEY(scope_id,source_id,source_version,parser_version))""")

    def connect(self):
        return sqlite3.connect(self.canonical.db_path, timeout=10)

    def parse(self, source_id: str, text: str, *, source_version: str):
        if not source_id.strip() or not source_version.strip():
            raise ValueError("a canonical source ID and version are required")
        if len(text) > self.config["max_source_chars"]:
            raise ValueError("DOCUMENT_SOURCE_BUDGET_EXHAUSTED; page the source explicitly")
        identity = (self.scope.group_id, source_id, source_version, self.key)
        with self.connect() as db:
            existing = db.execute("SELECT artifact_json,artifact_hash FROM document_hierarchy_artifacts WHERE scope_id=? AND source_id=? AND source_version=? AND parser_version=?", identity).fetchone()
        if existing:
            artifact = json.loads(existing[0])
            if digest(artifact) != existing[1] or artifact["content_hash"] != digest(text):
                raise ValueError("document artifact or source version changed")
            return artifact

        from llama_index.core import Document
        from llama_index.core.node_parser import HierarchicalNodeParser
        from llama_index.core.schema import MetadataMode
        parser = HierarchicalNodeParser.from_defaults(chunk_sizes=self.config["chunk_sizes"],
                 chunk_overlap=self.config["overlap"], include_metadata=False, include_prev_next_rel=False)
        document = Document(text=text, id_=source_id)
        nodes = parser.get_nodes_from_documents([document])
        native = {n.node_id: n for n in nodes}
        spans, visiting = {}, set()

        def locate(node):
            if node.node_id in spans:
                return spans[node.node_id]
            if node.node_id in visiting:
                raise ValueError("document parser returned a cycle")
            visiting.add(node.node_id)
            body = node.get_content(metadata_mode=MetadataMode.NONE)
            parent = node.parent_node
            if parent and parent.node_id in native:
                parent_span = locate(native[parent.node_id])
                parent_start = parent_span[0]
                parent_text = text[parent_span[0]:parent_span[1]]
                depth = parent_span[2] + 1
            else:
                parent_start, parent_text, depth = 0, text, 0
            local = node.start_char_idx
            if local is None or parent_text[local:local + len(body)] != body:
                raise ValueError("parser output has no exact source span")
            start, end = parent_start + local, parent_start + local + len(body)
            if text[start:end] != body:
                raise ValueError("document node does not match original evidence")
            spans[node.node_id] = (start, end, depth)
            visiting.remove(node.node_id)
            return spans[node.node_id]

        ids = {}
        def stable_id(node):
            if node.node_id in ids:
                return ids[node.node_id]
            start, end, depth = locate(node)
            parent = node.parent_node
            parent_id = stable_id(native[parent.node_id]) if parent and parent.node_id in native else None
            ids[node.node_id] = "document-chunk:" + digest([identity, parent_id, start, end, depth])
            return ids[node.node_id]
        for node in nodes:
            stable_id(node)
        chunks = []
        for node in nodes:
            start, end, depth = spans[node.node_id]
            parent = node.parent_node
            chunks.append({"canonical_id": source_id, "node_id": ids[node.node_id], "kind": "EVIDENCE_FRAGMENT",
                           "source_version": source_version, "source_spans": [{"ref": source_id, "start": start, "end": end}],
                           "body": text[start:end], "depth": depth,
                           "parent_id": ids.get(parent.node_id) if parent else None})
        # One-chunk documents can produce identical spans at different levels;
        # depth preserves those identities and parent links.
        chunks.sort(key=lambda c: (c["source_spans"][0]["start"], c["depth"], c["node_id"]))
        artifact = {"canonical_id": source_id, "source_version": source_version, "content_hash": digest(text),
                    "provider_version": self.provider_version, "scope": asdict(self.scope), "chunks": chunks}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO document_hierarchy_artifacts VALUES(?,?,?,?,?,?)", (*identity, encode(artifact), digest(artifact)))
            saved = db.execute("SELECT artifact_hash FROM document_hierarchy_artifacts WHERE scope_id=? AND source_id=? AND source_version=? AND parser_version=?", identity).fetchone()[0]
            if saved != digest(artifact):
                raise ValueError("concurrent document derivation changed")
        return artifact

    @staticmethod
    def expand(artifact, parent_id: str | None, *, offset=0, limit=8):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("document paging requires offset>=0 and limit 1..100")
        children = [c for c in artifact["chunks"] if c["parent_id"] == parent_id]
        return children[offset:offset + limit]

    def load(self, source_id: str, source_version: str):
        with self.connect() as db:
            row = db.execute("SELECT artifact_json,artifact_hash FROM document_hierarchy_artifacts WHERE scope_id=? AND source_id=? AND source_version=? AND parser_version=?",
                             (self.scope.group_id, source_id, source_version, self.key)).fetchone()
        if row is None:
            raise KeyError(source_id)
        artifact = json.loads(row[0])
        if digest(artifact) != row[1]:
            raise RuntimeError("document artifact failed integrity validation")
        return artifact

    def for_node(self, node_id: str):
        # Apply scope and parser version before inspecting the stored JSON.
        with self.connect() as db:
            row = db.execute("""SELECT source_id,source_version FROM document_hierarchy_artifacts
                WHERE scope_id=? AND parser_version=? AND EXISTS (
                    SELECT 1 FROM json_each(artifact_json,'$.chunks')
                    WHERE json_extract(value,'$.node_id')=?) LIMIT 1""",
                (self.scope.group_id, self.key, node_id)).fetchone()
        if row is None:
            raise KeyError(node_id)
        return self.load(*row)
