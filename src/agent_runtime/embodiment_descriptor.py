from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class EmbodimentDescriptor:
    embodiment_type: str = "digital"
    embodiment_version: str = "0.2.1"
    environment_type: str = "digital"
    sensor_profile: str = "digital_default"
    perception_profile: str = "digital_default"
    actuator_profile: str = "digital_default"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbodimentResolutionContext:
    """Bindings available at restore time. Resolvers read what they need and ignore the rest."""

    bindings: dict[str, Any] = field(default_factory=dict)


class UnknownEmbodimentError(ValueError):
    pass


class EmbodimentResolver(Protocol):
    def resolve(
        self,
        descriptor: EmbodimentDescriptor,
        context: EmbodimentResolutionContext,
    ): ...
