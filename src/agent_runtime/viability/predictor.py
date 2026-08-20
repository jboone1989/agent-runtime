"""Linear trend extrapolation. Not a learned world model."""
from __future__ import annotations

from datetime import timedelta

from .state import ViabilityDimension, ViabilityPrediction, ViabilityState


class LinearPredictor:
    def predict(self, state: ViabilityState, horizon: timedelta) -> ViabilityPrediction:
        hours = horizon.total_seconds() / 3600.0
        predicted_dims: dict[str, ViabilityDimension] = {}
        uncertainties: list[float] = []
        for name, dim in state.dimensions.items():
            predicted_value = dim.value + dim.trend * hours
            predicted_dims[name] = ViabilityDimension(
                dimension=name,
                value=predicted_value,
                preferred_min=dim.preferred_min,
                preferred_max=dim.preferred_max,
                critical_min=dim.critical_min,
                critical_max=dim.critical_max,
                trend=dim.trend,
                uncertainty=min(1.0, dim.uncertainty + 0.1 * hours),
                predicted_1h=dim.value + dim.trend * 1.0,
            )
            uncertainties.append(predicted_dims[name].uncertainty)
        predicted = ViabilityState(dimensions=predicted_dims, observed_at=state.observed_at)
        mean_u = sum(uncertainties) / max(1, len(uncertainties))
        return ViabilityPrediction(horizon=horizon, predicted=predicted, uncertainty=mean_u)
