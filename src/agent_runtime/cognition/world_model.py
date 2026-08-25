"""WorldState is everything that is not this agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..observations import Event, Observation


@dataclass
class WorldState:
    timestamp: datetime
    environment: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, Any] = field(default_factory=dict)
    other_agents: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


class WorldModel:
    def __init__(self) -> None:
        self.state = WorldState(timestamp=datetime.now(timezone.utc))

    def update(self, observation: Observation) -> list[str]:
        changed: list[str] = []
        self.state.timestamp = observation.timestamp
        for key, value in observation.world_payload().items():
            previous = self.state.environment.get(key, _MISSING)
            self.state.environment[key] = value
            if previous is _MISSING or previous != value:
                changed.append(key)
        if observation.event is not None:
            self.state.events.append(observation.event)
        others = observation.payload.get("other_agents")
        if isinstance(others, dict):
            self.state.other_agents.update(others)
        return changed


_MISSING = object()
