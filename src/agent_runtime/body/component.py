from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BodyComponent:
    component_id: str
    kind: str
    capabilities: set[str]
    health: float
    state: dict[str, Any] = field(default_factory=dict)
    uncertainty: float = 0.2
    dependencies: list[str] = field(default_factory=list)
    replaceable: bool = True
