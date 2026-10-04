"""Reusable semantic consolidation over durable memory retrieval views.

Runtime owns the deterministic hierarchy, cache keys, child-version validation,
and summary lifecycle. Embeddings and summary text generation are injected.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
import math
from typing import Any, Protocol, runtime_checkable

from .retrieval import DurableMemoryRetrieval, fingerprint


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(
        self,
        texts: list[str],
        *,
        query: bool = False,
    ) -> Sequence[Sequence[float]]: ...


SummaryGenerator = Callable[
    [Mapping[str, Any]],
    Awaitable[Mapping[str, str]],
]


class SemanticMemoryConsolidator:
    """Build a bounded semantic summary hierarchy for one topic."""

    def __init__(self, retrieval: DurableMemoryRetrieval) -> None:
        self.retrieval = retrieval
        self.store = retrieval.store

    def leaves(self, topic: str) -> list[dict[str, Any]]:
        self.retrieval.sync()
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT node_id
                FROM memory_nodes
                WHERE status='ACTIVE'
                AND node_type IN ('memory','source')
                AND EXISTS (
                    SELECT 1
                    FROM json_each(metadata,'$.topics')
                    WHERE value=?
                )
                ORDER BY node_id""",
                (str(topic),),
            ).fetchall()
        return [
            self.retrieval.node(row[0])
            for row in rows
        ]

    @staticmethod
    def _distance(
        left: Sequence[float],
        right: Sequence[float],
    ) -> float:
        return sum(
            (x - y) ** 2
            for x, y in zip(left, right, strict=True)
        )

    def _ensure_vectors(
        self,
        nodes: Sequence[dict[str, Any]],
        provider: EmbeddingProvider,
    ) -> list[list[float]]:
        model_id = str(provider.model_id)
        if not model_id:
            raise ValueError(
                "embedding provider model_id must be non-empty"
            )

        pending = [
            node
            for node in nodes
            if not node.get("vector")
            or node.get("model_id") != model_id
        ]
        if pending:
            texts = [
                node["prefix"] + " " + node["body"]
                for node in pending
            ]
            encoded = list(provider.embed(texts))
            if len(encoded) != len(pending):
                raise ValueError(
                    "embedding provider returned the wrong vector count"
                )
            with self.store._connect() as db:
                for node, vector in zip(
                    pending,
                    encoded,
                    strict=True,
                ):
                    vector_list = [
                        float(value)
                        for value in vector
                    ]
                    node["vector"] = json.dumps(
                        vector_list
                    )
                    node["model_id"] = model_id
                    revision = fingerprint(
                        [
                            model_id,
                            node["prefix"],
                            node["body"],
                        ]
                    )
                    node["vector_revision"] = revision
                    db.execute(
                        """UPDATE memory_nodes
                        SET vector=?,model_id=?,vector_revision=?
                        WHERE node_id=? AND version=?""",
                        (
                            node["vector"],
                            model_id,
                            revision,
                            node["node_id"],
                            node["version"],
                        ),
                    )

        vectors: list[list[float]] = []
        dimensions: int | None = None
        for node in nodes:
            raw = node.get("vector")
            if not raw:
                raise ValueError(
                    f"missing vector for {node['node_id']}"
                )
            vector = [
                float(value)
                for value in json.loads(raw)
            ]
            if dimensions is None:
                dimensions = len(vector)
                if dimensions == 0:
                    raise ValueError(
                        "embedding vectors must be non-empty"
                    )
            elif len(vector) != dimensions:
                raise ValueError(
                    "embedding vectors must share one dimension"
                )
            vectors.append(vector)
        return vectors

    def _cluster(
        self,
        nodes: Sequence[dict[str, Any]],
        provider: EmbeddingProvider,
    ) -> list[list[dict[str, Any]]]:
        vectors = self._ensure_vectors(
            nodes,
            provider,
        )
        count = min(
            6,
            max(
                2,
                math.ceil(len(nodes) / 6),
            ),
        )
        centroids = [vectors[0]]
        for _ in range(count - 1):
            centroids.append(
                max(
                    vectors,
                    key=lambda vector: min(
                        self._distance(
                            vector,
                            centroid,
                        )
                        for centroid in centroids
                    ),
                )
            )

        assigned: list[int] = []
        for _ in range(6):
            buckets: list[list[int]] = [
                []
                for _ in range(count)
            ]
            assigned = []
            for index, vector in enumerate(vectors):
                best = min(
                    range(count),
                    key=lambda cluster: (
                        round(
                            self._distance(
                                vector,
                                centroids[cluster],
                            ),
                            8,
                        ),
                        len(buckets[cluster]),
                        cluster,
                    ),
                )
                buckets[best].append(index)
                assigned.append(best)

            for cluster, indices in enumerate(
                buckets
            ):
                if not indices:
                    continue
                centroids[cluster] = [
                    sum(
                        vectors[index][dimension]
                        for index in indices
                    )
                    / len(indices)
                    for dimension in range(
                        len(vectors[0])
                    )
                ]

        groups = [
            [
                nodes[index]
                for index, cluster_id in enumerate(
                    assigned
                )
                if cluster_id == cluster
            ]
            for cluster in range(count)
        ]
        groups = [
            list(group)
            for group in groups
            if group
        ]
        if len(groups) == 1:
            groups = [
                list(nodes[index : index + 6])
                for index in range(
                    0,
                    len(nodes),
                    6,
                )
            ]
        return groups

    async def _summarize(
        self,
        nodes: Sequence[dict[str, Any]],
        *,
        topic: str,
        generator: SummaryGenerator,
    ) -> dict[str, Any]:
        children = [
            node["node_id"]
            for node in nodes
        ]
        versions = [
            node["version"]
            for node in nodes
        ]
        node_id = (
            "summary:"
            + fingerprint(
                [topic, children]
            )[:28]
        )

        with self.store._connect() as db:
            old = db.execute(
                "SELECT metadata,status "
                "FROM memory_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
        if old is not None:
            metadata = json.loads(
                old["metadata"]
            )
            if (
                old["status"] == "ACTIVE"
                and metadata.get(
                    "child_versions"
                )
                == versions
            ):
                return self.retrieval.node(
                    node_id
                )

        payload = {
            "topic": topic,
            "children": [
                {
                    "ref": f"C{index}",
                    "text": node["body"][:1000],
                    "context": node["prefix"][:240],
                }
                for index, node in enumerate(nodes)
            ],
        }
        result = await generator(payload)
        if not isinstance(result, Mapping):
            raise ValueError(
                "summary generator must return a mapping"
            )
        summary = str(
            result.get("summary") or ""
        ).strip()
        context_prefix = str(
            result.get("context_prefix") or ""
        ).strip()
        if not 12 <= len(summary) <= 900:
            raise ValueError(
                "summary must be 12..900 characters"
            )
        if not 8 <= len(context_prefix) <= 240:
            raise ValueError(
                "context_prefix must be 8..240 characters"
            )

        self.retrieval.put_node(
            node_id,
            "summary",
            summary,
            {
                "topics": [topic],
                "child_versions": versions,
            },
            children=children,
            prefix=context_prefix,
        )
        return self.retrieval.node(node_id)

    async def consolidate(
        self,
        topic: str,
        *,
        embedding_provider: EmbeddingProvider,
        generator: SummaryGenerator,
    ) -> str | None:
        leaves = self.leaves(topic)
        if not leaves:
            return None

        async def group(
            nodes: Sequence[dict[str, Any]],
        ) -> dict[str, Any]:
            if len(nodes) <= 6:
                return await self._summarize(
                    nodes,
                    topic=topic,
                    generator=generator,
                )

            groups = self._cluster(
                nodes,
                embedding_provider,
            )
            children = [
                await group(cluster)
                for cluster in groups
            ]
            if len(children) > 6:
                return await group(children)
            return await self._summarize(
                children,
                topic=topic,
                generator=generator,
            )

        root = await group(leaves)

        with self.store._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO "
                "memory_service_meta VALUES(?,?)",
                (
                    "summary_root:" + topic,
                    root["node_id"],
                ),
            )
            db.execute(
                """WITH RECURSIVE reachable(id) AS (
                    SELECT ?
                    UNION
                    SELECT child_id
                    FROM memory_dependencies d
                    JOIN reachable r
                      ON d.parent_id=r.id
                )
                UPDATE memory_nodes
                SET status='SUPERSEDED'
                WHERE node_type='summary'
                AND EXISTS (
                    SELECT 1
                    FROM json_each(metadata,'$.topics')
                    WHERE value=?
                )
                AND node_id NOT IN (
                    SELECT id FROM reachable
                )""",
                (
                    root["node_id"],
                    topic,
                ),
            )
        return str(root["node_id"])


__all__ = [
    "EmbeddingProvider",
    "SemanticMemoryConsolidator",
    "SummaryGenerator",
]
