"""Exact evidence archives committed before a canonical revision references them."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .projection_scope import ProjectionScope
from .revisions import digest, encode


class EvidenceArchive:
    def __init__(self, canonical, scope: ProjectionScope):
        self.canonical, self.scope = canonical, scope
        self.root = Path(canonical.root) / "evidence-v2" / scope.group_id

    def propose(self, *, claim_id: str, revision_id: str, body: str, record: dict):
        """Archive exact text, then propose; the caller separately decides adoption.

        A failed proposal may leave an unreferenced archive. A committed revision
        always refers to a complete file. This does not claim a cross-store transaction.
        """
        if not claim_id.strip() or not revision_id.strip() or not body.strip():
            raise ValueError("evidence identity, revision and exact body are required")
        if any(k in record and record[k] != v for k, v in asdict(self.scope).items()):
            raise ValueError("evidence scope differs from the archive binding")
        envelope = {"schema": "agent-evidence/1", "canonical_id": claim_id,
                    "scope": asdict(self.scope), "body": body,
                    "metadata": dict(record.get("source_metadata") or {})}
        archive_id = digest(envelope)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / (archive_id + ".json")
        data = encode(envelope).encode("utf-8")
        # Publish a fully flushed file without overwriting another writer's file.
        fd, temporary = tempfile.mkstemp(prefix="archive-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != data:
                    raise RuntimeError("immutable evidence archive changed")
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        revision = {**record, **asdict(self.scope), "claim_id": claim_id,
                    "revision_id": revision_id, "kind": "EVIDENCE_SOURCE",
                    "text": record.get("text") or record.get("title") or claim_id,
                    "payload": {**dict(record.get("payload") or {}),
                                "evidence_archive": {"archive_id": archive_id,
                                                     "body_hash": digest(body)}}}
        return self.canonical.propose_revision(revision)

    def content(self, record: dict) -> str:
        if not self.scope.matches(record):
            raise KeyError(record.get("claim_id"))
        reference = record.get("payload", {}).get("evidence_archive")
        if reference is None:
            return record["text"]
        archive_id = reference.get("archive_id", "")
        if len(archive_id) != 64 or any(c not in "0123456789abcdef" for c in archive_id):
            raise ValueError("invalid evidence archive identity")
        envelope = json.loads((self.root / (archive_id + ".json")).read_text(encoding="utf-8"))
        if (digest(envelope) != archive_id or envelope["scope"] != asdict(self.scope)
                or envelope["canonical_id"] != record["claim_id"]
                or digest(envelope["body"]) != reference["body_hash"]):
            raise RuntimeError("canonical evidence archive failed integrity validation")
        return envelope["body"]
