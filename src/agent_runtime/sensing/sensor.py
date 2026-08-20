from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..environment.base import EnvironmentReader


@dataclass
class SensorContext:
    now: datetime
    environment: EnvironmentReader
    cycle: int
    agent_id: str
