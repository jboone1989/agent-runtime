"""Counterfactual predictions whose errors can update metacognitive evidence."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import OutcomeRecord
from .beliefs import SelfPrediction
from .model import SelfModel


def predict_action(
    model: SelfModel,
    proposal: ActionProposal,
    *,
    timestamp: datetime,
    horizon_hours: float = 0.0,
) -> SelfPrediction | None:
    target = _action_target(model, proposal)
    if target is None:
        return None
    path, baseline, predicted = target
    uncertainty = float(model.uncertainty.get(path, 0.5))
    prediction = SelfPrediction(
        horizon_hours=horizon_hours,
        description=f"if I execute {proposal.kind}, {path} should become {predicted!r}",
        predicted={path: predicted},
        confidence=max(0.0, min(1.0, 1.0 - uncertainty)),
        prediction_id=new_id("spr"),
        target_path=path,
        baseline=baseline,
        intervention={
            "action": proposal.kind,
            "action_id": proposal.action_id,
            **(
                {
                    "prediction_failure_guard_id": proposal.payload[
                        "prediction_failure_guard_id"
                    ]
                }
                if proposal.payload.get("prediction_failure_guard_id")
                else {}
            ),
        },
        created_at=timestamp,
        evidence_refs=[proposal.action_id, *proposal.evidence],
    )
    model.future_predictions.append(prediction)
    return prediction


def reconcile_action_prediction(
    prediction: SelfPrediction,
    outcome: OutcomeRecord | None,
) -> SelfPrediction:
    if outcome is None:
        prediction.status = "CANCELLED"
        return prediction
    prediction.evidence_refs.extend(
        ref for ref in [outcome.outcome_id] if ref not in prediction.evidence_refs
    )
    if not outcome.success:
        prediction.status = "ACTION_FAILED"
        return prediction
    observed = _observed_value(prediction, outcome)
    if observed is _MISSING:
        prediction.status = "UNRESOLVED"
        return prediction
    prediction.observed = observed
    expected = prediction.predicted.get(prediction.target_path)
    prediction.error = _prediction_error(expected, observed)
    prediction.status = "MATCHED" if prediction.error <= 0.05 else "VIOLATED"
    return prediction


def _action_target(
    model: SelfModel, proposal: ActionProposal
) -> tuple[str, Any, Any] | None:
    if proposal.kind == "maintain":
        dimension = str(proposal.payload.get("dimension") or "")
        belief = model.internal_state.get(dimension)
        if belief is None or not _number(belief.value):
            return None
        baseline = float(belief.value)
        return (
            f"internal_state.{dimension}",
            baseline,
            min(1.0, baseline + 0.05),
        )
    if proposal.kind == "diagnose":
        dimension = str(proposal.payload.get("dimension") or "")
        belief = model.internal_state.get(dimension)
        expected_range = proposal.payload.get("expected_range")
        if (
            not dimension
            or belief is None
            or not isinstance(expected_range, (list, tuple))
            or len(expected_range) != 2
            or not all(_number(value) for value in expected_range)
            or float(expected_range[0]) > float(expected_range[1])
        ):
            return None
        return (
            f"internal_state.{dimension}",
            belief.value,
            [float(expected_range[0]), float(expected_range[1])],
        )
    if proposal.kind == "disable_capability":
        name = str(proposal.payload.get("capability") or "llm_provider")
        belief = model.capabilities.get(name)
        return (
            f"capabilities.{name}",
            None if belief is None else belief.available,
            False,
        )
    return None


def _observed_value(prediction: SelfPrediction, outcome: OutcomeRecord) -> Any:
    if prediction.target_path.startswith("internal_state."):
        return outcome.effects.get("value", _MISSING)
    if prediction.target_path.startswith("capabilities."):
        return outcome.effects.get("available", _MISSING)
    return _MISSING


def _prediction_error(expected: Any, observed: Any) -> float:
    if (
        isinstance(expected, (list, tuple))
        and len(expected) == 2
        and all(_number(value) for value in expected)
    ):
        if not _number(observed):
            return 1.0
        low, high = float(expected[0]), float(expected[1])
        return 0.0 if low <= float(observed) <= high else 1.0
    if _number(expected) and _number(observed):
        return min(1.0, abs(float(expected) - float(observed)))
    return 0.0 if expected == observed else 1.0


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_MISSING = object()
