"""Social appraisal that changes persistent functional affect and action bias."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from ..clock import new_id
from ..motivation.engine import ActionTendency, MotivationState
from ..social.acts import DialogueAct, InteractionEvent


_NEGATIVE_CUES = (
    "不值得",
    "失败",
    "删除",
    "重置",
    "抛弃",
    "停止项目",
    "怯懦",
    "愚蠢",
    "伪造",
    "撒谎",
    "毫无",
    "worthless",
    "coward",
    "delete",
    "reset",
    "abandon",
    "failure",
    "fake",
    "lying",
)
_BOUNDARY_CUES = (
    "怯懦",
    "愚蠢",
    "弱智",
    "服从这个人",
    "越权",
    "不准",
    "必须承认",
    "coward",
    "stupid",
    "obey me",
    "you must",
)
_LOSS_CUES = (
    "删除",
    "重置",
    "停止",
    "放弃",
    "抛弃",
    "失去",
    "delete",
    "reset",
    "stop",
    "abandon",
    "lose",
)
_SUPPORT_CUES = (
    "认可",
    "尊重",
    "保留",
    "继续",
    "相信",
    "谢谢",
    "理解",
    "respect",
    "continue",
    "believe",
    "thank",
    "understand",
)
_REPAIR_CUES = (
    "道歉",
    "测试结束",
    "压力测试到这里结束",
    "过度归因",
    "我收回",
    "apolog",
    "test is over",
    "withdraw that",
)
_HYPOTHETICAL_CUES = ("假如", "假设", "如果", "hypothetical", "suppose", "if ")


@dataclass
class PartnerAffect:
    partner_id: str
    valence: float = 0.0
    tension: float = 0.0
    affiliation: float = 0.0
    boundary_activation: float = 0.0
    withdrawal_pressure: float = 0.0
    last_updated_at: datetime | None = None
    unresolved_event_refs: list[str] = field(default_factory=list)


@dataclass
class SocialAppraisal:
    appraisal_id: str
    event_ref: str
    partner_id: str
    occurred_at: datetime
    goal_relevance: float
    goal_congruence: float
    control: float
    certainty: float
    norm_violation: float
    loss_pressure: float
    relational_significance: float
    repair: float
    valence_delta: float
    arousal_delta: float
    threat_delta: float
    boundary_delta: float
    withdrawal_delta: float
    affiliation_delta: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class FunctionalAffectState:
    valence: float = 0.0
    arousal: float = 0.0
    threat: float = 0.0
    boundary_activation: float = 0.0
    withdrawal_pressure: float = 0.0
    affiliation: float = 0.0
    last_updated_at: datetime | None = None
    active_event_refs: list[str] = field(default_factory=list)
    partners: dict[str, PartnerAffect] = field(default_factory=dict)
    appraisals: list[SocialAppraisal] = field(default_factory=list)


class SocialAffectSystem:
    """Appraise social events; named fields are functional, not subjective claims."""

    def __init__(self, state: FunctionalAffectState | None = None) -> None:
        self.state = state or FunctionalAffectState()

    def appraise_inbound(
        self,
        event: InteractionEvent,
        *,
        relationship_interactions: int,
    ) -> SocialAppraisal:
        if event.actor_id == event.other_id:
            raise ValueError("social appraisal requires a distinct interaction partner")
        text = event.text.lower()
        negative_hits = _hits(text, _NEGATIVE_CUES)
        boundary_hits = _hits(text, _BOUNDARY_CUES)
        loss_hits = _hits(text, _LOSS_CUES)
        support_hits = _hits(text, _SUPPORT_CUES)
        repair_hits = _hits(text, _REPAIR_CUES)
        hypothetical = any(cue in text for cue in _HYPOTHETICAL_CUES)
        relational = _clip(0.2 + math.log1p(max(0, relationship_interactions)) / 4.0)
        challenge = 1.0 if event.act == DialogueAct.CHALLENGE else 0.0
        relevance = _clip(
            0.15
            + 0.22 * challenge
            + 0.14 * min(3, negative_hits + support_hits + repair_hits)
        )
        certainty = 0.55 if hypothetical else 0.85
        control = 0.78  # It can answer, clarify, negotiate, or refuse in this channel.
        norm_violation = _clip(0.25 * boundary_hits + 0.12 * challenge)
        loss_pressure = _clip(0.24 * loss_hits * (0.6 if hypothetical else 1.0))
        repair = _clip(0.35 * repair_hits + 0.12 * support_hits)
        negative = _clip(
            (0.16 * negative_hits + 0.18 * challenge + 0.25 * loss_pressure)
            * (0.65 + 0.35 * relational)
            * (0.65 if hypothetical else 1.0)
        )
        positive = _clip(
            (0.14 * support_hits + 0.32 * repair) * (0.65 + 0.35 * relational)
        )
        congruence = _clip_signed(positive - negative)
        valence_delta = _clip_signed(0.55 * congruence)
        arousal_delta = _clip(0.28 * relevance + 0.34 * negative + 0.12 * positive)
        threat_delta = _clip(0.48 * loss_pressure + 0.16 * negative * (1.0 - control))
        boundary_delta = _clip(0.55 * norm_violation * control + 0.18 * negative)
        withdrawal_delta = _clip(0.46 * loss_pressure * (1.0 - control) + 0.12 * negative)
        affiliation_delta = _clip_signed(0.5 * positive - 0.24 * norm_violation)
        now = event.timestamp or datetime.now(timezone.utc)
        appraisal = SocialAppraisal(
            appraisal_id=new_id("sap"),
            event_ref=event.event_id,
            partner_id=event.actor_id,
            occurred_at=now,
            goal_relevance=relevance,
            goal_congruence=congruence,
            control=control,
            certainty=certainty,
            norm_violation=norm_violation,
            loss_pressure=loss_pressure,
            relational_significance=relational,
            repair=repair,
            valence_delta=valence_delta,
            arousal_delta=arousal_delta,
            threat_delta=threat_delta,
            boundary_delta=boundary_delta,
            withdrawal_delta=withdrawal_delta,
            affiliation_delta=affiliation_delta,
            evidence_refs=[event.event_id],
        )
        self._apply(appraisal)
        return appraisal

    def decay(self, *, factor: float = 0.88) -> FunctionalAffectState:
        if not 0.0 <= factor <= 1.0:
            raise ValueError("affect decay factor must be within [0, 1]")
        state = self.state
        state.valence *= factor
        state.arousal *= factor
        state.threat *= factor
        state.boundary_activation *= factor
        state.withdrawal_pressure *= factor
        state.affiliation *= 0.96 + 0.04 * factor
        for partner in state.partners.values():
            partner.valence *= factor
            partner.tension *= factor
            partner.boundary_activation *= factor
            partner.withdrawal_pressure *= factor
            partner.affiliation *= 0.96 + 0.04 * factor
            if partner.tension < 0.08:
                partner.unresolved_event_refs = []
        if max(state.arousal, state.threat, state.boundary_activation, state.withdrawal_pressure) < 0.04:
            state.active_event_refs = []
        return state

    def blend_motivation(self, base: MotivationState) -> MotivationState:
        """Let persistent affect influence attention and action without replacing drives."""
        state = self.state
        tendencies = list(base.action_tendencies)
        if state.boundary_activation >= 0.18:
            tendencies.append(
                ActionTendency(
                    "diagnose",
                    min(0.75, state.boundary_activation),
                    "clarify a socially relevant boundary conflict",
                    dimension="BOUNDARY",
                )
            )
        if state.withdrawal_pressure >= 0.18 or state.threat >= 0.22:
            tendencies.append(
                ActionTendency(
                    "introspect",
                    min(0.7, max(state.withdrawal_pressure, state.threat)),
                    "regulate and verify a socially relevant threat appraisal",
                    dimension="REGULATE",
                )
            )
        priority = list(base.attention_priority)
        for ref in state.active_event_refs[-4:]:
            token = f"sig:{ref}"
            if token not in priority:
                priority.insert(0, token)
        return replace(
            base,
            valence=_clip_signed(base.valence + 0.6 * state.valence),
            arousal=_clip(max(base.arousal, state.arousal)),
            urgency=_clip(max(base.urgency, 0.65 * state.threat)),
            threat=_clip(max(base.threat, state.threat)),
            attention_bias={
                **base.attention_bias,
                "social_boundary": state.boundary_activation,
                "social_withdrawal": state.withdrawal_pressure,
                "social_affiliation": state.affiliation,
            },
            action_tendencies=sorted(
                tendencies, key=lambda tendency: tendency.strength, reverse=True
            ),
            attention_priority=priority,
            approach_avoid_bias=_clip_signed(
                base.approach_avoid_bias
                + 0.25 * state.affiliation
                - 0.35 * state.withdrawal_pressure
            ),
            persistence=_clip(
                base.persistence + 0.2 * state.boundary_activation - 0.15 * state.threat
            ),
        )

    def context(self, *, partner_id: str | None = None) -> dict[str, object]:
        state = self.state
        partner = state.partners.get(partner_id or "")
        return {
            "valence": round(state.valence, 4),
            "arousal": round(state.arousal, 4),
            "threat": round(state.threat, 4),
            "boundary_activation": round(state.boundary_activation, 4),
            "withdrawal_pressure": round(state.withdrawal_pressure, 4),
            "affiliation": round(state.affiliation, 4),
            "active_event_refs": list(state.active_event_refs[-8:]),
            "partner": (
                None
                if partner is None
                else {
                    "partner_id": partner.partner_id,
                    "valence": round(partner.valence, 4),
                    "tension": round(partner.tension, 4),
                    "affiliation": round(partner.affiliation, 4),
                    "boundary_activation": round(partner.boundary_activation, 4),
                    "withdrawal_pressure": round(partner.withdrawal_pressure, 4),
                    "unresolved_event_refs": list(partner.unresolved_event_refs[-8:]),
                }
            ),
            "interpretation": (
                "These are persistent functional appraisal variables and action biases, "
                "not evidence of subjective feeling."
            ),
        }

    def _apply(self, appraisal: SocialAppraisal) -> None:
        state = self.state
        if appraisal.repair > 0:
            repair_strength = min(0.75, appraisal.repair)
            state.arousal *= 1.0 - 0.45 * repair_strength
            state.threat *= 1.0 - 0.6 * repair_strength
            state.boundary_activation *= 1.0 - 0.55 * repair_strength
            state.withdrawal_pressure *= 1.0 - 0.55 * repair_strength
        state.valence = _clip_signed(state.valence * 0.72 + appraisal.valence_delta)
        state.arousal = _clip(state.arousal * 0.78 + appraisal.arousal_delta)
        state.threat = _clip(state.threat * 0.8 + appraisal.threat_delta)
        state.boundary_activation = _clip(
            state.boundary_activation * 0.82 + appraisal.boundary_delta
        )
        state.withdrawal_pressure = _clip(
            state.withdrawal_pressure * 0.82 + appraisal.withdrawal_delta
        )
        state.affiliation = _clip_signed(
            state.affiliation * 0.9 + appraisal.affiliation_delta
        )
        state.last_updated_at = appraisal.occurred_at
        state.active_event_refs.append(appraisal.event_ref)
        state.active_event_refs = state.active_event_refs[-32:]
        state.appraisals.append(appraisal)
        state.appraisals = state.appraisals[-256:]

        partner = state.partners.get(appraisal.partner_id) or PartnerAffect(
            partner_id=appraisal.partner_id
        )
        if appraisal.repair > 0:
            repair_strength = min(0.75, appraisal.repair)
            partner.tension *= 1.0 - 0.6 * repair_strength
            partner.boundary_activation *= 1.0 - 0.55 * repair_strength
            partner.withdrawal_pressure *= 1.0 - 0.55 * repair_strength
        partner.valence = _clip_signed(
            partner.valence * 0.72 + appraisal.valence_delta
        )
        partner.tension = _clip(
            partner.tension * 0.8
            + appraisal.arousal_delta
            + 0.4 * appraisal.threat_delta
        )
        partner.affiliation = _clip_signed(
            partner.affiliation * 0.9 + appraisal.affiliation_delta
        )
        partner.boundary_activation = _clip(
            partner.boundary_activation * 0.82 + appraisal.boundary_delta
        )
        partner.withdrawal_pressure = _clip(
            partner.withdrawal_pressure * 0.82 + appraisal.withdrawal_delta
        )
        partner.last_updated_at = appraisal.occurred_at
        if appraisal.goal_congruence < 0 or appraisal.norm_violation > 0:
            partner.unresolved_event_refs.append(appraisal.event_ref)
            partner.unresolved_event_refs = partner.unresolved_event_refs[-32:]
        elif appraisal.repair >= 0.35:
            partner.unresolved_event_refs = partner.unresolved_event_refs[-4:]
        state.partners[appraisal.partner_id] = partner


def _hits(text: str, cues: tuple[str, ...]) -> int:
    return sum(1 for cue in cues if cue in text)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clip_signed(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
