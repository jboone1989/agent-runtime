from __future__ import annotations

from ..sensing.raw import RawObservation
from .base import PerceptionContext, PerceptionModel
from .percept import Percept


class PerceptionRegistry:
    def __init__(self) -> None:
        self._models: list[PerceptionModel] = []

    def register(self, model: PerceptionModel) -> None:
        self._models.append(model)

    def all(self) -> list[PerceptionModel]:
        return list(self._models)


class PerceptionPipeline:
    def __init__(self, models: list[PerceptionModel] | None = None) -> None:
        self.models = list(models or [])

    def perceive(
        self,
        observations: list[RawObservation],
        context: PerceptionContext,
    ) -> list[Percept]:
        out: list[Percept] = []
        for model in self.models:
            out.extend(model.perceive(observations, context))
        return out
