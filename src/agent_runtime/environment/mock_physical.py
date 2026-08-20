"""Simulated physical environment. Core runtime does not know this is physical."""
from __future__ import annotations

from typing import Any

from .adapter import assign, walk


class MockPhysicalEnvironment:
    environment_id = "mock_physical"

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "battery": 0.80,
            "temperature": 0.40,
            "distance_to_obstacle": 0.90,
            "conservation": False,
        }

    def describe_capabilities(self) -> set[str]:
        return {"battery", "temperature", "distance", "movement", "cooling"}

    def read(self, channel: str) -> Any:
        return walk(self.state, channel) if "/" in channel else self.state.get(channel)

    def apply(self, channel: str, value: Any) -> None:
        if "/" in channel:
            assign(self.state, channel, value)
        else:
            self.state[channel] = value
