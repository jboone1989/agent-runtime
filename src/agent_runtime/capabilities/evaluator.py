"""Multi-dimensional assessment. Hard constraints first; never a single score.

GitHub stars are provenance claims, not ranking dimensions.
"""
from __future__ import annotations

from .model import (
    ASSESSMENT_DIMENSIONS,
    CapabilityAssessment,
    CapabilityCandidate,
    CapabilityKind,
    CapabilityRequirement,
    SideEffectLevel,
)
from .policy import CapabilityPolicy

_LOCAL_SOURCES = frozenset({"local", "inventory"})


class CapabilityEvaluator:
    def assess(
        self,
        requirement: CapabilityRequirement,
        candidate: CapabilityCandidate,
        policy: CapabilityPolicy,
    ) -> CapabilityAssessment:
        reasons: list[str] = []
        local = candidate.source in _LOCAL_SOURCES
        skip_license = local or candidate.kind == CapabilityKind.REMOTE_AGENT
        if not skip_license and not policy.license_allowed(candidate.license):
            reasons.append("license")
        if requirement.required_permissions and not requirement.required_permissions <= candidate.permissions:
            reasons.append("permission")
        if candidate.side_effects.value > requirement.max_side_effect.value:
            reasons.append("side_effect")
        required_lang = requirement.constraints.get("language")
        candidate_lang = candidate.provenance.get("language")
        if required_lang and candidate_lang and candidate_lang != required_lang:
            reasons.append("compatibility")
        if requirement.constraints.get("maintained") is True and candidate.provenance.get("maintained") is False:
            reasons.append("maintenance")
        if not local and policy.allowed_kinds and not policy.kind_allowed(candidate.kind):
            reasons.append("kind")

        task_fit = _task_fit(requirement, candidate)
        maintenance = _maintenance(candidate)
        dimensions = {name: 0.5 for name in ASSESSMENT_DIMENSIONS}
        dimensions.update(
            {
                "task_fit": task_fit,
                "compatibility": 0.0 if "compatibility" in reasons else 1.0,
                "security": 0.8 if "license" not in reasons else 0.1,
                "provenance": 0.7,
                "maintenance": maintenance,
                "health": 1.0,
                "cost": 0.5,
                "latency": 0.5,
                "reversibility": 1.0
                if candidate.side_effects.value <= SideEffectLevel.READ_ONLY.value
                else 0.2,
                "confidence": 0.6 if not reasons else 0.2,
            }
        )
        return CapabilityAssessment(
            candidate_id=candidate.candidate_id,
            capability_id=candidate.capability_id,
            dimensions=dimensions,
            hard_fail_reasons=reasons,
        )


def _task_fit(requirement: CapabilityRequirement, candidate: CapabilityCandidate) -> float:
    claimed = candidate.provenance.get("task_fit")
    score = float(claimed) if isinstance(claimed, (int, float)) else 0.5
    required_lang = requirement.constraints.get("language")
    candidate_lang = candidate.provenance.get("language")
    if required_lang and candidate_lang == required_lang:
        score = max(score, 0.8)
    if candidate.provenance.get("maintained") is False:
        score = min(score, 0.3)
    return max(0.0, min(1.0, score))


def _maintenance(candidate: CapabilityCandidate) -> float:
    flag = candidate.provenance.get("maintained")
    if flag is True:
        return 0.9
    if flag is False:
        return 0.1
    return 0.5
