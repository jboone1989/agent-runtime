"""Viability → action tendencies shared by Default and VirtualBrain engines."""
from __future__ import annotations

from ..self_model.model import SelfModel
from ..viability.state import ViabilityAssessment
from .engine import ActionTendency, SelfModelUpdate


def _numeric(model: SelfModel, name: str) -> float | None:
    belief = model.internal_state.get(name)
    if belief is None or not isinstance(belief.value, (int, float)):
        return None
    return float(belief.value)


def tendencies_from_viability(
    viability: ViabilityAssessment,
    self_model: SelfModel,
) -> tuple[list[ActionTendency], list[SelfModelUpdate], dict[str, float], dict[str, float]]:
    drives: dict[str, float] = {}
    tendencies: list[ActionTendency] = []
    attention: dict[str, float] = {}
    updates: list[SelfModelUpdate] = []
    for t in viability.threats:
        drives[f"restore_{t.dimension}"] = t.severity
        attention[t.dimension] = t.severity
        tendencies.append(
            ActionTendency("maintain", t.severity, t.description, t.dimension)
        )
        tendencies.append(
            ActionTendency("diagnose", 0.6 * t.severity, f"diagnose {t.dimension}", t.dimension)
        )
        if t.severity >= 0.9 and t.dimension in {"compute", "operational_integrity"}:
            tendencies.append(
                ActionTendency("spawn_replica", t.severity, "survival failover candidate", t.dimension)
            )
        updates.append(
            SelfModelUpdate(
                path=f"vulnerabilities.{t.dimension}",
                new_value=t.severity,
                reason=t.description,
                uncertainty=viability.uncertainty,
            )
        )
    error_rate = _numeric(self_model, "llm_error_rate")
    llm_cap = self_model.capabilities.get("llm_provider")
    provider_degraded = (error_rate is not None and error_rate >= 0.4) or (
        llm_cap is not None and not llm_cap.available
    )
    if provider_degraded:
        strength = error_rate if error_rate is not None else 0.8
        tendencies.append(
            ActionTendency(
                action="switch_provider",
                strength=max(strength, 0.7),
                reason="llm provider error rate high; switch to backup",
                dimension="capability",
            )
        )
        updates.append(
            SelfModelUpdate(
                path="capabilities.llm_provider",
                new_value=False,
                reason="primary llm provider degraded",
                uncertainty=0.2,
            )
        )
        drives["restore_llm_provider"] = strength
        attention["llm_provider"] = strength
    return tendencies, updates, drives, attention
