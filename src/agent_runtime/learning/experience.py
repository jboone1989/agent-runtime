"""Learning schemas. v0.1 records experience; no fine-tune."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Experience:
    experience_id: str
    timestamp: datetime
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hypothesis:
    hypothesis_id: str
    statement: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class LessonCandidate:
    lesson_id: str
    claim: str
    support: list[str] = field(default_factory=list)


@dataclass
class StrategyCandidate:
    strategy_id: str
    name: str
    body: str


@dataclass
class StrategyEvaluation:
    strategy_id: str
    score: float
    notes: str = ""


@dataclass
class StrategyVersion:
    strategy_id: str
    version: str
    body: str
