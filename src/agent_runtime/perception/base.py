from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..body.model import BodyModel
from ..sensing.raw import RawObservation
from .percept import Percept


@dataclass
class PerceptionContext:
    now: datetime
    cycle: int
    agent_id: str
    body: BodyModel | None = None


class PerceptionModel(Protocol):
    def perceive(
        self,
        observations: list[RawObservation],
        context: PerceptionContext,
    ) -> list[Percept]: ...
