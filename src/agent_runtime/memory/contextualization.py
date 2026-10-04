"""Model-agnostic evidence contextualization for durable memory retrieval.

The runtime owns selection, validation, and persistence of contextual prefixes.
A provider callback owns the actual model call. This keeps memory state logic
reusable while allowing any agent/cognition provider to supply the text.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
from typing import Any

from .retrieval import DurableMemoryRetrieval, terms


ContextPrefixGenerator = Callable[
    [Mapping[str, Any]],
    Awaitable[Mapping[str, str]],
]


class EvidenceContextualizer:
    """Apply bounded contextual prefixes to evidence nodes."""

    def __init__(self, retrieval: DurableMemoryRetrieval) -> None:
        self.retrieval = retrieval
        self.store = retrieval.store

    def pending(
        self,
        node_ids: Sequence[str],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for node_id in node_ids:
            node = self.retrieval.node(str(node_id))
            if node["node_type"] != "evidence":
                continue
            if not self.retrieval.valid(node):
                continue
            if node["metadata"].get("contextualized"):
                continue
            nodes.append(node)
            if len(nodes) >= max(1, min(int(limit), 32)):
                break
        return nodes

    def request(
        self,
        node_ids: Sequence[str],
        *,
        limit: int = 8,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nodes = self.pending(node_ids, limit=limit)
        payload = {
            "chunks": [
                {
                    "ref": f"C{index}",
                    "text": node["body"],
                    "source_context": node["prefix"][:300],
                }
                for index, node in enumerate(nodes)
            ]
        }
        return nodes, payload

    def apply(
        self,
        nodes: Sequence[Mapping[str, Any]],
        prefixes: Mapping[str, str],
    ) -> int:
        expected = {
            f"C{index}"
            for index in range(len(nodes))
        }
        actual = set(prefixes)
        if actual != expected:
            raise ValueError(
                "contextualization must return exactly one prefix "
                f"for each supplied chunk; expected={sorted(expected)} "
                f"actual={sorted(actual)}"
            )

        normalized: dict[str, str] = {}
        for ref in sorted(expected):
            value = prefixes[ref]
            if not isinstance(value, str):
                raise ValueError(
                    f"contextual prefix {ref} must be a string"
                )
            prefix = value.strip()
            if not 8 <= len(prefix) <= 240:
                raise ValueError(
                    f"contextual prefix {ref} must be 8..240 characters"
                )
            normalized[ref] = prefix

        changed = 0
        with self.store._connect() as db:
            for index, node in enumerate(nodes):
                current = db.execute(
                    "SELECT version,prefix,metadata "
                    "FROM memory_nodes WHERE node_id=?",
                    (node["node_id"],),
                ).fetchone()
                if current is None:
                    raise KeyError(node["node_id"])
                if current["version"] != node["version"]:
                    raise ValueError(
                        "evidence changed during contextualization; "
                        "search again"
                    )
                metadata = json.loads(current["metadata"])
                prefix = normalized[f"C{index}"]
                if (
                    metadata.get("contextualized")
                    and current["prefix"] == prefix
                ):
                    continue
                metadata["contextualized"] = True
                db.execute(
                    """UPDATE memory_nodes
                    SET prefix=?,metadata=?,vector=NULL,
                        model_id='',vector_revision=''
                    WHERE node_id=? AND version=?""",
                    (
                        prefix,
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                        ),
                        node["node_id"],
                        node["version"],
                    ),
                )
                db.execute(
                    "DELETE FROM memory_nodes_fts WHERE node_id=?",
                    (node["node_id"],),
                )
                document = prefix + " " + node["body"]
                db.execute(
                    "INSERT INTO memory_nodes_fts VALUES(?,?)",
                    (
                        node["node_id"],
                        " ".join(terms(document))
                        + " "
                        + document,
                    ),
                )
                changed += 1
        return changed

    async def contextualize(
        self,
        node_ids: Sequence[str],
        generator: ContextPrefixGenerator,
        *,
        limit: int = 8,
    ) -> int:
        nodes, payload = self.request(
            node_ids,
            limit=limit,
        )
        if not nodes:
            return 0
        prefixes = await generator(payload)
        if not isinstance(prefixes, Mapping):
            raise ValueError(
                "context prefix generator must return a mapping"
            )
        return self.apply(nodes, prefixes)


__all__ = [
    "ContextPrefixGenerator",
    "EvidenceContextualizer",
]
