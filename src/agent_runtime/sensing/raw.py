from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..clock import new_id


@dataclass
class RawObservation:
    observation_id: str
    sensor_id: str
    modality: str
    timestamp: datetime
    payload: Any
    quality: float
    uncertainty: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        sensor_id: str,
        modality: str,
        timestamp: datetime,
        payload: Any,
        quality: float = 0.9,
        uncertainty: float = 0.1,
        metadata: dict[str, Any] | None = None,
    ) -> RawObservation:
        return cls(
            observation_id=new_id("raw"),
            sensor_id=sensor_id,
            modality=modality,
            timestamp=timestamp,
            payload=payload,
            quality=max(0.0, min(1.0, quality)),
            uncertainty=max(0.0, min(1.0, uncertainty)),
            metadata=metadata or {},
        )
