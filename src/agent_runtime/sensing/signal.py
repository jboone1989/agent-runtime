"""Unified sensory signal. Sensors report facts; they do not interpret."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from ..clock import new_id

Scope = Literal["SELF", "WORLD", "OTHER_AGENT", "UNKNOWN"]


@dataclass
class Signal:
    signal_id: str
    timestamp: datetime
    source: str
    domain: str
    kind: str
    value: Any
    severity: float
    confidence: float
    scope: Scope
    evidence_refs: list[str] = field(default_factory=list)
    ttl_seconds: float | None = None
    entity_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        domain: str,
        kind: str,
        value: Any,
        severity: float,
        confidence: float,
        scope: Scope,
        timestamp: datetime,
        evidence_refs: list[str] | None = None,
        ttl_seconds: float | None = None,
        entity_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Signal:
        return cls(
            signal_id=new_id("sig"),
            timestamp=timestamp,
            source=source,
            domain=domain,
            kind=kind,
            value=value,
            severity=max(0.0, min(1.0, severity)),
            confidence=max(0.0, min(1.0, confidence)),
            scope=scope,
            evidence_refs=evidence_refs or [],
            ttl_seconds=ttl_seconds,
            entity_id=entity_id,
            metadata=metadata or {},
        )
