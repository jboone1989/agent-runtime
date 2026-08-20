from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..clock import new_id


@dataclass
class Percept:
    percept_id: str
    source_observation_ids: list[str]
    modality: str
    kind: str
    entity_id: str | None
    attributes: dict[str, Any]
    confidence: float
    salience: float
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(
        cls,
        *,
        observation_id: str,
        modality: str,
        kind: str,
        timestamp: datetime,
        attributes: dict[str, Any],
        entity_id: str | None = None,
        confidence: float = 0.9,
        salience: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> Percept:
        return cls(
            percept_id=new_id("pct"),
            source_observation_ids=[observation_id],
            modality=modality,
            kind=kind,
            entity_id=entity_id,
            attributes=attributes,
            confidence=max(0.0, min(1.0, confidence)),
            salience=max(0.0, min(1.0, salience)),
            timestamp=timestamp,
            metadata=metadata or {},
        )
