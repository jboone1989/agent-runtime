from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..survival.failover import ProviderPool
from .world import SenseWorld


@dataclass
class SensorContext:
    now: datetime
    world: SenseWorld
    providers: ProviderPool
    cycle: int
    agent_id: str
