"""One scoped PostgreSQL content projection: pgvector plus explicit lexical FTS.

Canonical records and source archives remain caller-owned. This adapter stores
fragments, not a second adoption authority. All search filters apply before
ranking; the caller verifies each candidate against canonical before exposing it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Callable

from .projection_scope import ProjectionScope
from .revisions import canonical_time, digest, now


def validate_vector(vector, dimensions: int):
    if (not isinstance(vector, (list, tuple)) or len(vector) != dimensions
            or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in vector)
            or not any(n != 0 for n in vector)):
        raise ValueError("embedding must be a finite nonzero vector with the configured dimensions")
    return [float(n) for n in vector]


class ChineseLexical:
    """Use Jieba search segmentation for Chinese and ordinary English word tokens.

    The selected dictionary version must be recorded by the caller. Importing
    this module does not load a dictionary or embedding model in the supervisor.
    """
    version = "jieba/0.42.1;search-mode/1"

    def __init__(self):
        import jieba
        self.tokenizer = jieba.Tokenizer()

    def __call__(self, text: str):
        tokens = []
        for part in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9_]+", text.casefold()):
            if re.search(r"[\u3400-\u9fff]", part):
                tokens.extend(self.tokenizer.cut_for_search(part))
            else:
                tokens.append(part)
        return " ".join(tokens)


@dataclass(frozen=True)
class ContentFragment:
    canonical_id: str
    revision_id: str
    fragment_id: str
    body: str
    kind: str
    source_spans: list[dict[str, Any]]
    known_from: str
    valid_from: str | None = None
    valid_to: str | None = None
    status: str = "ACTIVE"

    def __post_init__(self):
        if any(not v.strip() for v in (self.canonical_id, self.revision_id, self.fragment_id, self.body, self.kind)):
            raise ValueError("fragment identity, revision, body and kind are required")
        canonical_time(self.known_from)
        for value in (self.valid_from, self.valid_to):
            if value:
                canonical_time(value)
        if self.valid_from and self.valid_to and canonical_time(self.valid_from) >= canonical_time(self.valid_to):
            raise ValueError("fragment valid_to must follow valid_from")
        if self.status not in {"ACTIVE", "DISPUTED", "SUPERSEDED", "RETRACTED", "ARCHIVED"}:
            raise ValueError("fragment status is invalid")
        if not self.source_spans:
            raise ValueError("fragments require canonical source spans")
        for span in self.source_spans:
            if (not span.get("ref") or type(span.get("start")) is not int or type(span.get("end")) is not int
                    or span["start"] < 0 or span["end"] <= span["start"]):
                raise ValueError("fragment source span is invalid")


class PostgresContentProjection:
    def __init__(self, connect: Callable, scope: ProjectionScope, *, dimensions: int,
                 embedding_model_id: str, lexical: Callable[[str], str], lexical_version: str,
                 normalize: Callable[[dict], dict | None], statement_timeout_ms: int = 5000, generation: str = "v1"):
        if (type(dimensions) is not int or not 1 <= dimensions <= 16000 or not embedding_model_id
                or not lexical_version or statement_timeout_ms <= 0 or not generation.strip()):
            raise ValueError("projection dimensions, model, lexical version and query budget must be explicit")
        self.connect, self.scope = connect, scope
        self.dimensions, self.embedding_model_id = dimensions, embedding_model_id
        self.lexical, self.lexical_version, self.normalize = lexical, lexical_version, normalize
        self.statement_timeout_ms = statement_timeout_ms
        self.version = "postgres-content/1;" + embedding_model_id + ";" + lexical_version
        self.key = digest([scope.group_id, self.version, dimensions, generation])

    def initialize(self):
        """Run explicitly during setup, never from a search or supervisor import."""
        with self.connect() as db:
            db.execute("CREATE EXTENSION IF NOT EXISTS vector")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_content_state (
                projection_key TEXT PRIMARY KEY, scope_id TEXT NOT NULL, config_json JSONB NOT NULL,
                indexed_through BIGINT NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_content_fragments (
                projection_key TEXT NOT NULL, scope_id TEXT NOT NULL, fragment_id TEXT NOT NULL,
                canonical_id TEXT NOT NULL, revision_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                body TEXT NOT NULL, kind TEXT NOT NULL, spans_json JSONB NOT NULL,
                adoption_seq BIGINT NOT NULL,
                lexical TSVECTOR NOT NULL,embedding VECTOR NOT NULL,
                PRIMARY KEY(projection_key,fragment_id,adoption_seq))""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_content_adoptions (
                projection_key TEXT NOT NULL, scope_id TEXT NOT NULL, adoption_seq BIGINT NOT NULL,
                canonical_id TEXT NOT NULL, revision_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                known_from TIMESTAMPTZ NOT NULL,valid_from TIMESTAMPTZ,valid_to TIMESTAMPTZ,
                status TEXT NOT NULL,PRIMARY KEY(projection_key,adoption_seq))""")
            db.execute("CREATE INDEX IF NOT EXISTS memory_content_scope ON memory_content_adoptions(projection_key,scope_id,canonical_id,known_from,adoption_seq)")
            db.execute("CREATE INDEX IF NOT EXISTS memory_content_lexical ON memory_content_fragments USING GIN(lexical)")
            from psycopg.types.json import Jsonb
            config = {"dimensions": self.dimensions, "embedding_model_id": self.embedding_model_id,
                      "lexical_version": self.lexical_version, "scope": asdict(self.scope)}
            db.execute("INSERT INTO memory_content_state(projection_key,scope_id,config_json) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (self.key, self.scope.group_id, Jsonb(config)))

    def write_adoption(self, fragments: list[ContentFragment], vectors: list[list[float]], *, adoption_seq: int):
        """One complete adopted claim version; acknowledgement follows its commit.

        The worker saves generated chunks/vectors as derivation artifacts before
        calling here. A retry writes the same immutable fragment identities.
        """
        if not fragments or len(fragments) != len(vectors) or type(adoption_seq) is not int or adoption_seq <= 0:
            raise ValueError("one nonempty adoption and matching vectors are required")
        first = fragments[0]
        if any((f.canonical_id, f.revision_id, canonical_time(f.known_from), f.valid_from, f.valid_to, f.status) != (first.canonical_id, first.revision_id, canonical_time(first.known_from), first.valid_from, first.valid_to, first.status) for f in fragments):
            raise ValueError("a projection transaction must contain exactly one adopted claim version")
        if len({f.fragment_id for f in fragments}) != len(fragments):
            raise ValueError("duplicate fragment identities")
        validated = [validate_vector(v, self.dimensions) for v in vectors]
        lexical = [self.lexical(f.body) for f in fragments]
        from psycopg.types.json import Jsonb
        from pgvector.psycopg import register_vector
        import numpy as np
        with self.connect() as db:
            register_vector(db)
            row = db.execute("SELECT indexed_through FROM memory_content_state WHERE projection_key=%s AND scope_id=%s FOR UPDATE", (self.key, self.scope.group_id)).fetchone()
            if row is None:
                raise RuntimeError("initialize content projection before writing")
            cursor = row[0]
            input_hash = digest([[asdict(f), v] for f, v in zip(fragments, validated, strict=True)])
            if adoption_seq <= cursor:
                # A retry is valid only for byte-identical already committed input.
                saved = db.execute("SELECT input_hash FROM memory_content_adoptions WHERE projection_key=%s AND adoption_seq=%s", (self.key, adoption_seq)).fetchone()
                if saved is None or saved[0] != input_hash:
                    raise ValueError("content checkpoint replay changed its adopted input")
                return False
            newer = db.execute("SELECT 1 FROM memory_content_adoptions WHERE projection_key=%s AND canonical_id=%s AND known_from>%s LIMIT 1", (self.key, first.canonical_id, first.known_from)).fetchone()
            if newer:
                raise ValueError("adoption time cannot move backwards")
            db.execute("INSERT INTO memory_content_adoptions VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                       (self.key, self.scope.group_id, adoption_seq, first.canonical_id, first.revision_id,
                        input_hash, first.known_from, first.valid_from, first.valid_to, first.status))
            for f, v, lex in zip(fragments, validated, lexical, strict=True):
                db.execute("""INSERT INTO memory_content_fragments VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple'::regconfig,%s),%s)
                    ON CONFLICT DO NOTHING""", (self.key, self.scope.group_id, f.fragment_id, f.canonical_id,
                    f.revision_id, digest([asdict(f), v]), f.body, f.kind, Jsonb(f.source_spans), adoption_seq,
                    lex, np.array(v, dtype=np.float32)))
            db.execute("UPDATE memory_content_state SET indexed_through=%s WHERE projection_key=%s", (adoption_seq, self.key))
        return True

    def search(self, query: str, *, query_vector: list[float] | None = None,
               valid_at: str | None = None, known_at: str | None = None, limit=8, candidate_budget=32,
               canonical_ids: list[str] | None = None, kinds: list[str] | None = None):
        if not query.strip() or not 1 <= limit <= candidate_budget <= 1000:
            raise ValueError("query and disclosed candidate budget must be valid")
        known = canonical_time(known_at) if known_at else now()
        valid = canonical_time(valid_at) if valid_at else now()
        params = [self.key, self.scope.group_id, known, valid, valid, self.key, self.scope.group_id]
        # Match the canonical dual-time rule: choose the last known adoption
        # among revisions valid at the requested world time, THEN test status.
        # A newer revision with a different validity interval does not erase
        # knowledge about an earlier world interval.
        chosen = """WITH chosen AS (SELECT DISTINCT ON(canonical_id) canonical_id,adoption_seq,status
            FROM memory_content_adoptions WHERE projection_key=%s AND scope_id=%s AND known_from<=%s
            AND (valid_from IS NULL OR valid_from<=%s) AND (valid_to IS NULL OR valid_to>%s)
            ORDER BY canonical_id,known_from DESC,adoption_seq DESC)
            SELECT f.fragment_id,f.canonical_id,f.revision_id,f.body,f.kind,f.spans_json
            FROM memory_content_fragments f JOIN chosen c ON c.canonical_id=f.canonical_id AND c.adoption_seq=f.adoption_seq
            WHERE f.projection_key=%s AND f.scope_id=%s AND c.status='ACTIVE'"""
        if canonical_ids is not None:
            if not canonical_ids:
                return []
            chosen += " AND f.canonical_id=ANY(%s)"
            params.append(canonical_ids)
        if kinds is not None:
            if not kinds:
                return []
            chosen += " AND f.kind=ANY(%s)"
            params.append(kinds)
        lex = self.lexical(query)
        from pgvector.psycopg import register_vector
        import numpy as np
        with self.connect() as db:
            register_vector(db)
            db.execute("SELECT set_config('statement_timeout',%s,true)", (str(self.statement_timeout_ms),))
            checkpoint = db.execute("SELECT indexed_through FROM memory_content_state WHERE projection_key=%s", (self.key,)).fetchone()
            if checkpoint is None:
                raise RuntimeError("content projection has not been initialized")
            lexical_rows = db.execute(chosen + " AND f.lexical @@ plainto_tsquery('simple'::regconfig,%s) ORDER BY ts_rank_cd(f.lexical,plainto_tsquery('simple'::regconfig,%s)) DESC,f.fragment_id LIMIT %s", (*params, lex, lex, candidate_budget)).fetchall()
            rankings = [lexical_rows]
            if query_vector is not None:
                v = validate_vector(query_vector, self.dimensions)
                dense_rows = db.execute(chosen + " ORDER BY f.embedding <=> %s,f.fragment_id LIMIT %s", (*params, np.array(v, dtype=np.float32), candidate_budget)).fetchall()
                rankings.append(dense_rows)
        scores, rows = {}, {}
        for ranking in rankings:
            for rank, row in enumerate(ranking, 1):
                scores[row[0]] = scores.get(row[0], 0) + 1 / (60 + rank)
                rows[row[0]] = row
        result = []
        for fragment_id in sorted(scores, key=lambda k: (-scores[k], k)):
            row = rows[fragment_id]
            candidate = {"node_id": fragment_id, "canonical_id": row[1], "revision_id": row[2],
                         "excerpt": row[3][:800], "kind": row[4], "source_spans": row[5],
                         "scope": asdict(self.scope), "status": "ACTIVE", "view": "CONTENT_FRAGMENT",
                         "provider_version": self.version, "indexed_through": checkpoint[0],
                         "valid_at": valid, "known_at": known, "ranking_reason": "reciprocal rank fusion of scoped PostgreSQL FTS and pgvector"}
            normalized = self.normalize(candidate)
            if normalized is not None:
                result.append(normalized)
            if len(result) == limit:
                break
        return result

    def progress(self):
        with self.connect() as db:
            row = db.execute("SELECT indexed_through FROM memory_content_state WHERE projection_key=%s AND scope_id=%s",
                             (self.key, self.scope.group_id)).fetchone()
        if row is None:
            raise RuntimeError("content projection has not been initialized")
        return {"indexed_through": row[0], "provider_version": self.version}
