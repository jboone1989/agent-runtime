"Evidence-first diagnostic reasoning with guarded failure scope and reusable lessons."
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable


EVIDENCE_FIRST_DIAGNOSTIC_METHOD: tuple[str, ...] = (
    "OBSERVE: preserve the exact symptom, error, timestamp, and machine evidence before explaining it.",
    "BOUND: identify the affected layers and the narrowest failure scope; do not widen a claim without wider evidence.",
    "HYPOTHESIZE: keep multiple plausible causes and state what would support or falsify each one.",
    "PROBE: run the cheapest, lowest-risk discriminating test that separates the leading hypotheses.",
    "LOCALIZE: assign the failure to the narrowest scope supported by evidence, and keep broader claims explicitly unproven.",
    "REMEDIATE: prefer the smallest reversible change that addresses the localized cause.",
    "VERIFY: repeat the original path and add a regression check so actuator success is not confused with problem resolution.",
    "LEARN: persist the symptom signature, discriminating probe, conclusion, fix, verification, and anti-pattern for reuse.",
)


class DiagnosticStage(str, Enum):
    OBSERVE = "OBSERVE"
    BOUND = "BOUND"
    HYPOTHESIZE = "HYPOTHESIZE"
    PROBE = "PROBE"
    LOCALIZE = "LOCALIZE"
    REMEDIATE = "REMEDIATE"
    VERIFY = "VERIFY"
    LEARN = "LEARN"


class HypothesisStatus(str, Enum):
    UNTESTED = "UNTESTED"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"


class ObservationKind(str, Enum):
    STATE = "STATE"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"


class ProbeStatus(str, Enum):
    PLANNED = "PLANNED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class DiagnosticObservation:
    observation_id: str
    statement: str
    scope: str
    kind: ObservationKind
    evidence_refs: tuple[str, ...]
    observed_at: str


@dataclass
class DiagnosticHypothesis:
    hypothesis_id: str
    statement: str
    scope: str
    predicts: str
    falsifier: str
    status: HypothesisStatus = HypothesisStatus.UNTESTED
    support_observation_ids: list[str] = field(default_factory=list)
    refute_observation_ids: list[str] = field(default_factory=list)


@dataclass
class DiagnosticProbe:
    probe_id: str
    description: str
    target_hypothesis_ids: tuple[str, ...]
    target_scope: str
    expected_if_true: str
    expected_if_false: str
    estimated_cost: float = 0.0
    reversible: bool = True
    status: ProbeStatus = ProbeStatus.PLANNED
    result_observation_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosticConclusion:
    hypothesis_id: str
    summary: str
    scope: str
    evidence_refs: tuple[str, ...]
    concluded_at: str


@dataclass(frozen=True)
class DiagnosticLesson:
    lesson_id: str
    trigger_tags: tuple[str, ...]
    principle: str
    procedure: tuple[str, ...]
    anti_patterns: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    created_at: str
    source_case_id: str


@dataclass
class DiagnosticCase:
    case_id: str
    symptom: str
    scope_ladder: tuple[str, ...]
    tags: tuple[str, ...]
    created_at: str
    stage: DiagnosticStage = DiagnosticStage.OBSERVE
    observations: list[DiagnosticObservation] = field(default_factory=list)
    hypotheses: list[DiagnosticHypothesis] = field(default_factory=list)
    probes: list[DiagnosticProbe] = field(default_factory=list)
    conclusion: DiagnosticConclusion | None = None
    remediation: str = ""
    verification_evidence_refs: list[str] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        *,
        symptom: str,
        scope_ladder: Iterable[str],
        tags: Iterable[str] = (),
        now: datetime,
    ) -> "DiagnosticCase":
        scopes = tuple(dict.fromkeys(str(x).strip() for x in scope_ladder if str(x).strip()))
        if not scopes:
            raise ValueError("diagnostic case requires a scope ladder")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("diagnostic case time requires timezone awareness")
        clean_symptom = " ".join(str(symptom).split())
        digest = sha256(
            json.dumps(
                {"symptom": clean_symptom, "scopes": scopes, "tags": sorted(set(tags))},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            case_id="diagnostic-case:" + digest,
            symptom=clean_symptom,
            scope_ladder=scopes,
            tags=tuple(sorted(set(str(x) for x in tags if str(x)))),
            created_at=_utc_iso(now),
        )

    def observe(
        self,
        *,
        statement: str,
        scope: str,
        kind: ObservationKind,
        evidence_refs: Iterable[str],
        now: datetime,
    ) -> DiagnosticObservation:
        self._require_scope(scope)
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        if not refs:
            raise ValueError("diagnostic observation requires evidence refs")
        clean = " ".join(str(statement).split())
        digest = sha256(
            f"{self.case_id}\0{scope}\0{kind.value}\0{clean}\0{refs}".encode("utf-8")
        ).hexdigest()[:24]
        item = DiagnosticObservation(
            observation_id="diagnostic-observation:" + digest,
            statement=clean,
            scope=scope,
            kind=kind,
            evidence_refs=refs,
            observed_at=_utc_iso(now),
        )
        if not any(x.observation_id == item.observation_id for x in self.observations):
            self.observations.append(item)
        self.stage = max(self.stage, DiagnosticStage.BOUND, key=_stage_rank)
        return item

    def hypothesize(
        self,
        *,
        statement: str,
        scope: str,
        predicts: str,
        falsifier: str,
    ) -> DiagnosticHypothesis:
        self._require_scope(scope)
        clean = " ".join(str(statement).split())
        digest = sha256(
            f"{self.case_id}\0{scope}\0{clean}\0{predicts}\0{falsifier}".encode("utf-8")
        ).hexdigest()[:24]
        item = DiagnosticHypothesis(
            hypothesis_id="diagnostic-hypothesis:" + digest,
            statement=clean,
            scope=scope,
            predicts=" ".join(str(predicts).split()),
            falsifier=" ".join(str(falsifier).split()),
        )
        prior = self._hypothesis(item.hypothesis_id, missing_ok=True)
        if prior is not None:
            return prior
        self.hypotheses.append(item)
        self.stage = DiagnosticStage.HYPOTHESIZE
        return item

    def support(self, hypothesis_id: str, observation_ids: Iterable[str]) -> DiagnosticHypothesis:
        hypothesis = self._hypothesis(hypothesis_id)
        observations = self._observations(observation_ids)
        if not observations:
            raise ValueError("support requires observations")
        required_rank = self._scope_rank(hypothesis.scope)
        if max(self._scope_rank(item.scope) for item in observations) < required_rank:
            raise ValueError(
                "cannot generalize a diagnostic conclusion beyond the scope of its supporting evidence"
            )
        hypothesis.support_observation_ids = list(
            dict.fromkeys([*hypothesis.support_observation_ids, *(x.observation_id for x in observations)])
        )
        hypothesis.status = HypothesisStatus.SUPPORTED
        self.stage = DiagnosticStage.LOCALIZE
        return hypothesis

    def refute(self, hypothesis_id: str, observation_ids: Iterable[str]) -> DiagnosticHypothesis:
        hypothesis = self._hypothesis(hypothesis_id)
        observations = self._observations(observation_ids)
        if not observations:
            raise ValueError("refutation requires observations")
        hypothesis.refute_observation_ids = list(
            dict.fromkeys([*hypothesis.refute_observation_ids, *(x.observation_id for x in observations)])
        )
        hypothesis.status = HypothesisStatus.REFUTED
        return hypothesis

    def plan_probe(
        self,
        *,
        description: str,
        target_hypothesis_ids: Iterable[str],
        target_scope: str,
        expected_if_true: str,
        expected_if_false: str,
        estimated_cost: float = 0.0,
        reversible: bool = True,
    ) -> DiagnosticProbe:
        self._require_scope(target_scope)
        targets = tuple(dict.fromkeys(str(x) for x in target_hypothesis_ids if str(x)))
        if not targets:
            raise ValueError("diagnostic probe requires a target hypothesis")
        for hypothesis_id in targets:
            self._hypothesis(hypothesis_id)
        digest = sha256(
            f"{self.case_id}\0{description}\0{targets}\0{target_scope}".encode("utf-8")
        ).hexdigest()[:24]
        probe = DiagnosticProbe(
            probe_id="diagnostic-probe:" + digest,
            description=" ".join(str(description).split()),
            target_hypothesis_ids=targets,
            target_scope=target_scope,
            expected_if_true=" ".join(str(expected_if_true).split()),
            expected_if_false=" ".join(str(expected_if_false).split()),
            estimated_cost=max(0.0, float(estimated_cost)),
            reversible=bool(reversible),
        )
        prior = next((x for x in self.probes if x.probe_id == probe.probe_id), None)
        if prior is not None:
            return prior
        self.probes.append(probe)
        self.stage = DiagnosticStage.PROBE
        return probe

    def complete_probe(self, probe_id: str, observation_ids: Iterable[str]) -> DiagnosticProbe:
        probe = next((x for x in self.probes if x.probe_id == probe_id), None)
        if probe is None:
            raise KeyError(probe_id)
        observations = self._observations(observation_ids)
        if not observations:
            raise ValueError("completed diagnostic probe requires observations")
        probe.result_observation_ids = list(
            dict.fromkeys([*probe.result_observation_ids, *(x.observation_id for x in observations)])
        )
        probe.status = ProbeStatus.COMPLETED
        return probe

    def conclude(self, hypothesis_id: str, *, summary: str, now: datetime) -> DiagnosticConclusion:
        hypothesis = self._hypothesis(hypothesis_id)
        if hypothesis.status != HypothesisStatus.SUPPORTED:
            raise ValueError("diagnostic conclusion requires a supported hypothesis")
        observations = self._observations(hypothesis.support_observation_ids)
        refs = tuple(
            dict.fromkeys(ref for item in observations for ref in item.evidence_refs)
        )
        conclusion = DiagnosticConclusion(
            hypothesis_id=hypothesis.hypothesis_id,
            summary=" ".join(str(summary).split()),
            scope=hypothesis.scope,
            evidence_refs=refs,
            concluded_at=_utc_iso(now),
        )
        self.conclusion = conclusion
        self.stage = DiagnosticStage.LOCALIZE
        return conclusion

    def record_remediation(self, description: str) -> None:
        if self.conclusion is None:
            raise ValueError("remediation requires a localized diagnostic conclusion")
        self.remediation = " ".join(str(description).split())
        self.stage = DiagnosticStage.REMEDIATE

    def verify(self, evidence_refs: Iterable[str]) -> None:
        refs = [str(x) for x in evidence_refs if str(x)]
        if not refs:
            raise ValueError("verification requires evidence refs")
        self.verification_evidence_refs = list(
            dict.fromkeys([*self.verification_evidence_refs, *refs])
        )
        self.stage = DiagnosticStage.VERIFY

    def to_lesson(
        self,
        *,
        principle: str,
        anti_patterns: Iterable[str] = (),
        procedure: Iterable[str] = EVIDENCE_FIRST_DIAGNOSTIC_METHOD,
        now: datetime,
    ) -> DiagnosticLesson:
        if self.conclusion is None:
            raise ValueError("diagnostic lesson requires a conclusion")
        if not self.verification_evidence_refs:
            raise ValueError("diagnostic lesson requires verification evidence")
        refs = tuple(
            dict.fromkeys(
                [
                    *self.conclusion.evidence_refs,
                    *self.verification_evidence_refs,
                ]
            )
        )
        digest = sha256(
            f"{self.case_id}\0{principle}\0{self.tags}".encode("utf-8")
        ).hexdigest()[:24]
        self.stage = DiagnosticStage.LEARN
        return DiagnosticLesson(
            lesson_id="diagnostic-lesson:" + digest,
            trigger_tags=self.tags,
            principle=" ".join(str(principle).split()),
            procedure=tuple(str(x) for x in procedure if str(x)),
            anti_patterns=tuple(str(x) for x in anti_patterns if str(x)),
            evidence_refs=refs,
            created_at=_utc_iso(now),
            source_case_id=self.case_id,
        )

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def _require_scope(self, scope: str) -> None:
        if scope not in self.scope_ladder:
            raise ValueError(f"unknown diagnostic scope: {scope}")

    def _scope_rank(self, scope: str) -> int:
        self._require_scope(scope)
        return self.scope_ladder.index(scope)

    def _hypothesis(self, hypothesis_id: str, *, missing_ok: bool = False) -> DiagnosticHypothesis | None:
        found = next((x for x in self.hypotheses if x.hypothesis_id == hypothesis_id), None)
        if found is None and not missing_ok:
            raise KeyError(hypothesis_id)
        return found

    def _observations(self, observation_ids: Iterable[str]) -> list[DiagnosticObservation]:
        wanted = set(str(x) for x in observation_ids if str(x))
        found = [x for x in self.observations if x.observation_id in wanted]
        missing = wanted - {x.observation_id for x in found}
        if missing:
            raise KeyError(sorted(missing)[0])
        return found


class DiagnosticMemory:
    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "diagnostics"

    def persist_case(self, case: DiagnosticCase) -> Path:
        path = self.root / "cases" / (case.case_id.split(":")[-1] + ".json")
        self._atomic_json(path, case.to_payload())
        return path

    def remember(self, lesson: DiagnosticLesson) -> Path:
        path = self.root / "lessons" / (lesson.lesson_id.split(":")[-1] + ".json")
        self._atomic_json(path, asdict(lesson))
        return path

    def lessons_for(self, tags: Iterable[str], *, limit: int = 5) -> list[DiagnosticLesson]:
        query = set(str(x) for x in tags if str(x))
        rows: list[tuple[int, str, DiagnosticLesson]] = []
        folder = self.root / "lessons"
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                lesson = DiagnosticLesson(
                    lesson_id=str(payload["lesson_id"]),
                    trigger_tags=tuple(payload.get("trigger_tags") or ()),
                    principle=str(payload.get("principle") or ""),
                    procedure=tuple(payload.get("procedure") or ()),
                    anti_patterns=tuple(payload.get("anti_patterns") or ()),
                    evidence_refs=tuple(payload.get("evidence_refs") or ()),
                    created_at=str(payload.get("created_at") or ""),
                    source_case_id=str(payload.get("source_case_id") or ""),
                )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            score = len(query.intersection(lesson.trigger_tags))
            if score:
                rows.append((score, lesson.created_at, lesson))
        rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in rows[: max(1, int(limit))]]

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


def _stage_rank(stage: DiagnosticStage) -> int:
    return list(DiagnosticStage).index(stage)


def _utc_iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("diagnostic time requires timezone awareness")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "DiagnosticCase",
    "DiagnosticConclusion",
    "DiagnosticHypothesis",
    "DiagnosticLesson",
    "DiagnosticMemory",
    "DiagnosticObservation",
    "DiagnosticProbe",
    "DiagnosticStage",
    "EVIDENCE_FIRST_DIAGNOSTIC_METHOD",
    "HypothesisStatus",
    "ObservationKind",
    "ProbeStatus",
]
