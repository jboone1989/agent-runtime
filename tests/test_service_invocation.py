from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.capabilities import (
    CapabilityInventory,
    CapabilityRequirement,
    ServiceVerification,
    SideEffectLevel,
    VerifiedShadowService,
)
from agent_runtime.goals.goal import Goal, GoalOrigin, GoalStatus
from agent_runtime.integrations import register_trading_provider
from agent_runtime.planning.step import PlanStep


class _Provider:
    def __init__(self):
        self.calls = []

    def manifest(self):
        return {
            "provider": "trading",
            "version": "1.0",
            "capabilities": [
                {
                    "capability_id": "crypto.edge.discover",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "READ_ONLY",
                    "legacy_aliases": [],
                },
                {
                    "capability_id": "crypto.opportunity.scan",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "LOCAL_STATE",
                    "legacy_aliases": [],
                },
                {
                    "capability_id": "crypto.trading.execute",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                    "legacy_aliases": [],
                },
            ],
        }

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, payload))
        return {"ok": True, "observed": capability_id}


def _goal() -> Goal:
    return Goal(
        goal_id="goal-1",
        description="observe the market",
        origin=GoalOrigin.USER,
        status=GoalStatus.ACTIVE,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        reason="test",
        evidence_refs=["interest-1"],
    )


def _service(tmp_path, provider=None, verifier=None):
    provider = provider or _Provider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)
    return (
        VerifiedShadowService(
            inventory=inventory,
            provider=provider,
            ledger_path=tmp_path / "service-ledger.json",
            verifier=verifier,
        ),
        provider,
    )


def test_read_only_e2e_resolves_calls_verifies_and_restores(tmp_path) -> None:
    service, provider = _service(tmp_path)
    step = PlanStep(
        step_id="step-1",
        objective="discover edges",
        required_capabilities=[CapabilityRequirement("crypto.edge.discover")],
        payload={"top": 2},
    )

    record = service.invoke(goal=_goal(), plan_step=step, cycle=7)

    assert record.success is True
    assert record.resolution_action == "REUSE_LOCAL"
    assert record.verification.verified is True
    assert provider.calls == [("crypto.edge.discover", {"top": 2})]
    restored = VerifiedShadowService(
        inventory=service.inventory,
        provider=provider,
        ledger_path=tmp_path / "service-ledger.json",
    )
    assert restored.records[0].goal_id == "goal-1"
    assert restored.records[0].verification.status == "PASS"


def test_local_state_requires_explicit_shadow(tmp_path) -> None:
    service, provider = _service(tmp_path)
    step = PlanStep(
        step_id="step-local",
        objective="scan opportunities",
        required_capabilities=[
            CapabilityRequirement(
                "crypto.opportunity.scan",
                max_side_effect=SideEffectLevel.LOCAL_STATE,
            )
        ],
    )

    blocked = service.invoke(goal=_goal(), plan_step=step, cycle=1, shadow=False)
    shadowed = service.invoke(goal=_goal(), plan_step=step, cycle=2, shadow=True)

    assert blocked.success is False
    assert blocked.invocation["reason"] == "read_gateway_side_effect_blocked"
    assert shadowed.success is True
    assert provider.calls == [("crypto.opportunity.scan", {})]


def test_external_side_effect_has_zero_provider_calls(tmp_path) -> None:
    service, provider = _service(tmp_path)
    step = PlanStep(
        step_id="step-execute",
        objective="execute opportunity",
        required_capabilities=[
            CapabilityRequirement(
                "crypto.trading.execute",
                max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
            )
        ],
    )

    record = service.invoke(goal=_goal(), plan_step=step, cycle=1, shadow=True)

    assert record.success is False
    assert record.invocation["reason"] == "read_gateway_side_effect_blocked"
    assert provider.calls == []


def test_provider_exception_and_verification_failure_are_persisted(tmp_path) -> None:
    class _FailingProvider(_Provider):
        def invoke(self, capability_id, payload):
            raise RuntimeError("down")

    def reject(_goal, _step, _invocation):
        return ServiceVerification("FAIL", False, "evidence mismatch", {"field": "ok"})

    service, _provider = _service(tmp_path, provider=_FailingProvider(), verifier=reject)
    step = PlanStep(
        step_id="step-fail",
        objective="discover edges",
        required_capabilities=[CapabilityRequirement("crypto.edge.discover")],
    )

    record = service.invoke(goal=_goal(), plan_step=step, cycle=3)
    restored = VerifiedShadowService(
        inventory=service.inventory,
        provider=_Provider(),
        ledger_path=tmp_path / "service-ledger.json",
    )

    assert record.success is False
    assert record.invocation["reason"] == "provider_error:RuntimeError"
    assert record.verification.summary == "evidence mismatch"
    assert restored.records[0].verification.evidence == {"field": "ok"}


def test_unpersistable_provider_and_verifier_values_become_bounded_failures(tmp_path) -> None:
    class _BadProvider(_Provider):
        def invoke(self, capability_id, payload):
            return {"ok": True, "bad": float("nan")}

    def bad_verifier(_goal, _step, _invocation):
        return {"status": "PASS", "verified": True}

    service, _provider = _service(tmp_path, provider=_BadProvider(), verifier=bad_verifier)
    step = PlanStep(
        step_id="step-bad",
        objective="discover edges",
        required_capabilities=[CapabilityRequirement("crypto.edge.discover")],
    )

    record = service.invoke(goal=_goal(), plan_step=step, cycle=4)
    restored = VerifiedShadowService(
        inventory=service.inventory,
        provider=_Provider(),
        ledger_path=tmp_path / "service-ledger.json",
    )

    assert record.success is False
    assert record.invocation["reason"] == "provider_result_not_persistable"
    assert record.verification.summary == "verification returned an invalid result"
    assert restored.records[0].success is False


def test_invalid_plan_step_is_persisted_as_bounded_failure(tmp_path) -> None:
    service, provider = _service(tmp_path)
    record = service.invoke(
        goal=_goal(),
        plan_step=PlanStep(step_id="step-empty", objective="nothing"),
        cycle=5,
    )

    assert record.success is False
    assert record.invocation["reason"] == "orchestration_error:ValueError"
    assert provider.calls == []


def test_inconsistent_verification_cannot_be_recorded_as_success(tmp_path) -> None:
    def inconsistent(_goal, _step, _invocation):
        return ServiceVerification("FAIL", True, "contradictory")

    service, _provider = _service(tmp_path, verifier=inconsistent)
    step = PlanStep(
        step_id="step-inconsistent",
        objective="discover edges",
        required_capabilities=[CapabilityRequirement("crypto.edge.discover")],
    )

    record = service.invoke(goal=_goal(), plan_step=step, cycle=6)

    assert record.success is False
    assert record.verification.summary == "verification result was not persistable"
