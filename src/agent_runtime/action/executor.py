"""Execute authorized actions. v0.1 handlers are local and deterministic."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..clock import new_id
from ..records import OutcomeRecord
from ..self_model.beliefs import CapabilityBelief, StateBelief
from ..self_model.model import SelfModel
from ..survival.failover import ProviderPool
from .proposal import ActionProposal

Handler = Callable[[ActionProposal, SelfModel, datetime], dict[str, Any]]


class ActionExecutor:
    def __init__(self, providers: ProviderPool | None = None) -> None:
        self.providers = providers
        self.handlers: dict[str, Handler] = {
            "noop": self._noop,
            "introspect": self._introspect,
            "diagnose": self._diagnose,
            "maintain": self._maintain,
            "record": self._record,
            "switch_provider": self._switch_provider,
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
        try:
            effects = handler(proposal, self_model, timestamp)
            success = True
            description = f"{proposal.kind} completed"
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
        )

    def _noop(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        return {"kind": "noop"}

    def _introspect(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        from ..self_model.introspection import describe_self

        return {"description": describe_self(self_model)}

    def _diagnose(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        dim = proposal.payload.get("dimension")
        belief = self_model.internal_state.get(str(dim)) if dim else None
        return {
            "dimension": dim,
            "value": None if belief is None else belief.value,
            "uncertainty": None if belief is None else belief.uncertainty,
        }

    def _maintain(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        dim = proposal.payload.get("dimension")
        if not dim or dim not in self_model.internal_state:
            return {"restored": False}
        belief = self_model.internal_state[str(dim)]
        if isinstance(belief.value, (int, float)):
            restored = min(1.0, float(belief.value) + 0.05)
            belief.value = restored
            if dim in self_model.resources:
                self_model.resources[dim].amount = restored
            return {"restored": True, "dimension": dim, "value": restored}
        return {"restored": False, "dimension": dim}

    def _record(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        return {"recorded": True, "payload": proposal.payload}

    def _switch_provider(
        self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime
    ) -> dict[str, Any]:
        if self.providers is None:
            raise ValueError("no provider pool configured")
        old, new = self.providers.switch_to_backup()
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

    def _unknown(self, proposal: ActionProposal, self_model: SelfModel, timestamp: datetime) -> dict[str, Any]:
        raise ValueError(f"no handler for action {proposal.kind}")
