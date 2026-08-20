from __future__ import annotations

from .internal.memory import MemorySensor
from .internal.process import ProcessSensor
from .internal.resource import ResourceSensor
from .internal.security import SecuritySensor
from .external.provider import ProviderSensor
from .registry import SensorRegistry


def default_registry() -> SensorRegistry:
    registry = SensorRegistry()
    for sensor in (
        ResourceSensor(),
        ProcessSensor(),
        ProviderSensor(),
        MemorySensor(),
        SecuritySensor(),
    ):
        registry.register(sensor)
    return registry
