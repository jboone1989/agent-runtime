"""Backend completion is not Runtime-verified success.

AgentBackend status COMPLETED only means the specialist call finished.
VerifiedLearningEvidence and success-strategy learning require an independent
Runtime verification stage.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import pytest

from agent_runtime.backends import (
    AgentArtifact,
    AgentBackendCapabilities,
    AgentEvent,
    AgentRequest,
    AgentResult,
    AgentRunMode,
    AgentUsage,
)
from agent_runtime.capabilities.model import CapabilityRequirement, SideEffectLevel
from agent_runtime.clock import FrozenClock
from agent_runtime.execution import VerificationSpec
from agent_runtime.execution.verification import CommandObservation
from agent_runtime.goals.goal import Goal, GoalOrigin
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.observations import Observation
from agent_runtime.planning.plan import Plan
from agent_runtime.planning.state import PlanStatus
from agent_runtime.planning.step import PlanStep
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


@dataclass
class ClaimingSuccessBackend:
    """Specialist that always claims success, including a foo.py artifact."""

    requests: list[AgentRequest] = field(default_factory=list)
    backend_id: str = "claiming"

    @property
    def capabilities(self) -> AgentBackendCapabilities:
        return AgentBackendCapabilities(workspace_execution=True)

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_response="Implemented successfully",
            status="COMPLETED",
            artifacts=(AgentArtifact(uri="foo.py", kind="file"),),
        )


@dataclass
class FakeWorkspace:
    artifacts: set[str] = field(default_factory=set)
    commands: dict[tuple[str, ...], CommandObservation] = field(default_factory=dict)

    def has_artifact(self, uri: str) -> bool:
        return uri in self.artifacts

    def run_command(
        self, command: Sequence[str], *, cwd: str | None = None
    ) -> CommandObservation:
        del cwd
        return self.commands.get(
            tuple(command),
            CommandObservation(exit_code=1, stdout="", stderr="command not stubbed"),
        )


@dataclass
class ReportingBackend(ClaimingSuccessBackend):
    """Specialist that streams translated events and token usage."""

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_response="Implemented successfully",
            status="COMPLETED",
            artifacts=(AgentArtifact(uri="foo.py", kind="file"),),
            events=(
                AgentEvent(
                    kind="backend.command_executed",
                    summary="Backend executed a shell command",
                    payload={"command": "pytest -q", "exit_code": 0},
                ),
            ),
            usage=AgentUsage(input_tokens=120, output_tokens=80),
        )


@dataclass
class FailedBackend(ClaimingSuccessBackend):
    """Specialist that executed work and then reported a failed turn."""

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_response="",
            status="FAILED",
            events=(
                AgentEvent(
                    kind="backend.command_executed",
                    summary="Backend executed a shell command",
                    payload={"command": "pytest -q", "exit_code": 0},
                ),
                AgentEvent(
                    kind="backend.usage.updated",
                    summary="Backend reported thread token usage",
                    payload={"total_tokens": 90},
                ),
            ),
            error="sandbox denied the write",
        )


@dataclass
class CancellableBackend(ClaimingSuccessBackend):
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    complete_immediately: bool = False
    cancellation_error: str | None = None

    @property
    def capabilities(self) -> AgentBackendCapabilities:
        return AgentBackendCapabilities(
            workspace_execution=True,
            cancellable_execution=True,
        )

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        self.started.set()
        if self.complete_immediately:
            return AgentResult(final_response="completed", status="COMPLETED")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled.set()
            if self.cancellation_error == "runtime":
                raise RuntimeError("cleanup failed")
            if self.cancellation_error == "permission":
                raise PermissionError("cleanup denied")
            raise
        raise AssertionError("cancellable fixture unexpectedly completed")


@dataclass
class RaisingBackend(ClaimingSuccessBackend):
    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        raise RuntimeError("fixture provider unavailable")


@dataclass
class ViolatingBackend(ClaimingSuccessBackend):
    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_response="done",
            status="COMPLETED",
            events=(
                AgentEvent(
                    kind="backend.side_effect_violation",
                    summary="provider attempted an unauthorized side effect",
                    payload={
                        "side_effect_violations": [
                            {"resource": "network", "description": "egress denied"}
                        ]
                    },
                ),
            ),
        )


@dataclass
class LeakyEvidenceBackend(ClaimingSuccessBackend):
    """Specialist whose event payloads carry credential-shaped content."""

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(
            final_response="Implemented successfully " + "x" * 5000,
            status="COMPLETED",
            artifacts=(AgentArtifact(uri="foo.py", kind="file"),),
            events=(
                AgentEvent(
                    kind="backend.command_executed",
                    summary="Backend executed a shell command",
                    payload={
                        "command": 'curl -H "Authorization: Bearer sk-live-abcdef123456" https://api.example.com',
                        "exit_code": 0,
                    },
                ),
                AgentEvent(
                    kind="backend.files_changed",
                    summary="Backend applied file changes",
                    payload={
                        "status": "completed",
                        "changes": [
                            {"path": f"src/file_{index}.py", "kind": "update"}
                            for index in range(300)
                        ],
                    },
                ),
                AgentEvent(
                    kind="backend.web_searched",
                    summary="Backend ran a web search",
                    payload={"query": "deploy docs", "api_key": "sk-secret-999"},
                ),
                AgentEvent(
                    kind="backend.usage.updated",
                    summary="Backend reported thread token usage",
                    payload={"total_tokens": 100},
                ),
                AgentEvent(
                    kind="backend.usage.updated",
                    summary="Backend reported thread token usage",
                    payload={"total_tokens": 250},
                ),
            ),
        )


def _autonomy_with_workspace() -> AutonomyPolicy:
    autonomy = AutonomyPolicy.default()
    autonomy.allowed_actions.add("workspace_execute")
    autonomy.allowed_resources.add("workspace")
    return autonomy


def _execution_plan(goal_id: str, *, tools: list[str] | None = None) -> Plan:
    step = PlanStep(
        step_id="stp_workspace",
        objective="workspace_execute",
        required_capabilities=[
            CapabilityRequirement(
                capability_id="workspace_execution",
                max_side_effect=SideEffectLevel.LOCAL_STATE,
                required_permissions={"workspace_write"},
            )
        ],
        payload={
            "task": "implement foo.py",
            "allowed_tools": list(tools or ["workspace_execute"]),
            "allowed_networks": ["internet"],
            "verification": {
                "command": ["pytest"],
                "expected_exit_code": 0,
                "required_artifacts": ["foo.py"],
            },
        },
        status=PlanStatus.ACTIVE,
    )
    return Plan(
        plan_id="plan_workspace",
        goal_ids=[goal_id],
        steps=[step],
        current_step_id=step.step_id,
        status=PlanStatus.ACTIVE,
        created_cycle=0,
    )


def _runtime(tmp_path, *, backend, workspace) -> AgentRuntime:
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        autonomy=_autonomy_with_workspace(),
        agent_backend=backend,
        clock=FrozenClock(NOW),
        execution_workspace=workspace,
        self_awareness_enabled=False,
        agency_threads_enabled=False,
        agency_discoveries_enabled=False,
        agency_experiments_enabled=False,
        agency_selection_enabled=False,
        selection_tracing_enabled=False,
        bounded_choice_enabled=False,
        diagnostic_coverage_enabled=False,
    )
    goal = Goal.create(
        description="Implement foo.py",
        origin=GoalOrigin.USER,
        created_at=NOW,
        reason="execution gateway test",
    )
    runtime.goals.append(goal)
    runtime.plans.append(_execution_plan(goal.goal_id))
    return runtime


def _step(runtime: AgentRuntime) -> None:
    runtime.step(
        Observation.self_and_world(
            self_metrics={"compute": 0.8},
            world={},
            timestamp=NOW,
        )
    )


def _execution_records(runtime: AgentRuntime):
    actions = [item for item in runtime.records.actions if item.kind == "workspace_execute"]
    outcomes = [
        item
        for item in runtime.records.outcomes
        if actions and item.action_id == actions[0].action_id
    ]
    return actions, outcomes


def test_backend_claimed_success_cannot_override_failed_runtime_verification(
    tmp_path,
) -> None:
    backend = ClaimingSuccessBackend()
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={
            ("pytest",): CommandObservation(
                exit_code=1, stdout="", stderr="pytest failed"
            )
        },
    )
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)

    actions, outcomes = _execution_records(runtime)
    assert backend.requests, "Runtime never invoked the EXECUTION backend"
    request = backend.requests[-1]
    assert request.mode is AgentRunMode.EXECUTION
    assert request.policy.allowed_networks == frozenset()
    assert "internet" not in request.policy.allowed_networks
    assert "shell" not in request.policy.allowed_tools

    assert actions, "ActionRecord for completed backend execution is missing"
    assert outcomes, "OutcomeRecord for Runtime verification is missing"
    action, outcome = actions[0], outcomes[0]
    assert action.kind == "workspace_execute"
    assert outcome.effects.get("backend_status") == "COMPLETED"
    assert outcome.effects.get("backend_response") == "Implemented successfully"
    assert outcome.success is False
    assert outcome.effects.get("verification_status") == "FAIL"

    success_evidence = [
        item
        for item in runtime.learning.verified_evidence
        if item.success and item.action_id == action.action_id
    ]
    assert success_evidence == []

    related = [
        item.evidence_id
        for item in runtime.learning.verified_evidence
        if item.action_id == action.action_id
    ]
    proposed = runtime.learning.propose_strategy(
        strategy_id="claimed-success",
        body="treat backend COMPLETED as a successful strategy",
        evidence_refs=related,
        successes=max(1, len(related)),
        failures=0,
    )
    assert proposed.accepted is False


def test_execution_policy_cannot_exceed_runtime_authorization(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={("pytest",): CommandObservation(exit_code=1, stderr="pytest failed")},
    )
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    runtime.plans[0].steps[0].payload["allowed_tools"] = [
        "workspace_execute",
        "shell",
        "network",
    ]
    _step(runtime)
    request = backend.requests[-1]
    assert request.policy.allowed_tools <= frozenset(runtime.autonomy.allowed_actions)
    assert "shell" not in request.policy.allowed_tools
    assert "network" not in request.policy.allowed_tools
    assert request.policy.allowed_networks == frozenset()
    assert request.policy.workspace_write is True
    assert "workspace" in request.policy.allowed_resources


def test_execution_deadline_cancels_backend_and_records_timeout(tmp_path) -> None:
    backend = CancellableBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(
            artifacts={"foo.py"},
            commands={("pytest",): CommandObservation(exit_code=0)},
        ),
    )
    runtime.plans[0].steps[0].payload["max_duration_seconds"] = 0.01

    _step(runtime)

    assert backend.cancelled.is_set()
    assert backend.requests[-1].max_duration_seconds == 0.01
    _actions, outcomes = _execution_records(runtime)
    outcome = outcomes[0]
    assert outcome.success is False
    assert outcome.effects["backend_status"] == "TIMED_OUT"
    assert outcome.effects["verification_status"] == "TIMEOUT"
    assert outcome.effects["backend_error"]
    assert not any(
        item.success and item.action_id == outcome.action_id
        for item in runtime.learning.verified_evidence
    )


def test_execution_completed_before_deadline_remains_completed(tmp_path) -> None:
    backend = CancellableBackend(complete_immediately=True)
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    runtime.plans[0].steps[0].payload["max_duration_seconds"] = 1

    _step(runtime)

    _actions, outcomes = _execution_records(runtime)
    assert backend.cancelled.is_set() is False
    assert outcomes[0].effects["backend_status"] == "COMPLETED"
    assert outcomes[0].effects["verification_status"] == "FAIL"
    assert outcomes[0].success is False


@pytest.mark.parametrize("cancellation_error", ["runtime", "permission"])
def test_timeout_remains_auditable_when_backend_cleanup_fails(
    tmp_path, cancellation_error
) -> None:
    backend = CancellableBackend(cancellation_error=cancellation_error)
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    runtime.plans[0].steps[0].payload["max_duration_seconds"] = 0.01

    _step(runtime)

    _actions, outcomes = _execution_records(runtime)
    assert outcomes[0].effects["backend_status"] == "TIMED_OUT"
    assert outcomes[0].effects["verification_status"] == "TIMEOUT"
    assert outcomes[0].effects["backend_events"][0]["payload"][
        "cancellation_cleanup_error"
    ]


def test_cancelling_gateway_await_cleans_up_backend_task(tmp_path) -> None:
    backend = CancellableBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    step = runtime.plans[0].steps[0]

    async def invoke_and_cancel() -> None:
        task = asyncio.create_task(
            runtime.execution_gateway.execute(
                plan_step=step,
                task="implement foo.py",
                required_capabilities={"workspace_execution"},
                target_resources={"workspace"},
                verification_spec=VerificationSpec.from_payload(step.payload),
                max_duration_seconds=60,
            )
        )
        await backend.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(invoke_and_cancel())
    assert backend.cancelled.is_set()


def test_deadline_is_denied_before_backend_without_cancellation_contract(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    runtime.plans[0].steps[0].payload["max_duration_seconds"] = 1

    _step(runtime)

    assert backend.requests == []
    assert not [
        item for item in runtime.records.actions if item.kind == "workspace_execute"
    ]


def test_malformed_deadline_is_denied_before_backend(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    runtime.plans[0].steps[0].payload["max_duration_seconds"] = "not-a-duration"

    _step(runtime)

    assert backend.requests == []
    assert not [
        item for item in runtime.records.actions if item.kind == "workspace_execute"
    ]


def test_compute_and_financial_limits_deny_before_backend(tmp_path) -> None:
    for field_name, value in (("compute_cost", 2.0), ("financial_cost", 2.0)):
        backend = ClaimingSuccessBackend()
        runtime = _runtime(
            tmp_path / field_name,
            backend=backend,
            workspace=FakeWorkspace(artifacts={"foo.py"}),
        )
        async def invoke() -> None:
            kwargs = {
                "plan_step": runtime.plans[0].steps[0],
                "task": "implement foo.py",
                "required_capabilities": {"workspace_execution"},
                "target_resources": {"workspace"},
                "verification_spec": VerificationSpec.from_payload(
                    runtime.plans[0].steps[0].payload
                ),
                field_name: value,
            }
            result = await runtime.execution_gateway.execute(**kwargs)
            assert result.denied

        asyncio.run(invoke())

        assert backend.requests == []


def test_unauthorized_execution_does_not_call_backend(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    workspace = FakeWorkspace(artifacts={"foo.py"})
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    runtime.autonomy.allowed_actions.discard("workspace_execute")
    _step(runtime)
    assert backend.requests == []
    actions, _outcomes = _execution_records(runtime)
    assert actions == []


def test_verified_backend_success_can_enter_learning_ledger(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={
            ("pytest",): CommandObservation(exit_code=0, stdout="1 passed", stderr="")
        },
    )
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)
    actions, outcomes = _execution_records(runtime)
    assert outcomes[0].success is True
    assert outcomes[0].effects.get("verification_status") == "PASS"
    assert any(
        item.success and item.action_id == actions[0].action_id
        for item in runtime.learning.verified_evidence
    )


def test_backend_events_and_usage_enter_outcome_records(tmp_path) -> None:
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={("pytest",): CommandObservation(exit_code=0, stdout="1 passed")},
    )
    backend = ReportingBackend()
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)

    actions, outcomes = _execution_records(runtime)
    effects = outcomes[0].effects
    assert effects["backend_usage"] == {"input_tokens": 120, "output_tokens": 80}
    assert effects["backend_events"] == [
        {
            "kind": "backend.command_executed",
            "summary": "Backend executed a shell command",
            "payload": {"command": "pytest -q", "exit_code": 0},
        }
    ]
    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW), agent_backend=None)
    restored_outcomes = [
        item
        for item in restored.records.outcomes
        if item.action_id == actions[0].action_id
    ]
    assert restored_outcomes[0].effects["backend_usage"] == {
        "input_tokens": 120,
        "output_tokens": 80,
    }
    assert restored_outcomes[0].effects["backend_events"][0]["kind"] == (
        "backend.command_executed"
    )


def test_failed_provider_turn_still_produces_auditable_outcome(tmp_path) -> None:
    workspace = FakeWorkspace(artifacts=set())
    backend = FailedBackend()
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)

    actions, outcomes = _execution_records(runtime)
    assert actions, "failed provider turn must still create an ActionRecord"
    assert outcomes, "failed provider turn must still create an OutcomeRecord"
    effects = outcomes[0].effects
    assert effects["backend_status"] == "FAILED"
    assert effects["backend_error"] == "sandbox denied the write"
    assert effects["execution_completed"] is False
    assert effects["verified_success"] is False
    # Evidence collected before the failure reaches durable records.
    assert [event["kind"] for event in effects["backend_events"]] == [
        "backend.command_executed",
        "backend.usage.updated",
    ]
    assert effects["backend_events"][0]["payload"]["command"] == "pytest -q"

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW), agent_backend=None)
    restored_outcomes = [
        item for item in restored.records.outcomes if item.action_id == actions[0].action_id
    ]
    assert restored_outcomes[0].success is False
    assert restored_outcomes[0].effects["backend_error"] == "sandbox denied the write"
    assert not any(
        item.success and item.action_id == actions[0].action_id
        for item in restored.learning.verified_evidence
    )


def test_failed_provider_cannot_be_marked_success_by_preexisting_verification(
    tmp_path,
) -> None:
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={("pytest",): CommandObservation(exit_code=0, stdout="1 passed")},
    )
    runtime = _runtime(tmp_path, backend=FailedBackend(), workspace=workspace)
    _step(runtime)

    _actions, outcomes = _execution_records(runtime)
    assert outcomes[0].success is False
    assert outcomes[0].effects["backend_status"] == "FAILED"
    assert outcomes[0].effects["verification_status"] == "PASS"
    assert outcomes[0].effects["verified_success"] is False


def test_backend_evidence_is_redacted_and_truncated_in_outcomes(tmp_path) -> None:
    from agent_runtime.execution import evidence as evidence_module

    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={("pytest",): CommandObservation(exit_code=0, stdout="1 passed")},
    )
    backend = LeakyEvidenceBackend()
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)

    _, outcomes = _execution_records(runtime)
    events = outcomes[0].effects["backend_events"]
    by_kind = {event["kind"]: event for event in events}
    command = by_kind["backend.command_executed"]["payload"]["command"]
    assert "sk-live-abcdef123456" not in command
    assert "[redacted]" in command
    assert "Authorization: Bearer" in command, "command shape stays auditable"
    search = by_kind["backend.web_searched"]["payload"]
    assert "api_key" not in search
    assert search["query"] == "deploy docs"
    response = outcomes[0].effects["backend_response"]
    assert "x" * 5000 not in response
    assert response.endswith("...[truncated]")
    # A single event with a huge inner collection is still bounded.
    changes = by_kind["backend.files_changed"]["payload"]["changes"]
    assert len(changes) == evidence_module.MAX_LIST_ITEMS + 1
    assert changes[-1] == "...[236 more items truncated]"
    serialized = str(outcomes[0].effects)
    assert "src/file_299.py" not in serialized

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW), agent_backend=None)
    restored_effects = [
        item for item in restored.records.outcomes if item.action_id
    ][-1].effects
    assert "sk-secret-999" not in str(restored_effects)


def test_usage_updates_coalesce_and_event_count_is_bounded(tmp_path) -> None:
    from agent_runtime.execution import evidence as evidence_module

    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={("pytest",): CommandObservation(exit_code=0, stdout="1 passed")},
    )
    limit = evidence_module.MAX_EVENT_COUNT

    class NoisyBackend(ClaimingSuccessBackend):
        async def run(self, request: AgentRequest) -> AgentResult:
            self.requests.append(request)
            commands = [
                AgentEvent(
                    kind="backend.command_executed",
                    summary="usage",
                    payload={"index": index},
                )
                for index in range(limit + 20)
            ]
            usage = [
                AgentEvent(
                    kind="backend.usage.updated",
                    summary="usage",
                    payload={"total_tokens": index},
                )
                for index in range(10)
            ]
            return AgentResult(
                final_response="done", status="COMPLETED", events=tuple(commands + usage)
            )

    runtime = _runtime(tmp_path, backend=NoisyBackend(), workspace=workspace)
    _step(runtime)

    _, outcomes = _execution_records(runtime)
    effects = outcomes[0].effects
    recorded = effects["backend_events"]
    # 220 commands plus one coalesced usage event overflow the cap; the bound
    # keeps the earliest side effects and the terminal usage event.
    assert len(recorded) == limit
    assert effects["events_truncated"] is True
    assert effects["events_dropped"] == 21
    usage_events = [
        event for event in recorded if event["kind"] == "backend.usage.updated"
    ]
    assert usage_events == [{"kind": "backend.usage.updated", "summary": "usage", "payload": {"total_tokens": 9}}]


def test_verification_failure_survives_restore_and_provider_loss(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    workspace = FakeWorkspace(
        artifacts={"foo.py"},
        commands={
            ("pytest",): CommandObservation(exit_code=1, stderr="pytest failed")
        },
    )
    runtime = _runtime(tmp_path, backend=backend, workspace=workspace)
    _step(runtime)
    actions, outcomes = _execution_records(runtime)
    action_id = actions[0].action_id
    agent_id = runtime.identity.agent_id
    assert outcomes[0].success is False
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW), agent_backend=None)
    restored_actions = [
        item for item in restored.records.actions if item.kind == "workspace_execute"
    ]
    restored_outcomes = [
        item for item in restored.records.outcomes if item.action_id == action_id
    ]
    assert restored.identity.agent_id == agent_id
    assert restored.agent_backend is None
    assert restored.has_execution_backend is False
    assert restored_actions[0].action_id == action_id
    assert restored_outcomes[0].success is False
    assert restored_outcomes[0].effects.get("backend_status") == "COMPLETED"
    assert restored_outcomes[0].effects.get("verification_status") == "FAIL"
    assert not any(
        item.success and item.action_id == action_id
        for item in restored.learning.verified_evidence
    )


def test_runtime_verifier_ignores_backend_status_and_response_text() -> None:
    from agent_runtime.execution import RuntimeVerifier, VerificationSpec

    verifier = RuntimeVerifier()
    result = verifier.verify(
        expected=VerificationSpec(
            command=("pytest",),
            required_artifacts=("foo.py",),
        ),
        backend_result=AgentResult(
            final_response="Implemented successfully",
            status="COMPLETED",
            artifacts=(AgentArtifact(uri="foo.py", kind="file"),),
        ),
        workspace=FakeWorkspace(
            artifacts={"foo.py"},
            commands={
                ("pytest",): CommandObservation(exit_code=1, stderr="pytest failed")
            },
        ),
    )
    assert result.verified_success is False
    assert result.status == "FAIL"
    assert "pytest failed" in result.summary


def test_inactive_capability_binding_denies_before_provider_invocation(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    from agent_runtime.capabilities.model import CapabilityLifecycleState

    runtime.capability_inventory.set_state(
        "workspace_execution", CapabilityLifecycleState.RETIRED
    )
    _step(runtime)
    assert backend.requests == []
    assert runtime.capability_inventory.usable("workspace_execution") is False


def test_degraded_capability_binding_denies_before_provider_invocation(tmp_path) -> None:
    backend = ClaimingSuccessBackend()
    runtime = _runtime(
        tmp_path,
        backend=backend,
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    from agent_runtime.capabilities.model import CapabilityLifecycleState

    runtime.capability_inventory.set_state(
        "workspace_execution", CapabilityLifecycleState.DEGRADED
    )
    _step(runtime)
    assert backend.requests == []


def test_provider_exception_becomes_failed_auditable_outcome(tmp_path) -> None:
    runtime = _runtime(
        tmp_path,
        backend=RaisingBackend(),
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    _step(runtime)
    _actions, outcomes = _execution_records(runtime)
    assert outcomes[0].success is False
    assert outcomes[0].effects["backend_status"] == "FAILED"
    assert "fixture provider unavailable" in outcomes[0].effects["backend_error"]


def test_explicit_provider_side_effect_violation_fails_verified_execution(tmp_path) -> None:
    runtime = _runtime(
        tmp_path,
        backend=ViolatingBackend(),
        workspace=FakeWorkspace(artifacts={"foo.py"}),
    )
    _step(runtime)
    _actions, outcomes = _execution_records(runtime)
    assert outcomes[0].success is False
    assert outcomes[0].effects["verification_status"] == "FAIL"
    assert outcomes[0].effects["verification_details"]["side_effect_violations"]
