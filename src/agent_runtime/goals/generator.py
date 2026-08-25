"""Emit Internal/Viability goals from viability threats — no user prompt required."""
from __future__ import annotations

from datetime import datetime

from ..viability.state import ViabilityAssessment
from ..self_model.beliefs import BeliefRevision, BeliefRevisionStatus, SelfMonitoringEvent
from ..cognition.attribution import CausalAttribution
from ..self_model.beliefs import SelfPrediction
from .goal import Goal, GoalOrigin, GoalStatus


class GoalGenerator:
    def __init__(self, threat_threshold: float = 0.25) -> None:
        self.threat_threshold = threat_threshold

    def generate(
        self,
        viability: ViabilityAssessment,
        existing: list[Goal],
        now: datetime,
        evidence: list[str],
    ) -> list[Goal]:
        created: list[Goal] = []
        active = [g for g in existing if g.status == GoalStatus.ACTIVE]
        for threat in viability.threats:
            if threat.severity < self.threat_threshold:
                continue
            description = f"Restore {threat.dimension} into preferred range"
            if any(g.description == description and g.origin == GoalOrigin.VIABILITY for g in active):
                continue
            created.append(
                Goal.create(
                    description=description,
                    origin=GoalOrigin.VIABILITY,
                    created_at=now,
                    reason=threat.description,
                    priority=min(1.0, 0.5 + threat.severity),
                    evidence_refs=list(evidence),
                    dimension=threat.dimension,
                    success_criteria=f"{threat.dimension} back in preferred band",
                )
            )
        return created

    def generate_self_awareness(
        self,
        *,
        revisions: list[BeliefRevision],
        monitoring_events: list[SelfMonitoringEvent],
        existing: list[Goal],
        now: datetime,
    ) -> list[Goal]:
        """Open evidence-bound internal goals for unprompted self discrepancies."""
        active_descriptions = {
            goal.description for goal in existing if goal.status == GoalStatus.ACTIVE
        }
        created: list[Goal] = []
        for revision in revisions:
            if revision.status != BeliefRevisionStatus.OPEN:
                continue
            description = f"Resolve conflicting self-belief {revision.path}"
            if description in active_descriptions:
                continue
            active_descriptions.add(description)
            created.append(
                Goal.create(
                    description=description,
                    origin=GoalOrigin.INTERNAL,
                    created_at=now,
                    reason=revision.reason,
                    priority=0.85,
                    evidence_refs=[revision.revision_id, *revision.evidence_refs],
                    dimension=revision.path.removeprefix("internal_state."),
                    success_criteria="obtain corroborating evidence and reconcile belief",
                )
            )
        if created:
            return created
        for event in monitoring_events:
            if not event.spontaneous or event.materiality < 0.10:
                continue
            description = f"Explain material self-change {event.path}"
            if description in active_descriptions:
                continue
            active_descriptions.add(description)
            created.append(
                Goal.create(
                    description=description,
                    origin=GoalOrigin.INTERNAL,
                    created_at=now,
                    reason="unprompted self-monitor detected a material change",
                    priority=min(0.8, 0.5 + event.materiality),
                    evidence_refs=[event.event_id, *event.evidence_refs],
                    dimension=event.path.removeprefix("internal_state."),
                    success_criteria="diagnose the observed internal change",
                )
            )
        return created

    def generate_cognitive_gaps(
        self,
        *,
        attribution: CausalAttribution,
        predictions: list[SelfPrediction],
        calibration: float | None,
        resolved_assessments: int,
        assessment_evidence: list[str],
        existing: list[Goal],
        now: datetime,
    ) -> list[Goal]:
        """Turn unresolved self-knowledge gaps into persistent internal goals."""
        active = {
            goal.description for goal in existing if goal.status == GoalStatus.ACTIVE
        }
        created: list[Goal] = []
        if (
            attribution.cause_type == "UNKNOWN"
            and attribution.alternatives
            and attribution.confidence < 0.4
        ):
            description = "Resolve current self/world causal ambiguity"
            if description not in active:
                created.append(
                    Goal.create(
                        description=description,
                        origin=GoalOrigin.INTERNAL,
                        created_at=now,
                        reason=attribution.notes or "multiple causes remain plausible",
                        priority=0.8,
                        evidence_refs=list(attribution.evidence_refs),
                        success_criteria="obtain intervention evidence that selects a cause",
                    )
                )
                active.add(description)
        for prediction in predictions:
            if prediction.status != "VIOLATED":
                continue
            description = f"Explain failed self-prediction {prediction.prediction_id}"
            if description in active:
                continue
            created.append(
                Goal.create(
                    description=description,
                    origin=GoalOrigin.INTERNAL,
                    created_at=now,
                    reason=f"prediction error={prediction.error}",
                    priority=0.85,
                    evidence_refs=[prediction.prediction_id, *prediction.evidence_refs],
                    dimension=prediction.target_path.removeprefix("internal_state."),
                    success_criteria="produce a later matched prediction for the same path",
                )
            )
            active.add(description)
        if calibration is not None and resolved_assessments >= 3 and calibration < 0.7:
            description = "Improve metacognitive calibration"
            if description not in active:
                created.append(
                    Goal.create(
                        description=description,
                        origin=GoalOrigin.INTERNAL,
                        created_at=now,
                        reason=f"calibration score {calibration:.3f} is below 0.700",
                        priority=0.8,
                        evidence_refs=list(assessment_evidence),
                        success_criteria="raise evidence-scored calibration to at least 0.80",
                    )
                )
        return created
