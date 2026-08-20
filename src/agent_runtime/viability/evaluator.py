"""Observe self+world into viability dimensions and evaluate threats."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from ..cognition.world_model import WorldState
from ..config.loader import ViabilityConfig
from ..self_model.model import SelfState
from .policy import in_critical, in_preferred
from .predictor import LinearPredictor
from .state import (
    Opportunity,
    Threat,
    ViabilityAssessment,
    ViabilityDimension,
    ViabilityPrediction,
    ViabilityState,
)


class ViabilityModel(Protocol):
    def observe(self, self_state: SelfState, world_state: WorldState) -> ViabilityState: ...

    def predict(self, state: ViabilityState, horizon: timedelta) -> ViabilityPrediction: ...

    def evaluate(self, prediction: ViabilityPrediction) -> ViabilityAssessment: ...


def _severity(value: float, preferred_min: float, critical_min: float) -> float:
    if value >= preferred_min:
        return 0.0
    span = max(1e-6, preferred_min - critical_min)
    return min(1.0, (preferred_min - value) / span)


class DefaultViabilityModel:
    def __init__(self, cfg: ViabilityConfig) -> None:
        self.cfg = cfg
        self._predictor = LinearPredictor()
        self._previous: ViabilityState | None = None

    def observe(self, self_state: SelfState, world_state: WorldState) -> ViabilityState:
        now = world_state.timestamp
        dims: dict[str, ViabilityDimension] = {}
        for name, bounds in self.cfg.dimensions.items():
            value = float(self_state.metrics.get(name, self.cfg.initial_value))
            trend = 0.0
            if self._previous and name in self._previous.dimensions:
                trend = value - self._previous.dimensions[name].value
            dims[name] = ViabilityDimension(
                dimension=name,
                value=value,
                preferred_min=bounds.preferred_min,
                preferred_max=bounds.preferred_max,
                critical_min=bounds.critical_min,
                critical_max=bounds.critical_max,
                trend=trend,
                uncertainty=0.2 if name in self_state.metrics else 0.6,
                predicted_1h=value + trend,
            )
        # Weak world coupling: API latency can threaten network_connectivity.
        latency = world_state.environment.get("api_latency_ms")
        if isinstance(latency, (int, float)) and "network_connectivity" in dims:
            dim = dims["network_connectivity"]
            penalty = min(0.5, max(0.0, (float(latency) - 200.0) / 1000.0))
            dim.value = max(0.0, dim.value - penalty)
        state = ViabilityState(dimensions=dims, observed_at=now)
        self._previous = state
        return state

    def predict(self, state: ViabilityState, horizon: timedelta) -> ViabilityPrediction:
        return self._predictor.predict(state, horizon)

    def evaluate(self, prediction: ViabilityPrediction) -> ViabilityAssessment:
        threats: list[Threat] = []
        opportunities: list[Opportunity] = []
        urgencies: list[float] = []
        health: list[float] = []
        uncertainties: list[float] = []
        current = (
            self._previous.dimensions
            if self._previous is not None
            else prediction.predicted.dimensions
        )
        for name, predicted in prediction.predicted.dimensions.items():
            now_dim = current.get(name, predicted)
            health.append(_dimension_health(now_dim))
            uncertainties.append(now_dim.uncertainty)
            sev = max(
                _severity(now_dim.value, now_dim.preferred_min, now_dim.critical_min),
                _severity(predicted.value, predicted.preferred_min, predicted.critical_min),
            )
            if sev >= self.cfg.threat_threshold or in_critical(now_dim):
                threats.append(
                    Threat(
                        dimension=name,
                        severity=max(sev, 0.9 if in_critical(now_dim) else sev),
                        description=f"{name} leaving preferred band (value={now_dim.value:.2f})",
                        predicted_breach=not in_preferred(predicted),
                    )
                )
                urgencies.append(sev)
            elif now_dim.value > now_dim.preferred_min + 0.2:
                opportunities.append(
                    Opportunity(
                        dimension=name,
                        value=min(1.0, now_dim.value - now_dim.preferred_min),
                        description=f"{name} has slack",
                    )
                )
        score = sum(health) / max(1, len(health))
        urgency = max(urgencies) if urgencies else 0.0
        uncertainty = sum(uncertainties) / max(1, len(uncertainties))
        return ViabilityAssessment(
            score=score,
            threats=sorted(threats, key=lambda t: t.severity, reverse=True),
            opportunities=opportunities,
            urgency=urgency,
            uncertainty=uncertainty,
            dimensions=dict(current),
        )


def _dimension_health(dim: ViabilityDimension) -> float:
    mid = 0.5 * (dim.preferred_min + dim.preferred_max)
    span = max(1e-6, dim.preferred_max - dim.preferred_min)
    return max(0.0, 1.0 - abs(dim.value - mid) / span)
