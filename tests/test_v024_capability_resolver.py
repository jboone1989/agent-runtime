"""Agent Runtime v0.2.4 Capability Resolver — Reuse Before Create.

Resolves PlanStep.required_capabilities. Does not install anything.
Acquisition / sandbox / verification is v0.2.5.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityCandidate,
    CapabilityDecisionAction,
    CapabilityKind,
    CapabilityLifecycleState,
    CapabilityRequirement,
    SideEffectLevel,
)
from agent_runtime.capabilities.need import CapabilityNeed
from agent_runtime.capabilities.policy import CapabilityPolicy
from agent_runtime.clock import FrozenClock
from agent_runtime.goals.goal import GoalOrigin, GoalStatus
from agent_runtime.observations import Observation
from agent_runtime.planning.plan import Plan
from agent_runtime.planning.state import PlanStatus
from agent_runtime.planning.step import PlanStep
from agent_runtime.runtime import AgentRuntime


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
CLOCK = datetime(2026, 8, 22, 5, 0, tzinfo=timezone.utc)


def _healthy(**overrides: float) -> dict[str, float]:
    metrics = {
        "operational_integrity": 0.9,
        "identity_integrity": 0.95,
        "memory_integrity": 0.9,
        "compute": 0.8,
        "storage": 0.8,
        "network_connectivity": 0.9,
        "financial_resources": 0.7,
        "capability": 0.8,
        "security": 0.85,
        "social_support": 0.5,
        "goal_progress": 0.6,
        "future_survivability": 0.7,
    }
    metrics.update(overrides)
    return metrics


def _open_policy() -> CapabilityPolicy:
    return CapabilityPolicy(
        allowed_sources={"fixture"},
        allowed_kinds={
            CapabilityKind.LIBRARY.value,
            CapabilityKind.TOOL.value,
            CapabilityKind.REMOTE_AGENT.value,
            CapabilityKind.SKILL.value,
        },
    )


def _inventory() -> CapabilityInventory:
    inv = CapabilityInventory()
    inv.seed_builtin()
    return inv


def _need(capability_id: str, **kwargs) -> CapabilityNeed:
    req = CapabilityRequirement(capability_id=capability_id, **kwargs)
    return CapabilityNeed(requirement=req, evidence_refs=["step_rate"])


def test_reuse_local_beats_external_candidate() -> None:
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.fixture import FixtureCapabilitySource
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inventory()
    fixture = FixtureCapabilitySource(
        [
            CapabilityCandidate(
                candidate_id="ext_introspect",
                capability_id="introspect",
                kind=CapabilityKind.SKILL,
                provider="marketplace",
                source="fixture",
                license="MIT",
            )
        ]
    )
    resolution = CapabilityResolver(policy=_open_policy()).resolve(
        _need("introspect"),
        sources=[LocalCapabilitySource(inv), fixture],
        inventory=inv,
        plan_step_id="step_1",
        cycle=1,
    )
    assert resolution.decision.action == CapabilityDecisionAction.REUSE_LOCAL
    assert resolution.decision.plan_step_id == "step_1"
    assert resolution.decision.evidence_refs
    assert "local" in resolution.search.sources_consulted
    assert "fixture" not in resolution.search.sources_consulted
    assert inv.usable("introspect")


def test_remote_delegate_is_not_owned() -> None:
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inventory()
    inv.register(
        CapabilityBinding(
            capability_id="software.review",
            kind=CapabilityKind.REMOTE_AGENT,
            provider="agent://reviewer",
            access_mode=CapabilityAccessMode.REMOTE_ACCESS,
            state=CapabilityLifecycleState.ACTIVE,
        )
    )
    resolution = CapabilityResolver(policy=_open_policy()).resolve(
        _need("software.review"),
        sources=[LocalCapabilitySource(inv)],
        inventory=inv,
        plan_step_id="step_review",
        cycle=2,
    )
    assert resolution.decision.action == CapabilityDecisionAction.DELEGATE_REMOTE
    binding = inv.get("software.review")
    assert binding is not None
    assert binding.access_mode == CapabilityAccessMode.REMOTE_ACCESS
    assert binding.access_mode != CapabilityAccessMode.OWNED


def test_hard_fail_license_permission_and_side_effect() -> None:
    from agent_runtime.capabilities.evaluator import CapabilityEvaluator

    policy = _open_policy()
    req = CapabilityRequirement(
        capability_id="software.rate_limit",
        max_side_effect=SideEffectLevel.READ_ONLY,
        required_permissions={"net"},
        constraints={"language": "python"},
    )
    evaluator = CapabilityEvaluator()
    license_fail = evaluator.assess(
        req,
        CapabilityCandidate(
            candidate_id="gpl",
            capability_id="software.rate_limit",
            kind=CapabilityKind.LIBRARY,
            provider="pypi",
            source="fixture",
            license="GPL-3.0",
            permissions={"net"},
            side_effects=SideEffectLevel.READ_ONLY,
        ),
        policy,
    )
    perm_fail = evaluator.assess(
        req,
        CapabilityCandidate(
            candidate_id="noperf",
            capability_id="software.rate_limit",
            kind=CapabilityKind.LIBRARY,
            provider="pypi",
            source="fixture",
            license="MIT",
            permissions=set(),
            side_effects=SideEffectLevel.READ_ONLY,
        ),
        policy,
    )
    side_fail = evaluator.assess(
        req,
        CapabilityCandidate(
            candidate_id="write",
            capability_id="software.rate_limit",
            kind=CapabilityKind.LIBRARY,
            provider="pypi",
            source="fixture",
            license="MIT",
            permissions={"net"},
            side_effects=SideEffectLevel.IRREVERSIBLE,
        ),
        policy,
    )
    assert not license_fail.viable
    assert "license" in license_fail.hard_fail_reasons
    assert not perm_fail.viable
    assert "permission" in perm_fail.hard_fail_reasons
    assert not side_fail.viable
    assert "side_effect" in side_fail.hard_fail_reasons


def test_cannot_create_without_search_evidence() -> None:
    from agent_runtime.capabilities.resolver import (
        CapabilityCreateWithoutSearchError,
        CapabilityResolver,
    )

    resolver = CapabilityResolver(policy=_open_policy())
    try:
        resolver.create_without_search("software.rate_limit")
    except CapabilityCreateWithoutSearchError:
        return
    raise AssertionError("CREATE without search must be rejected")


def test_create_after_search_records_rejections() -> None:
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.fixture import FixtureCapabilitySource
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inventory()
    fixture = FixtureCapabilitySource(
        [
            CapabilityCandidate(
                candidate_id="pkg_a",
                capability_id="software.rate_limit",
                kind=CapabilityKind.LIBRARY,
                provider="pypi",
                source="fixture",
                license="GPL-3.0",
                permissions={"net"},
            )
        ]
    )
    resolution = CapabilityResolver(policy=_open_policy()).resolve(
        _need(
            "software.rate_limit",
            max_side_effect=SideEffectLevel.READ_ONLY,
            required_permissions={"net"},
        ),
        sources=[LocalCapabilitySource(inv), fixture],
        inventory=inv,
        plan_step_id="step_rate",
        cycle=4,
    )
    assert resolution.decision.action == CapabilityDecisionAction.CREATE
    assert resolution.search.sources_consulted == ["local", "fixture"]
    assert resolution.search.rejected
    assert resolution.decision.search_id == resolution.search.search_id
    assert "software.rate_limit" not in inv.entries or not inv.usable("software.rate_limit")


def test_acquire_decision_does_not_install() -> None:
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.fixture import FixtureCapabilitySource
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inventory()
    before = set(inv.entries)
    fixture = FixtureCapabilitySource(
        [
            CapabilityCandidate(
                candidate_id="pkg_ok",
                capability_id="software.rate_limit",
                kind=CapabilityKind.LIBRARY,
                provider="pypi",
                source="fixture",
                license="MIT",
                permissions={"net"},
                side_effects=SideEffectLevel.READ_ONLY,
                provenance={"maintained": True, "language": "python", "stars": 12},
            )
        ]
    )
    resolution = CapabilityResolver(policy=_open_policy()).resolve(
        _need(
            "software.rate_limit",
            max_side_effect=SideEffectLevel.READ_ONLY,
            required_permissions={"net"},
        ),
        sources=[LocalCapabilitySource(inv), fixture],
        inventory=inv,
        plan_step_id="step_rate",
        cycle=5,
    )
    assert resolution.decision.action == CapabilityDecisionAction.ACQUIRE
    assert resolution.decision.candidate_id == "pkg_ok"
    assert set(inv.entries) == before
    assert not inv.usable("software.rate_limit")
    assert not any("acquire" in name.lower() for name in dir(inv))


def test_stars_do_not_outrank_task_fit() -> None:
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.fixture import FixtureCapabilitySource
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inventory()
    fixture = FixtureCapabilitySource(
        [
            CapabilityCandidate(
                candidate_id="pkg_stars",
                capability_id="software.rate_limit",
                kind=CapabilityKind.LIBRARY,
                provider="pypi",
                source="fixture",
                license="MIT",
                permissions={"net"},
                provenance={
                    "stars": 50000,
                    "language": "ruby",
                    "maintained": False,
                },
            ),
            CapabilityCandidate(
                candidate_id="pkg_fit",
                capability_id="software.rate_limit",
                kind=CapabilityKind.LIBRARY,
                provider="pypi",
                source="fixture",
                license="MIT",
                permissions={"net"},
                provenance={
                    "stars": 1200,
                    "language": "python",
                    "maintained": True,
                    "task_fit": 0.95,
                },
            ),
        ]
    )
    resolution = CapabilityResolver(policy=_open_policy()).resolve(
        _need(
            "software.rate_limit",
            max_side_effect=SideEffectLevel.READ_ONLY,
            required_permissions={"net"},
            constraints={"language": "python", "maintained": True},
        ),
        sources=[LocalCapabilitySource(inv), fixture],
        inventory=inv,
        plan_step_id="step_rate",
        cycle=6,
    )
    assert resolution.decision.action == CapabilityDecisionAction.ACQUIRE
    assert resolution.decision.candidate_id == "pkg_fit"
    for assessment in resolution.assessments:
        assert "stars" not in assessment.dimensions


def test_resolver_failure_replans_without_retry_loop(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("need teleport")
    rt.plans = [
        Plan(
            plan_id="plan_gap",
            goal_ids=[goal.goal_id],
            steps=[
                PlanStep(
                    step_id="step_teleport",
                    objective="teleport",
                    required_capabilities=[CapabilityRequirement(capability_id="teleport")],
                    status=PlanStatus.ACTIVE,
                )
            ],
            current_step_id="step_teleport",
            status=PlanStatus.ACTIVE,
            created_cycle=rt.cycle,
        )
    ]
    obs = Observation.self_and_world(self_metrics=_healthy(), world={})
    rt.step(obs)
    assert rt.active_plan.status in {PlanStatus.BLOCKED, PlanStatus.WAITING}
    assert goal.status != GoalStatus.FAILED
    searches_after_first = len(rt.capability_searches)
    assert searches_after_first >= 1
    assert rt.capability_decisions
    assert rt.capability_decisions[-1].plan_step_id == "step_teleport"
    rt.step(obs)
    assert len(rt.capability_searches) == searches_after_first
    assert not any(a.kind == "teleport" for a in rt.records.actions)


def test_plan_step_requirements_are_official_gap_path(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("need search")
    rt.plans = [
        Plan(
            plan_id="plan_search",
            goal_ids=[goal.goal_id],
            steps=[
                PlanStep(
                    step_id="step_search",
                    objective="web.search",
                    required_capabilities=[
                        CapabilityRequirement(capability_id="web.search", intent="find sources")
                    ],
                    status=PlanStatus.ACTIVE,
                )
            ],
            current_step_id="step_search",
            status=PlanStatus.ACTIVE,
            created_cycle=0,
        )
    ]
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    gaps = [g for g in rt.capability_gaps if g.capability_id == "web.search"]
    assert gaps
    assert "step_search" in gaps[0].evidence_refs
    assert gaps[0].intent == "find sources"


def test_old_v023_snapshot_restores(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.submit_goal("legacy")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    agent_id = rt.identity.agent_id
    path = tmp_path / "agent_state.json"
    rt.persist()
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw["data"]
    for key in ("capability_decisions", "capability_searches"):
        data.pop(key, None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.identity.agent_id == agent_id
    assert restored.capability_decisions == []
    assert restored.capability_searches == []
    assert any(g.origin == GoalOrigin.USER for g in restored.goals)


def test_resolver_modules_do_not_import_virtualbrain_or_network() -> None:
    roots = [
        SRC / "capabilities" / "resolver.py",
        SRC / "capabilities" / "evaluator.py",
        SRC / "capabilities" / "search.py",
        SRC / "capabilities" / "sources",
    ]
    forbidden = ("virtualbrain", "agent_network", "agent-network")
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(root.rglob("*.py"))
    assert files, "resolver modules must exist"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    lowered = alias.name.lower()
                    assert not any(token in lowered for token in forbidden), path
            if isinstance(node, ast.ImportFrom) and node.module:
                lowered = node.module.lower()
                assert not any(token in lowered for token in forbidden), path
