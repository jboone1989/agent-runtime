"""Generic environment snapshot. Core runtime does not interpret channels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnvironmentState:
    environment_id: str
    channels: dict[str, Any] = field(default_factory=dict)
