from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..environment.base import EnvironmentAdapter


@dataclass
class SensorContext:
    now: datetime
    environment: EnvironmentAdapter
    cycle: int
    agent_id: str
