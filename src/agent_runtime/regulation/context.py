from __future__ import annotations

from dataclasses import dataclass

from ..sensing.world import SenseWorld
from ..survival.failover import ProviderPool
from .state import RegulatoryState


@dataclass
class RegulationContext:
    world: SenseWorld
    providers: ProviderPool
    regulatory_state: RegulatoryState
    cycle: int
