"""Execute authorized actions. v0.1 handlers are local and deterministic."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..clock import new_id
from ..records import OutcomeRecord
from ..self_model.beliefs import CapabilityBelief, StateBelief
from ..self_model.model import SelfModel
from ..survival.failover import ProviderPool
from .evidence import classify_diagnostic_evidence
from .proposal import ActionProposal

Handler = Callable[[ActionProposal, SelfModel, datetime], dict[str, Any]]


class ActionExecutor:
    def __init__(self, providers: ProviderPool | None = None) -> None:
        self.providers = providers
        self.environment = None
        self.world = None
        self.goals = None
        self.regulatory_state = None
        self.handlers: dict[str, Handler] = {
            "noop": self._noop,
            "introspect": self._introspect,
            "diagnose": self._diagnose,
            "maintain": self._maintain,
            "record": self._record,
            "switch_provider": self._switch_provider,
            "probe_provider": self._probe_provider,
            "conserve": self._conserve,
            "contain": self._contain,
            "restore_config": self._restore_config,
            "disable_capability": self._disable_capability,
        }

    def execute(
        self,
        proposal: ActionProposal,
        self_model: SelfModel,
        *,
        cycle: int,
        timestamp: datetime,
    ) -> OutcomeRecord:
        handler = self.handlers.get(proposal.kind, self._unknown)
        evidence_result = None
        missing_evidence_units: list[str] = []
        try:
            effects = handler(proposal, self_model, timestamp)
            success = True
            description = f"{proposal.kind} completed"
            if proposal.kind == "diagnose":
                result, missing_evidence_units = classify_diagnostic_evidence(
                    effects
                )
                evidence_result = result.value
        except Exception as exc:  # noqa: BLE001 — record failure as outcome
            effects = {"error": str(exc)}
            success = False
            description = f"{proposal.kind} failed: {exc}"
        return OutcomeRecord(
            outcome_id=new_id("out"),
            action_id=proposal.action_id,
            cycle=cycle,
            timestamp=timestamp,
            success=success,
            description=description,
            effects=effects,
            evidence_result=evidence_result,
            missing_evidence_units=missing_evidence_units,
        )

    def _noop(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        return {"kind": "noop"}

    def _introspect(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        from ..self_model.introspection import describe_self

        return {"description": describe_self(self_model)}

    def _diagnose(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        dim = proposal.payload.get("dimension")
        belief = self_model.internal_state.get(str(dim)) if dim else None
        result = {
            "dimension": dim,
            "target_path": None if not dim else f"internal_state.{dim}",
            "value": None if belief is None else belief.value,
            "uncertainty": None if belief is None else belief.uncertainty,
            "evidence_refs": [] if belief is None else list(belief.evidence_refs),
            "observed_at": (
                None
                if belief is None or belief.last_updated is None
                else belief.last_updated.isoformat()
            ),
        }
        guard_id = str(
            proposal.payload.get("prediction_failure_guard_id") or ""
        )
        if guard_id:
            anomaly_refs = [
                str(ref)
                for ref in proposal.payload.get("anomaly_evidence_refs", [])
                if str(ref)
            ]
            belief_refs = [] if belief is None else list(belief.evidence_refs)
            expected_range = proposal.payload.get("expected_range")
            value = None if belief is None else belief.value
            reproduced = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isinstance(expected_range, (list, tuple))
                and len(expected_range) == 2
                and float(expected_range[0])
                <= float(value)
                <= float(expected_range[1])
            )
            result.update(
                {
                    "prediction_failure_guard_id": guard_id,
                    "diagnostic_explanation": (
                        "ANOMALY_REPRODUCED"
                        if reproduced
                        else "ANOMALY_NOT_REPRODUCED"
                    ),
                    "diagnostic_explanation_complete": belief is not None,
                    "explanation_evidence_refs": list(
                        dict.fromkeys(
                            [*anomaly_refs[-8:], *belief_refs[-16:]]
                        )
                    ),
                }
            )
        return result

    def _maintain(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        dim = proposal.payload.get("dimension")
        if not dim or dim not in self_model.internal_state:
            raise ValueError("maintain requires a known state dimension")
        belief = self_model.internal_state[str(dim)]
        if isinstance(belief.value, (int, float)):
            restored = min(1.0, float(belief.value) + 0.05)
            belief.value = restored
            if dim in self_model.resources:
                self_model.resources[dim].amount = restored
            return {"restored": True, "dimension": dim, "value": restored}
        raise ValueError(f"state dimension is not maintainable: {dim}")

    def _record(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        return {"recorded": True, "payload": proposal.payload}

    def _switch_provider(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        if self.providers is None:
            raise ValueError("no provider pool configured")
        target = proposal.payload.get("target")
        if target is None:
            old, new = self.providers.switch_to_backup()
        else:
            old, new = self.providers.switch_to(str(target))
        if self.environment is not None:
            self.environment.apply("active_provider", new)
        existing = self_model.capabilities.get("llm_provider")
        self_model.capabilities["llm_provider"] = CapabilityBelief(
            name="llm_provider",
            available=True,
            proficiency=existing.proficiency if existing else 0.7,
            uncertainty=0.15,
            evidence_refs=[],
        )
        self_model.internal_state["active_provider"] = StateBelief(
            name="active_provider",
            value=new,
            trend=0.0,
            uncertainty=0.05,
            last_updated=timestamp,
        )
        self_model.uncertainty["capabilities.llm_provider"] = 0.15
        return {"previous": old, "active": new}

    def _probe_provider(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        if self.environment is not None:
            name = str(proposal.payload.get("target") or (self.providers.active if self.providers else ""))
            stats = self.environment.read(f"providers/{name}") or {}
            err = float(stats.get("error_rate", 1.0)) if isinstance(stats, dict) else 1.0
            return {"target": name, "error_rate": err, "ok": err < 0.5}
        if self.providers is None or self.world is None:
            return {"ok": False}
        name = str(proposal.payload.get("target") or self.providers.active)
        stats = self.world.providers.get(name) or {}
        err = float(stats.get("error_rate", 1.0))
        return {"target": name, "error_rate": err, "ok": err < 0.5}

    def _conserve(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        if self.regulatory_state is not None:
            self.regulatory_state.resource_conservation = max(
                float(self.regulatory_state.resource_conservation), 0.7
            )
        blocked = 0
        if self.goals is not None:
            from ..goals.goal import GoalOrigin, GoalStatus

            for goal in self.goals:
                if goal.origin == GoalOrigin.USER and goal.status == GoalStatus.ACTIVE:
                    goal.status = GoalStatus.BLOCKED
                    goal.priority = min(goal.priority, 0.2)
                    blocked += 1
        return {"conservation": True, "blocked_goals": blocked}

    def _contain(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        return {"mode": proposal.payload.get("mode", "CONTAINED"), "attack": False}

    def _restore_config(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        if self.environment is not None:
            blocked = self.environment.read("security/restore_blocked")
            if blocked:
                raise RuntimeError("restore blocked")
            self.environment.apply("security/config_mutated", False)
            self.environment.apply("security/credentials_valid", True)
            self.environment.apply("security/signature_ok", True)
            return {"restored": True}
        if self.world is not None:
            self.world.security["config_mutated"] = False
            self.world.security["credentials_valid"] = True
            self.world.security["signature_ok"] = True
        return {"restored": True}

    def _disable_capability(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        name = str(proposal.payload.get("capability") or "llm_provider")
        existing = self_model.capabilities.get(name)
        self_model.capabilities[name] = CapabilityBelief(
            name=name,
            available=False,
            proficiency=existing.proficiency if existing else 0.5,
            uncertainty=0.2,
            evidence_refs=[],
        )
        return {"capability": name, "available": False}

    def _unknown(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        raise ValueError(f"no handler for action {proposal.kind}")
