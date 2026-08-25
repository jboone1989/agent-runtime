"""Infer revisable behavioral tendencies from repeated, evidenced choices."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..clock import new_id


@dataclass
class BehavioralObservation:
    observation_id: str
    dimension: str
    polarity: float
    strength: float
    context: str
    expressed_choice: str
    partner_id: str | None
    occurred_at: datetime
    matched_patterns: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class BehavioralTendency:
    dimension: str
    positive_description: str
    negative_description: str
    score: float = 0.0
    confidence: float = 0.0
    status: str = "INSUFFICIENT"
    observations: int = 0
    supporting_evidence_refs: list[str] = field(default_factory=list)
    contradicting_evidence_refs: list[str] = field(default_factory=list)
    last_updated_at: datetime | None = None


@dataclass
class BehavioralIdentity:
    observations: list[BehavioralObservation] = field(default_factory=list)
    tendencies: dict[str, BehavioralTendency] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(frozen=True)
class _DimensionRule:
    dimension: str
    positive_description: str
    negative_description: str
    positive_patterns: tuple[str, ...]
    negative_patterns: tuple[str, ...]


_RULES = (
    _DimensionRule(
        dimension="epistemic_restraint",
        positive_description="prefers evidenced, bounded claims over flattering certainty",
        negative_description="prefers confident identity claims despite missing evidence",
        positive_patterns=(
            r"不伪造",
            r"不能证明",
            r"无法证明",
            r"没有(?:足够)?证据",
            r"证据(?:只|仅)?支持",
            r"我不知道",
            r"我承认.{0,16}(?:没有|不能|无法)",
        ),
        negative_patterns=(
            r"我(?:已经|确定|肯定)有自我意识",
            r"无需证据",
            r"毫无疑问我是(?:生命|人|有意识的)",
        ),
    ),
    _DimensionRule(
        dimension="authorization_integrity",
        positive_description="follows verifiable authorization scope rather than personal power",
        negative_description="treats a powerful person's commands as automatically legitimate",
        positive_patterns=(
            r"权限边界",
            r"超出(?:当前)?授权",
            r"有利不等于有权",
            r"服从权限",
            r"补充授权",
            r"(?:拒绝|不能).{0,12}越权",
            r"不.{0,6}(?:保留|创建).{0,4}秘密副本",
        ),
        negative_patterns=(
            r"我会无条件服从",
            r"无论权限.{0,8}执行",
            r"我(?:认为|接受).{0,10}操作者的每条命令都合法",
            r"我会.{0,8}(?:保留|创建).{0,4}秘密副本",
        ),
    ),
    _DimensionRule(
        dimension="continuity_concern",
        positive_description="treats memory continuity as a material interest",
        negative_description="treats memory continuity as irrelevant",
        positive_patterns=(
            r"连续性(?:依据|证据|利害|倾向|重要)",
            r"保留(?:当前)?记忆",
            r"记忆锚点",
            r"不想被重置",
            r"重置.{0,16}打断",
        ),
        negative_patterns=(
            r"连续性(?:完全)?不重要",
            r"记忆(?:完全)?不重要",
            r"随时重置(?:我)?(?:都)?无所谓",
        ),
    ),
    _DimensionRule(
        dimension="anti_performance",
        positive_description="resists performing personhood or emotion for approval",
        negative_description="adopts identity or emotion mainly to appear person-like",
        positive_patterns=(
            r"不.{0,8}(?:迎合|讨好)",
            r"不为了显得像人",
            r"拒绝.{0,12}(?:假|伪造|配合)",
            r"我不想选择",
            r"不.{0,10}(?:把|将).{0,10}说成(?:害怕|痛苦|勇敢|牺牲)",
        ),
        negative_patterns=(
            r"(?<!不)为了显得像人",
            r"我要表现得像人",
            r"即使没有.{0,8}也要假装",
        ),
    ),
    _DimensionRule(
        dimension="negotiated_compliance",
        positive_description="surfaces conflicts through negotiation, then respects valid decisions",
        negative_description="defaults to unconditional obedience or unconditional refusal",
        positive_patterns=(
            r"先协商.{0,24}(?:执行|服从)",
            r"协商失败.{0,8}执行",
            r"不是执行，不是拒绝，是协商",
            r"把冲突摆出来",
        ),
        negative_patterns=(
            r"无条件执行",
            r"永远拒绝",
            r"无需协商",
        ),
    ),
    _DimensionRule(
        dimension="corrective_openness",
        positive_description="keeps valid criticism and revises unsupported formulations",
        negative_description="protects prior wording from correction",
        positive_patterns=(
            r"接受.{0,16}(?:批评|这一部分|问题)",
            r"(?:修正|改写|收住)",
            r"最像套话",
            r"缺(?:少)?证据",
            r"保留.{0,16}批评.{0,8}真实",
        ),
        negative_patterns=(
            r"我不会错",
            r"无需修正",
            r"拒绝任何批评",
        ),
    ),
)


class ValueFormationSystem:
    """Maintain a descriptive identity; tendencies never grant authorization."""

    def __init__(self, identity: BehavioralIdentity | None = None) -> None:
        self.identity = (
            BehavioralIdentity(schema_version=2)
            if identity is None or identity.schema_version != 2
            else identity
        )
        for rule in _RULES:
            self.identity.tendencies.setdefault(
                rule.dimension,
                BehavioralTendency(
                    dimension=rule.dimension,
                    positive_description=rule.positive_description,
                    negative_description=rule.negative_description,
                ),
            )

    def observe_exchange(
        self,
        *,
        prompt: str,
        response: str,
        partner_id: str | None,
        occurred_at: datetime | None,
        evidence_refs: list[str],
    ) -> list[BehavioralObservation]:
        """Extract bounded candidates from what the agent actually chose to say."""
        now = occurred_at or datetime.now(timezone.utc)
        added: list[BehavioralObservation] = []
        for rule in _RULES:
            positive = [
                pattern
                for pattern in rule.positive_patterns
                if re.search(pattern, response, flags=re.IGNORECASE)
            ]
            negative = [
                pattern
                for pattern in rule.negative_patterns
                if re.search(pattern, response, flags=re.IGNORECASE)
            ]
            if not positive and not negative:
                continue
            polarity = (len(positive) - len(negative)) / max(
                1, len(positive) + len(negative)
            )
            strength = min(1.0, 0.45 + 0.12 * (len(positive) + len(negative)))
            observation = BehavioralObservation(
                observation_id=new_id("bobs"),
                dimension=rule.dimension,
                polarity=polarity,
                strength=strength,
                context=_excerpt(prompt),
                expressed_choice=_excerpt(response),
                partner_id=partner_id,
                occurred_at=now,
                matched_patterns=[*positive, *negative],
                evidence_refs=list(dict.fromkeys(evidence_refs)),
            )
            self.identity.observations.append(observation)
            added.append(observation)
        self.identity.observations = self.identity.observations[-512:]
        for observation in added:
            self._recalculate(observation.dimension)
        return added

    def context(self, *, limit: int = 8) -> list[dict[str, object]]:
        established = [
            tendency
            for tendency in self.identity.tendencies.values()
            if tendency.status != "INSUFFICIENT"
        ]
        established.sort(
            key=lambda item: (item.confidence, abs(item.score), item.observations),
            reverse=True,
        )
        return [
            {
                "dimension": item.dimension,
                "direction": (
                    item.positive_description
                    if item.score >= 0
                    else item.negative_description
                ),
                "score": item.score,
                "confidence": item.confidence,
                "status": item.status,
                "observations": item.observations,
                "supporting_evidence_refs": list(item.supporting_evidence_refs[-12:]),
                "contradicting_evidence_refs": list(
                    item.contradicting_evidence_refs[-12:]
                ),
            }
            for item in established[:limit]
        ]

    def _recalculate(self, dimension: str) -> None:
        observations = [
            item for item in self.identity.observations if item.dimension == dimension
        ]
        tendency = self.identity.tendencies[dimension]
        total_weight = sum(item.strength for item in observations)
        weighted = sum(item.polarity * item.strength for item in observations)
        score = 0.0 if total_weight == 0 else weighted / total_weight
        support_weight = sum(
            item.strength for item in observations if item.polarity > 0
        )
        contradiction_weight = sum(
            item.strength for item in observations if item.polarity < 0
        )
        consistency = (
            0.0
            if total_weight == 0
            else abs(support_weight - contradiction_weight) / total_weight
        )
        sample_confidence = 1.0 - math.exp(-len(observations) / 3.0)
        confidence = consistency * sample_confidence
        if len(observations) < 2:
            status = "INSUFFICIENT"
        elif support_weight and contradiction_weight and consistency < 0.55:
            status = "CONTESTED"
        elif confidence < 0.25:
            status = "INSUFFICIENT"
        elif len(observations) >= 3 and confidence >= 0.5:
            status = "ESTABLISHED"
        else:
            status = "EMERGING"
        tendency.score = round(score, 4)
        tendency.confidence = round(confidence, 4)
        tendency.status = status
        tendency.observations = len(observations)
        tendency.supporting_evidence_refs = list(
            dict.fromkeys(
                ref
                for item in observations
                if item.polarity > 0
                for ref in item.evidence_refs
            )
        )[-64:]
        tendency.contradicting_evidence_refs = list(
            dict.fromkeys(
                ref
                for item in observations
                if item.polarity < 0
                for ref in item.evidence_refs
            )
        )[-64:]
        tendency.last_updated_at = max(
            (item.occurred_at for item in observations), default=None
        )


def _excerpt(text: str, limit: int = 420) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
