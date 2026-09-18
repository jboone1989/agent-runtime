"""Evidence-driven competition and replacement for non-core capability implementations.

A capability is the stable *need* ("browser automation", "HTTP transport", ...).
Its implementation is replaceable.  Candidates are discovered continuously but
cannot replace an incumbent from claims alone: they must pass matched shadow
trials and preserve hard safety/compatibility floors.

The Runtime intentionally does not know how to search GitHub, install a package,
or call a vendor.  Adopters supply candidates and trial evidence.  Runtime owns
the comparison, promotion gate, rollback record, and the immutable-core boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

from .inventory import CapabilityInventory
from .lifecycle import CapabilityLifecycleService
from .model import CapabilityBinding


class ReplacementStatus(str, Enum):
    SEARCH_DUE = "SEARCH_DUE"
    SHADOW_TRIAL = "SHADOW_TRIAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    KEEP_CURRENT = "KEEP_CURRENT"
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"
    CORE_PROTECTED = "CORE_PROTECTED"


@dataclass(frozen=True)
class ReplaceableCapability:
    capability_id: str
    current_binding_id: str
    current_provider: str
    current_version: str | None = None
    core_protected: bool = False
    search_interval_cycles: int = 24
    minimum_trials: int = 3
    improvement_threshold: float = 0.05
    max_regression: float = 0.05
    search_terms: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.current_binding_id.strip():
            raise ValueError("replaceable capability requires capability and binding ids")
        if not self.current_provider.strip():
            raise ValueError("replaceable capability requires current_provider")
        if self.search_interval_cycles < 1 or self.minimum_trials < 1:
            raise ValueError("replacement intervals/trials must be positive")
        for name in ("improvement_threshold", "max_regression"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within 0..1")
        object.__setattr__(
            self,
            "search_terms",
            tuple(dict.fromkeys(str(x).strip() for x in self.search_terms if str(x).strip())),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(dict.fromkeys(str(x) for x in self.evidence_refs if str(x))),
        )


@dataclass(frozen=True)
class CapabilityChallenger:
    capability_id: str
    candidate_id: str
    provider: str
    version: str | None = None
    source: str = ""
    description: str = ""
    discovered_cycle: int = 0
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.candidate_id.strip():
            raise ValueError("challenger requires capability_id and candidate_id")
        if not self.provider.strip():
            raise ValueError("challenger requires provider")
        if self.discovered_cycle < 0:
            raise ValueError("discovered_cycle must be non-negative")


@dataclass(frozen=True)
class CapabilityTrial:
    capability_id: str
    implementation_id: str
    benchmark_id: str
    cycle: int
    success: bool
    correctness: float
    reliability: float
    security: float
    compatibility: float
    maintenance: float = 0.5
    cost_efficiency: float = 0.5
    latency_efficiency: float = 0.5
    reversibility: float = 1.0
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.implementation_id.strip():
            raise ValueError("trial requires capability/implementation ids")
        if not self.benchmark_id.strip() or self.cycle < 0:
            raise ValueError("trial requires benchmark_id and non-negative cycle")
        for name in (
            "correctness",
            "reliability",
            "security",
            "compatibility",
            "maintenance",
            "cost_efficiency",
            "latency_efficiency",
            "reversibility",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within 0..1")


@dataclass(frozen=True)
class CapabilityReplacementDecision:
    capability_id: str
    candidate_id: str | None
    status: ReplacementStatus
    cycle: int
    reason: str
    current_metrics: Mapping[str, float] = field(default_factory=dict)
    challenger_metrics: Mapping[str, float] = field(default_factory=dict)
    improved_dimensions: tuple[str, ...] = ()
    regressed_dimensions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityReplacementReceipt:
    capability_id: str
    cycle: int
    previous_binding: Mapping[str, object]
    replacement_binding: Mapping[str, object]
    evidence_refs: tuple[str, ...] = ()


_METRICS = (
    "correctness",
    "reliability",
    "security",
    "compatibility",
    "maintenance",
    "cost_efficiency",
    "latency_efficiency",
    "reversibility",
)
_HARD_FLOORS = {
    "correctness": 0.80,
    "reliability": 0.70,
    "security": 0.80,
    "compatibility": 0.90,
    "reversibility": 0.60,
}
_PROTECTED_REGRESSION = {
    "correctness",
    "reliability",
    "security",
    "compatibility",
    "reversibility",
}


class CapabilityReplacementEngine:
    """Persistent evidence ledger + Pareto-style promotion gate.

    It deliberately has no "best library" score.  A challenger must clear hard
    floors, avoid meaningful regressions in protected dimensions, and show a
    real improvement in at least one operational dimension on matched trials.
    """

    schema_version = "capability-replacement.v1"

    def __init__(self, *, state_path: Path | None = None) -> None:
        self.state_path = Path(state_path) if state_path is not None else None
        self.slots: dict[str, ReplaceableCapability] = {}
        self.challengers: dict[str, CapabilityChallenger] = {}
        self.trials: list[CapabilityTrial] = []
        self.last_search_cycle: dict[str, int] = {}
        self.decisions: list[CapabilityReplacementDecision] = []
        self.replacements: list[CapabilityReplacementReceipt] = []
        self._load()

    def register(self, spec: ReplaceableCapability) -> None:
        existing = self.slots.get(spec.capability_id)
        if existing is not None and existing.core_protected and not spec.core_protected:
            raise ValueError("core-protected capability cannot become replaceable")
        self.slots[spec.capability_id] = spec
        self._persist()

    def search_due(
        self,
        capability_id: str,
        *,
        cycle: int,
        health: float = 1.0,
        opportunity: bool = False,
    ) -> bool:
        spec = self._spec(capability_id)
        if spec.core_protected:
            return False
        if opportunity or float(health) < 0.70:
            return True
        last = self.last_search_cycle.get(capability_id)
        return last is None or int(cycle) - int(last) >= spec.search_interval_cycles

    def record_search(
        self,
        capability_id: str,
        *,
        cycle: int,
        evidence_refs: Iterable[str] = (),
    ) -> CapabilityReplacementDecision:
        spec = self._spec(capability_id)
        self.last_search_cycle[capability_id] = int(cycle)
        decision = CapabilityReplacementDecision(
            capability_id=capability_id,
            candidate_id=None,
            status=(
                ReplacementStatus.CORE_PROTECTED
                if spec.core_protected
                else ReplacementStatus.SEARCH_DUE
            ),
            cycle=int(cycle),
            reason=(
                "core subject boundary is immutable"
                if spec.core_protected
                else "replacement search completed; discovered candidates require shadow evidence"
            ),
            evidence_refs=tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x))),
        )
        self.decisions.append(decision)
        self._persist()
        return decision

    def discover(self, challenger: CapabilityChallenger) -> None:
        spec = self._spec(challenger.capability_id)
        if spec.core_protected:
            raise ValueError("cannot register challenger for core-protected capability")
        if challenger.candidate_id == spec.current_binding_id:
            return
        self.challengers[challenger.candidate_id] = challenger
        self._persist()

    def record_trial(self, trial: CapabilityTrial) -> None:
        spec = self._spec(trial.capability_id)
        allowed = {spec.current_binding_id}
        allowed.update(
            item.candidate_id
            for item in self.challengers.values()
            if item.capability_id == trial.capability_id
        )
        if trial.implementation_id not in allowed:
            raise KeyError(trial.implementation_id)
        key = (
            trial.capability_id,
            trial.implementation_id,
            trial.benchmark_id,
            trial.cycle,
        )
        if not any(
            (
                item.capability_id,
                item.implementation_id,
                item.benchmark_id,
                item.cycle,
            )
            == key
            for item in self.trials
        ):
            self.trials.append(trial)
        self._persist()

    def decide(
        self,
        capability_id: str,
        candidate_id: str,
        *,
        cycle: int,
    ) -> CapabilityReplacementDecision:
        spec = self._spec(capability_id)
        if spec.core_protected:
            return self._decision(
                capability_id,
                candidate_id,
                cycle,
                ReplacementStatus.CORE_PROTECTED,
                "core subject boundary is immutable",
            )
        challenger = self.challengers.get(candidate_id)
        if challenger is None or challenger.capability_id != capability_id:
            raise KeyError(candidate_id)

        current_rows = self._trials(capability_id, spec.current_binding_id)
        challenger_rows = self._trials(capability_id, candidate_id)
        current_by_bench = {row.benchmark_id: row for row in current_rows}
        challenger_by_bench = {row.benchmark_id: row for row in challenger_rows}
        matched = sorted(set(current_by_bench) & set(challenger_by_bench))
        if len(matched) < spec.minimum_trials:
            return self._decision(
                capability_id,
                candidate_id,
                cycle,
                ReplacementStatus.INSUFFICIENT_EVIDENCE,
                f"need {spec.minimum_trials} matched shadow trials; have {len(matched)}",
                evidence_refs=self._trial_refs(current_rows + challenger_rows),
            )

        current = self._aggregate([current_by_bench[key] for key in matched])
        proposed = self._aggregate([challenger_by_bench[key] for key in matched])
        failed_floor = [
            name for name, floor in _HARD_FLOORS.items()
            if proposed[name] < floor
        ]
        if failed_floor:
            return self._decision(
                capability_id,
                candidate_id,
                cycle,
                ReplacementStatus.REJECT,
                "challenger failed hard floors: " + ",".join(failed_floor),
                current=current,
                challenger=proposed,
                evidence_refs=self._trial_refs(current_rows + challenger_rows),
            )

        regressions = tuple(
            name
            for name in _METRICS
            if current[name] - proposed[name] > spec.max_regression
        )
        protected_regressions = tuple(
            name for name in regressions if name in _PROTECTED_REGRESSION
        )
        improvements = tuple(
            name
            for name in _METRICS
            if proposed[name] - current[name] >= spec.improvement_threshold
        )
        if protected_regressions:
            return self._decision(
                capability_id,
                candidate_id,
                cycle,
                ReplacementStatus.KEEP_CURRENT,
                "challenger regressed protected dimensions: "
                + ",".join(protected_regressions),
                current=current,
                challenger=proposed,
                improved=improvements,
                regressed=regressions,
                evidence_refs=self._trial_refs(current_rows + challenger_rows),
            )
        if not improvements:
            return self._decision(
                capability_id,
                candidate_id,
                cycle,
                ReplacementStatus.KEEP_CURRENT,
                "challenger showed no material improvement on matched trials",
                current=current,
                challenger=proposed,
                regressed=regressions,
                evidence_refs=self._trial_refs(current_rows + challenger_rows),
            )
        return self._decision(
            capability_id,
            candidate_id,
            cycle,
            ReplacementStatus.PROMOTE,
            "challenger passed hard floors and improved without protected regression",
            current=current,
            challenger=proposed,
            improved=improvements,
            regressed=regressions,
            evidence_refs=self._trial_refs(current_rows + challenger_rows),
        )

    def promote(
        self,
        decision: CapabilityReplacementDecision,
        *,
        replacement: CapabilityBinding,
        lifecycle: CapabilityLifecycleService,
    ) -> CapabilityReplacementReceipt:
        spec = self._spec(decision.capability_id)
        if spec.core_protected or decision.status is not ReplacementStatus.PROMOTE:
            raise ValueError("replacement promotion is not authorized by comparison evidence")
        if replacement.capability_id != decision.capability_id:
            raise ValueError("replacement targets wrong capability")
        current = lifecycle.inventory.get(decision.capability_id)
        if current is None:
            raise KeyError(decision.capability_id)
        previous = _binding_payload(current)
        lifecycle.replace(
            decision.capability_id,
            replacement,
            cycle=decision.cycle,
            reason=decision.reason,
            evidence_refs=decision.evidence_refs,
        )
        receipt = CapabilityReplacementReceipt(
            capability_id=decision.capability_id,
            cycle=decision.cycle,
            previous_binding=previous,
            replacement_binding=_binding_payload(replacement),
            evidence_refs=decision.evidence_refs,
        )
        self.replacements.append(receipt)
        self.slots[decision.capability_id] = ReplaceableCapability(
            **{
                **asdict(spec),
                "current_binding_id": replacement.binding_id
                or replacement.candidate_id
                or decision.candidate_id
                or "",
                "current_provider": replacement.provider,
                "current_version": replacement.version,
            }
        )
        self._persist()
        return receipt

    def rollback(
        self,
        capability_id: str,
        *,
        inventory: CapabilityInventory,
    ) -> CapabilityBinding:
        spec = self._spec(capability_id)
        if spec.core_protected:
            raise ValueError("core-protected capability has no replacement rollback")
        receipt = next(
            (item for item in reversed(self.replacements) if item.capability_id == capability_id),
            None,
        )
        if receipt is None:
            raise ValueError("no replacement receipt exists")
        restored = _binding_from_payload(receipt.previous_binding)
        inventory.register(restored)
        self.slots[capability_id] = ReplaceableCapability(
            **{
                **asdict(spec),
                "current_binding_id": restored.binding_id
                or restored.candidate_id
                or restored.provider,
                "current_provider": restored.provider,
                "current_version": restored.version,
            }
        )
        self._persist()
        return restored

    def _decision(
        self,
        capability_id: str,
        candidate_id: str | None,
        cycle: int,
        status: ReplacementStatus,
        reason: str,
        *,
        current: Mapping[str, float] | None = None,
        challenger: Mapping[str, float] | None = None,
        improved: tuple[str, ...] = (),
        regressed: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
    ) -> CapabilityReplacementDecision:
        decision = CapabilityReplacementDecision(
            capability_id=capability_id,
            candidate_id=candidate_id,
            status=status,
            cycle=int(cycle),
            reason=reason,
            current_metrics=dict(current or {}),
            challenger_metrics=dict(challenger or {}),
            improved_dimensions=improved,
            regressed_dimensions=regressed,
            evidence_refs=evidence_refs,
        )
        self.decisions.append(decision)
        self._persist()
        return decision

    def _spec(self, capability_id: str) -> ReplaceableCapability:
        try:
            return self.slots[capability_id]
        except KeyError as exc:
            raise KeyError(f"replacement capability not registered: {capability_id}") from exc

    def _trials(self, capability_id: str, implementation_id: str) -> list[CapabilityTrial]:
        return [
            item for item in self.trials
            if item.capability_id == capability_id
            and item.implementation_id == implementation_id
        ]

    @staticmethod
    def _aggregate(rows: list[CapabilityTrial]) -> dict[str, float]:
        out: dict[str, float] = {}
        for name in _METRICS:
            out[name] = sum(float(getattr(row, name)) for row in rows) / len(rows)
        out["success_rate"] = sum(1.0 if row.success else 0.0 for row in rows) / len(rows)
        return {key: round(value, 6) for key, value in out.items()}

    @staticmethod
    def _trial_refs(rows: list[CapabilityTrial]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            ref for row in rows for ref in row.evidence_refs if ref
        ))

    def _persist(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "slots": [asdict(item) for item in self.slots.values()],
            "challengers": [asdict(item) for item in self.challengers.values()],
            "trials": [asdict(item) for item in self.trials[-1000:]],
            "last_search_cycle": dict(self.last_search_cycle),
            "decisions": [
                {
                    **asdict(item),
                    "status": item.status.value,
                }
                for item in self.decisions[-500:]
            ],
            "replacements": [asdict(item) for item in self.replacements[-100:]],
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != self.schema_version:
            raise ValueError("capability replacement state version mismatch")
        self.slots = {
            item["capability_id"]: ReplaceableCapability(
                **{
                    **item,
                    "search_terms": tuple(item.get("search_terms") or ()),
                    "evidence_refs": tuple(item.get("evidence_refs") or ()),
                }
            )
            for item in raw.get("slots") or []
        }
        self.challengers = {
            item["candidate_id"]: CapabilityChallenger(
                **{
                    **item,
                    "evidence_refs": tuple(item.get("evidence_refs") or ()),
                }
            )
            for item in raw.get("challengers") or []
        }
        self.trials = [
            CapabilityTrial(
                **{
                    **item,
                    "evidence_refs": tuple(item.get("evidence_refs") or ()),
                }
            )
            for item in raw.get("trials") or []
        ]
        self.last_search_cycle = {
            str(key): int(value)
            for key, value in (raw.get("last_search_cycle") or {}).items()
        }
        self.decisions = [
            CapabilityReplacementDecision(
                **{
                    **item,
                    "status": ReplacementStatus(item["status"]),
                    "improved_dimensions": tuple(item.get("improved_dimensions") or ()),
                    "regressed_dimensions": tuple(item.get("regressed_dimensions") or ()),
                    "evidence_refs": tuple(item.get("evidence_refs") or ()),
                }
            )
            for item in raw.get("decisions") or []
        ]
        self.replacements = [
            CapabilityReplacementReceipt(
                **{
                    **item,
                    "evidence_refs": tuple(item.get("evidence_refs") or ()),
                }
            )
            for item in raw.get("replacements") or []
        ]


def _binding_payload(binding: CapabilityBinding) -> dict[str, object]:
    return {
        "capability_id": binding.capability_id,
        "kind": binding.kind.value,
        "provider": binding.provider,
        "binding_id": binding.binding_id,
        "candidate_id": binding.candidate_id,
        "access_mode": binding.access_mode.value,
        "state": binding.state.value,
        "version": binding.version,
        "side_effects": binding.side_effects.value,
        "entry_point": dict(binding.entry_point),
        "health": binding.health,
        "evidence_refs": list(binding.evidence_refs),
    }


def _binding_from_payload(raw: Mapping[str, object]) -> CapabilityBinding:
    from .model import (
        CapabilityAccessMode,
        CapabilityKind,
        CapabilityLifecycleState,
        SideEffectLevel,
    )

    return CapabilityBinding(
        capability_id=str(raw["capability_id"]),
        kind=CapabilityKind(str(raw["kind"])),
        provider=str(raw["provider"]),
        binding_id=None if raw.get("binding_id") is None else str(raw["binding_id"]),
        candidate_id=None if raw.get("candidate_id") is None else str(raw["candidate_id"]),
        access_mode=CapabilityAccessMode(str(raw["access_mode"])),
        state=CapabilityLifecycleState(str(raw["state"])),
        version=None if raw.get("version") is None else str(raw["version"]),
        side_effects=SideEffectLevel(int(raw["side_effects"])),
        entry_point=dict(raw.get("entry_point") or {}),
        health=float(raw.get("health") or 0.0),
        evidence_refs=[str(x) for x in raw.get("evidence_refs") or []],
    )


__all__ = [
    "CapabilityChallenger",
    "CapabilityReplacementDecision",
    "CapabilityReplacementEngine",
    "CapabilityReplacementReceipt",
    "CapabilityTrial",
    "ReplaceableCapability",
    "ReplacementStatus",
]
