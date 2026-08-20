from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Episode:
    episode_id: str
    timestamp: datetime
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


class EpisodicMemory:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []

    def append(self, episode: Episode) -> None:
        self.episodes.append(episode)
