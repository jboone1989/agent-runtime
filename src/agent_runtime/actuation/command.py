from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clock import new_id


@dataclass
class ActuationCommand:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    resource: str = "self"
    command_id: str = field(default_factory=lambda: new_id("cmd"))
