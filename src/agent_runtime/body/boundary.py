from __future__ import annotations

from enum import Enum
from dataclasses import dataclass


class BoundaryRelation(Enum):
    CORE_SELF = "CORE_SELF"
    BODY = "BODY"
    OWNED = "OWNED"
    DEPENDENCY = "DEPENDENCY"
    ALLY = "ALLY"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class SelfBoundary:
    entity_id: str
    relation: BoundaryRelation
    confidence: float
