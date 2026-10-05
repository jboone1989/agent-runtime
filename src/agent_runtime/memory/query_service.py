"""Scoped retrieval tools over canonical authority and committed provider views.

No parsing, embedding, summarization or index updates run on the query path.
Caller-owned query embedding and graph IPC callbacks execute outside the supervisor.
"""
from __future__ import annotations

from dataclasses import asdict
import json

from .archive import EvidenceArchive
from .revisions import digest, now
from .session import MemoryToolInputError


class MemoryIndexBehind(RuntimeError):
    pass


class MemoryQueryService:
    def __init__(self, canonical, scope, *, content, documents, topics, graph_reader=None,
                 query_embedder=None, valid_at=None, known_at=None, page_chars=2400):
        if any(provider.scope != scope for provider in (content, documents, topics)):
            raise ValueError("all memory query providers must share the bound scope")
        if type(page_chars) is not int or not 1 <= page_chars <= 12000:
            raise ValueError("original read page budget must be 1..12000 characters")
        self.canonical, self.scope = canonical, scope
        self.content, self.documents, self.topics = content, documents, topics
        self.graph_reader, self.query_embedder = graph_reader, query_embedder
        self.valid_at, self.known_at, self.page_chars = valid_at, known_at, page_chars
        self.archive = EvidenceArchive(canonical, scope)
        self.content.normalize = self.normalize_content

    def _claim(self, claim_id):
        record = self.canonical.read_claim(claim_id, **asdict(self.scope),
                                           valid_at=self.valid_at or now(), known_at=self.known_at)
        if record is None:
            raise KeyError(claim_id)
        return record

    def _card(self, record, *, node_id=None, text=None, spans=None, view="CANONICAL_RECORD", **extra):
        active = record["status"] == "ACTIVE"
        return {"node_id": node_id or record["claim_id"], "canonical_id": record["claim_id"],
                "revision_id": record["revision_id"], "version": digest([record["revision_id"], record["decision_id"]]),
                "kind": record["kind"], "text": (text if text is not None else record["text"])[:800] if active else "",
                "status": record["status"], "needs_refresh": False,
                "source_spans": spans or [], "source_refs": record["source_refs"],
                "evidence_refs": record["evidence_refs"], "scope": asdict(self.scope), "view": view,
                "valid_from": record.get("valid_from"), "valid_to": record.get("valid_to"),
                "known_from": record["adopted_at"], "valid_at": self.valid_at or now(), "known_at": self.known_at,
                "provider_version": "canonical-query/1", "indexed_through": None,
                "ranking_reason": "exact scoped canonical identity", **extra}

    def normalize_content(self, candidate):
        if candidate.get("scope") != asdict(self.scope):
            return None
        try:
            record = self.canonical.read_claim(candidate["canonical_id"], **asdict(self.scope),
                valid_at=candidate["valid_at"], known_at=candidate["known_at"], statuses=["ACTIVE"])
            if record is None or record["revision_id"] != candidate["revision_id"]:
                return None
            artifact = self.documents.load(record["claim_id"], record["revision_id"])
            chunk = next(c for c in artifact["chunks"] if c["node_id"] == candidate["node_id"])
        except (KeyError, StopIteration):
            return None
        body = self.archive.content(record)
        if (digest(body) != artifact["content_hash"] or candidate["source_spans"] != chunk["source_spans"]
                or candidate["excerpt"] != chunk["body"][:800]):
            raise RuntimeError("content projection fragment differs from immutable original")
        card = self._card(record, node_id=chunk["node_id"], text=chunk["body"],
                          spans=chunk["source_spans"], view="CONTENT_FRAGMENT",
                          provider_version=candidate["provider_version"], indexed_through=candidate["indexed_through"],
                          ranking_reason=candidate["ranking_reason"], valid_at=candidate["valid_at"], known_at=candidate["known_at"])
        return card

    def progress(self):
        with self.canonical._connect() as db:
            latest = db.execute("""SELECT COALESCE(MAX(a.adoption_seq),0) FROM memory_adoptions a
                JOIN memory_revisions r ON r.revision_id=a.revision_id
                WHERE r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?""",
                tuple(asdict(self.scope).values())).fetchone()[0]
        content = self.content.progress()
        return {"scope": asdict(self.scope), "canonical_through": latest,
                "content": {**content, "needs_update": content["indexed_through"] < latest},
                "topics": self.topics.progress()}

    def search(self, query, *, topic="", limit=8, node_types=(), source_ids=()):
        if not query.strip() or type(limit) is not int or not 1 <= limit <= 100:
            raise MemoryToolInputError("query and a limit between 1 and 100 are required")
        ids = None
        if topic:
            if self.topics.progress()["needs_update"]:
                raise MemoryIndexBehind("topic membership index is behind canonical")
            members = self.topics.members(topic, limit=1000, valid_at=self.valid_at, known_at=self.known_at)
            if len(members) == 1000:
                raise MemoryToolInputError("TOPIC_FILTER_BUDGET_EXHAUSTED; choose a subtopic")
            ids = [r["claim_id"] for r in members if r]
        if source_ids:
            # Filter source identities in canonical SQL before provider ranking.
            with self.canonical._connect() as db:
                placeholders = ",".join("?" for _ in source_ids)
                rows = db.execute("""WITH ranked AS (SELECT r.*,a.status,
                    ROW_NUMBER() OVER(PARTITION BY r.claim_id ORDER BY a.recorded_at DESC,a.adoption_seq DESC) AS rank
                    FROM memory_revisions r JOIN memory_adoptions a ON a.revision_id=r.revision_id
                    WHERE r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                    AND r.recorded_at<=? AND a.recorded_at<=?
                    AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_to IS NULL OR r.valid_to>?))
                    SELECT claim_id FROM ranked WHERE rank=1 AND status='ACTIVE'
                    AND (claim_id IN (""" + placeholders + ") OR EXISTS (SELECT 1 FROM json_each(record_json,'$.source_refs') WHERE value IN (" + placeholders + "))) LIMIT 1001",
                    (*asdict(self.scope).values(), self.known_at or now(), self.known_at or now(),
                     self.valid_at or now(), self.valid_at or now(), *source_ids, *source_ids)).fetchall()
            if len(rows) > 1000:
                raise MemoryToolInputError("SOURCE_FILTER_BUDGET_EXHAUSTED; refine source identities")
            scoped_ids = {r[0] for r in rows}
            ids = sorted(scoped_ids if ids is None else scoped_ids.intersection(ids))
        vector = self.query_embedder(query) if self.query_embedder else None
        items = self.content.search(query, query_vector=vector, limit=limit, candidate_budget=max(32, limit * 4),
                    valid_at=self.valid_at, known_at=self.known_at, canonical_ids=ids,
                    kinds=list(node_types) if node_types else None)
        progress = self.progress()
        if not items and progress["content"]["needs_update"]:
            raise MemoryIndexBehind("content index is behind canonical: " + json.dumps(progress))
        return [{**item, "needs_update": progress["content"]["needs_update"]} for item in items]

    def _chunk(self, node_id):
        artifact = self.documents.for_node(node_id)
        record = self._claim(artifact["canonical_id"])
        chunk = next(c for c in artifact["chunks"] if c["node_id"] == node_id)
        if record["revision_id"] != artifact["source_version"]:
            return record, artifact, chunk, False
        if digest(self.archive.content(record)) != artifact["content_hash"]:
            raise RuntimeError("document source integrity validation failed")
        return record, artifact, chunk, True

    def _topic(self, node_id):
        topic_id = node_id.removeprefix("topic:")
        with self.topics.connect() as db:
            row = db.execute("""SELECT n.*,s.text,s.bindings_json FROM topic_nodes n
                LEFT JOIN topic_summary_revisions s ON s.summary_id=n.summary_id
                WHERE n.scope_id=? AND n.topic_id=?""", (self.scope.group_id, topic_id)).fetchone()
        if row is None:
            raise KeyError(node_id)
        progress = self.topics.progress()
        bindings = json.loads(row["bindings_json"] or "[]")
        current = self.topics.members(topic_id, limit=min(1000, len(bindings) + 1),
                                      valid_at=self.valid_at, known_at=self.known_at)
        current_bindings = [{k: r[k] for k in ("claim_id", "revision_id", "decision_id")} for r in current if r]
        dirty = progress["needs_update"] or not row["summary_id"] or current_bindings != bindings
        # Current summaries are not historical knowledge. Older-time reads
        # navigate exact historical members and cannot expose a later summary.
        if self.known_at or self.valid_at:
            dirty = True
        return {"node_id": node_id, "canonical_id": topic_id, "kind": row["kind"],
                "version": digest([row["summary_id"], row["dirty_seq"], progress["canonical_through"], dirty]),
                "text": "" if dirty else row["text"][:800], "name": row["name"],
                "status": "DIRTY" if dirty else "ACTIVE", "needs_refresh": dirty, "view": "TOPIC_SUMMARY",
                "summary_id": row["summary_id"], "source_spans": [], "bindings": bindings,
                "scope": asdict(self.scope), "provider_version": progress["provider_version"],
                "indexed_through": progress["indexed_through"], "ranking_reason": "scoped SQL topic navigation"}

    def snapshot(self, node_id):
        if node_id.startswith("topic:"):
            return self._topic(node_id)
        if node_id.startswith("document-chunk:"):
            record, artifact, chunk, current = self._chunk(node_id)
            return self._card(record, node_id=node_id, text=chunk["body"] if current else "",
                spans=chunk["source_spans"] if current else [], view="CONTENT_FRAGMENT",
                needs_refresh=not current, status=record["status"] if current else "SUPERSEDED",
                provider_version=artifact["provider_version"])
        return self._card(self._claim(node_id))

    def read(self, node_id, *, offset=0):
        if type(offset) is not int or offset < 0:
            raise MemoryToolInputError("original read offset must be nonnegative")
        card = self.snapshot(node_id)
        if card["needs_refresh"] or card["status"] != "ACTIVE":
            raise MemoryToolInputError("memory view changed or is inactive; search again")
        if node_id.startswith("topic:"):
            with self.topics.connect() as db:
                body = db.execute("SELECT text FROM topic_summary_revisions WHERE summary_id=? AND scope_id=?",
                                  (card["summary_id"], self.scope.group_id)).fetchone()[0]
            start = 0
        elif node_id.startswith("document-chunk:"):
            record, _, chunk, current = self._chunk(node_id)
            if not current or record["status"] != "ACTIVE":
                raise MemoryToolInputError("memory changed during original read; search again")
            body, start = chunk["body"], chunk["source_spans"][0]["start"]
        else:
            record = self._claim(node_id)
            if card["revision_id"] != record["revision_id"] or record["status"] != "ACTIVE":
                raise MemoryToolInputError("memory changed during original read; search again")
            body, start = self.archive.content(record), 0
        if offset > len(body):
            raise MemoryToolInputError("read offset exceeds the source length")
        end = min(len(body), offset + self.page_chars)
        view = card["view"] if node_id.startswith("topic:") else (
            "EVIDENCE_ORIGINAL" if record.get("payload", {}).get("evidence_archive") else "CANONICAL_RECORD")
        spans = card["source_spans"] if node_id.startswith("topic:") else ([{
            "ref": card["canonical_id"], "start": start + offset, "end": start + end
        }] if offset < end else [])
        return {**card, "view": view, "source_spans": spans, "text": body[offset:end], "offset": offset,
                "source_offset": start + offset, "source_end": start + end,
                "next_offset": end if end < len(body) else None, "total_chars": len(body)}

    def expand(self, node_id, *, offset=0):
        if type(offset) is not int or offset < 0:
            raise MemoryToolInputError("expand offset must be nonnegative")
        if node_id.startswith("topic:"):
            self._topic(node_id)
            members = self.topics.members(node_id.removeprefix("topic:"), offset=offset, limit=8,
                                          valid_at=self.valid_at, known_at=self.known_at)
            return [self._card(r) for r in members if r]
        if node_id.startswith("document-chunk:"):
            record, artifact, _, current = self._chunk(node_id)
            if not current or record["status"] != "ACTIVE":
                raise MemoryToolInputError("document changed; search again")
            parent_id = node_id
        else:
            record = self._claim(node_id)
            if record["status"] != "ACTIVE":
                raise MemoryToolInputError("document is inactive")
            try:
                artifact = self.documents.load(node_id, record["revision_id"])
            except KeyError:
                raise MemoryIndexBehind("document hierarchy has not indexed this revision") from None
            parent_id = None
        return [self.snapshot(c["node_id"]) for c in self.documents.expand(artifact, parent_id, offset=offset, limit=8)]

    def relations(self, node_id, *, offset=0):
        if self.graph_reader is None:
            raise RuntimeError("Graphiti query provider is not configured")
        record = self._claim(self.snapshot(node_id)["canonical_id"])
        from .graph_projection import adopted_relations
        candidates = self.graph_reader(record, offset=offset, limit=8,
            valid_at=self.valid_at or now(), known_at=self.known_at)
        cards = []
        for candidate in candidates:
            if candidate.get("scope") != asdict(self.scope):
                continue
            try:
                current = self._claim(candidate["canonical_id"])
            except KeyError:
                continue
            if current["status"] != "ACTIVE" or current["revision_id"] != candidate["revision_id"]:
                continue
            relation_id = candidate.get("relation", {}).get("relation_id")
            relation = next((r for r in adopted_relations(current) if r["relation_id"] == relation_id), None)
            if relation is not None:
                cards.append(self._card(current, view="GRAPH_RELATION", relation=relation,
                    spans=relation["source_spans"], provider_version=candidate["provider_version"],
                    indexed_through=candidate["indexed_through"], ranking_reason=candidate["ranking_reason"]))
        return cards

    def changes(self, since=0, *, limit=8):
        if type(since) is not int or since < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise MemoryToolInputError("change cursor and limit are invalid")
        with self.canonical._connect() as db:
            rows = db.execute("""SELECT a.adoption_seq,a.recorded_at,r.claim_id FROM memory_adoptions a
                JOIN memory_revisions r ON r.revision_id=a.revision_id
                WHERE a.adoption_seq>? AND r.tenant_id=? AND r.owner_id=? AND r.agent_id=? AND r.access_scope=?
                AND a.recorded_at<=? ORDER BY a.adoption_seq LIMIT ?""",
                (since, *asdict(self.scope).values(), self.known_at or now(), limit)).fetchall()
        result = []
        for row in rows:
            try:
                card = self.snapshot(row["claim_id"])
            except KeyError:
                card = {"node_id": row["claim_id"], "canonical_id": row["claim_id"], "text": "",
                        "status": "OUTSIDE_VALID_TIME", "version": digest(list(row)),
                        "scope": asdict(self.scope), "needs_refresh": True}
            result.append({**card, "change_seq": row["adoption_seq"]})
        return result

    async def contextualize(self, runtime, node_ids):
        # Contextualization is a saved worker derivation. Reads must not load
        # a model or mutate provider views inside the task's decision loop.
        return 0
