"""Backend completion is not Runtime-verified success.

AgentBackend status COMPLETED only means the specialist call finished.
VerifiedLearningEvidence and success-strategy learning require an independent
Runtime verification stage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

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
