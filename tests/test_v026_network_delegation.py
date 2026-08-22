"""Agent Runtime v0.2.6 Network Capability / Delegation.

Uses a Runtime-side network port. Does not import Agent Network packages
or modify Trust Foundation.
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
    CapabilityKind,
    CapabilityLifecycleState,
    CapabilityRequirement,
    SideEffectLevel,
)
from agent_runtime.capabilities.need import CapabilityNeed
from agent_runtime.capabilities.policy import CapabilityPolicy
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
CLOCK = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)


def _inv() -> CapabilityInventory:
    inv = CapabilityInventory()
    inv.seed_builtin()
    return inv


def _policy() -> CapabilityPolicy:
    return CapabilityPolicy(
        allowed_sources={"network"},
        allowed_kinds={CapabilityKind.REMOTE_AGENT.value, CapabilityKind.LIBRARY.value},
    )


def _need(capability_id: str = "software.review") -> CapabilityNeed:
    return CapabilityNeed(
        requirement=CapabilityRequirement(
            capability_id=capability_id,
            max_side_effect=SideEffectLevel.LOCAL_STATE,
        ),
        evidence_refs=["step_review"],
    )


def test_local_and_remote_candidates_are_comparable() -> None:
    from agent_runtime.capabilities.evaluator import CapabilityEvaluator
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort
    from agent_runtime.capabilities.network.source import AgentNetworkCapabilitySource
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inv()
    inv.register(
        CapabilityBinding(
            capability_id="software.review",
            kind=CapabilityKind.LIBRARY,
            provider="operator",
            access_mode=CapabilityAccessMode.LOCAL,
            state=CapabilityLifecycleState.ACTIVE,
        )
    )
    port = FixtureNetworkPort()
    port.claims.append(
        NetworkCapabilityClaim(
            claim_id="claim_remote",
            capability_id="software.review",
            provider_agent_id="bug-review-agent",
            artifact_revision="rev-a",
            experience_counts={"success": 12, "fail": 1},
            evidence_refs=["ev_net_1"],
        )
    )
    resolver = CapabilityResolver(policy=_policy())
    resolution = resolver.resolve(
        _need(),
        sources=[LocalCapabilitySource(inv), AgentNetworkCapabilitySource(port)],
        inventory=inv,
        plan_step_id="step_review",
        cycle=1,
    )
    assert resolution.decision.action.name == "REUSE_LOCAL"
    remote = AgentNetworkCapabilitySource(port).search(_need().requirement)[0]
    local = LocalCapabilitySource(inv).search(_need().requirement)[0]
    evaluator = CapabilityEvaluator()
    local_a = evaluator.assess(_need().requirement, local, _policy())
    remote_a = evaluator.assess(_need().requirement, remote, _policy())
    assert local_a.dimensions.keys() == remote_a.dimensions.keys()
    assert "stars" not in local_a.dimensions
    assert "stars" not in remote_a.dimensions
    assert remote.provenance.get("claimed") is True


def test_missing_local_delegates_remote_without_owning() -> None:
    from agent_runtime.capabilities.model import CapabilityDecisionAction
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort
    from agent_runtime.capabilities.network.source import AgentNetworkCapabilitySource
    from agent_runtime.capabilities.resolver import CapabilityResolver
    from agent_runtime.capabilities.sources.local import LocalCapabilitySource

    inv = _inv()
    port = FixtureNetworkPort()
    port.claims.append(
        NetworkCapabilityClaim(
            claim_id="claim_reviewer",
            capability_id="software.review",
            provider_agent_id="bug-review-agent",
            artifact_revision="rev-a",
            experience_counts={"success": 4, "fail": 0},
            evidence_refs=["ev_1"],
        )
    )
    resolution = CapabilityResolver(policy=_policy()).resolve(
        _need(),
        sources=[LocalCapabilitySource(inv), AgentNetworkCapabilitySource(port)],
        inventory=inv,
        plan_step_id="step_review",
        cycle=2,
    )
    assert resolution.decision.action == CapabilityDecisionAction.DELEGATE_REMOTE
    assert resolution.decision.candidate_id == "claim_reviewer"
    binding = inv.get("software.review")
    assert binding is None or binding.access_mode != CapabilityAccessMode.OWNED


def test_network_trust_does_not_authorize() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_hot",
        capability_id="software.review",
        provider_agent_id="star-agent",
        artifact_revision="rev-a",
        experience_counts={"success": 9800, "fail": 2},
        evidence_refs=["rep_1"],
    )
    port.claims.append(claim)
    result = DelegationService(port).delegate(
        claim,
        task_payload={"diff": "x"},
        authorized=False,
        cycle=3,
    )
    assert result.success is False
    assert "authoriz" in result.reason.lower()
    assert port.submitted == []


def test_delegation_failure_falls_back_or_replans() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    port = FixtureNetworkPort()
    primary = NetworkCapabilityClaim(
        claim_id="claim_a",
        capability_id="software.review",
        provider_agent_id="agent-a",
        artifact_revision="rev-a",
        experience_counts={"success": 2, "fail": 0},
        evidence_refs=["e1"],
    )
    backup = NetworkCapabilityClaim(
        claim_id="claim_b",
        capability_id="software.review",
        provider_agent_id="agent-b",
        artifact_revision="rev-b",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["e2"],
    )
    port.claims.extend([primary, backup])
    port.fail_providers.add("agent-a")
    result = DelegationService(port).delegate(
        primary,
        task_payload={"diff": "x"},
        authorized=True,
        cycle=4,
        alternatives=[backup],
        quorum=1,
    )
    assert result.success is True
    assert result.fallback_candidate_id == "claim_b"
    port.fail_providers.add("agent-b")
    failed = DelegationService(port).delegate(
        primary,
        task_payload={"diff": "x"},
        authorized=True,
        cycle=5,
        alternatives=[backup],
        quorum=1,
    )
    assert failed.success is False
    assert failed.replan is True


def test_old_receipts_do_not_cover_new_artifact_revision() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort
    from agent_runtime.capabilities.network.models import VerificationReceipt

    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_rev",
        capability_id="software.review",
        provider_agent_id="agent-a",
        artifact_revision="rev-new",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["e"],
    )
    old = VerificationReceipt(
        receipt_id="rcpt_old",
        contract_id="ignored",
        artifact_revision="rev-old",
        accepted=True,
        source="network",
        verifier_id="v1",
        evidence_refs=["old"],
    )
    result = DelegationService(port).accept(
        claim,
        receipts=[old],
        quorum=1,
    )
    assert result.success is False


def test_quorum_shortfall_is_not_success() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort
    from agent_runtime.capabilities.network.models import VerificationReceipt

    claim = NetworkCapabilityClaim(
        claim_id="claim_q",
        capability_id="software.review",
        provider_agent_id="agent-a",
        artifact_revision="rev-a",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["e"],
    )
    receipt = VerificationReceipt(
        receipt_id="rcpt_1",
        contract_id="c1",
        artifact_revision="rev-a",
        accepted=True,
        source="network",
        verifier_id="v1",
        evidence_refs=["n1"],
    )
    result = DelegationService(FixtureNetworkPort()).accept(
        claim, receipts=[receipt], quorum=2
    )
    assert result.success is False
    assert "quorum" in result.reason.lower()


def test_irreversible_retry_reuses_idempotency_key() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_irr",
        capability_id="software.deploy",
        provider_agent_id="agent-a",
        artifact_revision="rev-a",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["e"],
        side_effects=SideEffectLevel.IRREVERSIBLE,
    )
    service = DelegationService(port)
    first = service.delegate(
        claim,
        task_payload={"release": "1"},
        authorized=True,
        cycle=8,
        irreversible=True,
        idempotency_key="deploy-1",
        quorum=1,
    )
    second = service.delegate(
        claim,
        task_payload={"release": "1"},
        authorized=True,
        cycle=9,
        irreversible=True,
        idempotency_key="deploy-1",
        quorum=1,
    )
    assert first.success and second.success
    assert first.contract is not None and second.contract is not None
    assert first.contract.contract_id == second.contract.contract_id
    assert first.contract.idempotency_key == "deploy-1"
    assert len(port.submitted) == 1


def test_private_runtime_state_is_not_published() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_priv",
        capability_id="software.review",
        provider_agent_id="agent-a",
        artifact_revision="rev-a",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["e"],
    )
    DelegationService(port).delegate(
        claim,
        task_payload={"diff": "public"},
        authorized=True,
        cycle=10,
        quorum=1,
        private={"threat": 0.91, "valence": -0.4, "relationship": "attachment"},
    )
    blob = json.dumps(port.submitted, default=str)
    assert "threat" not in blob
    assert "valence" not in blob
    assert "attachment" not in blob
    assert "public" in blob


def test_receipt_evidence_keeps_network_source() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_src",
        capability_id="software.review",
        provider_agent_id="agent-a",
        artifact_revision="rev-a",
        experience_counts={"success": 1, "fail": 0},
        evidence_refs=["ledger:1"],
    )
    result = DelegationService(port).delegate(
        claim, task_payload={"diff": "x"}, authorized=True, cycle=11, quorum=1
    )
    assert result.success
    assert result.receipts
    assert all(receipt.source == "network" for receipt in result.receipts)
    assert any("network" in ref or ref.startswith("ledger:") for ref in result.evidence_refs)


def test_successful_delegate_binds_remote_access_not_owned() -> None:
    from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
    from agent_runtime.capabilities.network.delegation import DelegationService
    from agent_runtime.capabilities.network.fixture import FixtureNetworkPort

    inv = _inv()
    port = FixtureNetworkPort()
    claim = NetworkCapabilityClaim(
        claim_id="claim_bind",
        capability_id="software.review",
        provider_agent_id="bug-review-agent",
        artifact_revision="rev-a",
        experience_counts={"success": 3, "fail": 0},
        evidence_refs=["e"],
    )
    result = DelegationService(port).delegate(
        claim,
        task_payload={"diff": "x"},
        authorized=True,
        cycle=12,
        quorum=1,
        inventory=inv,
    )
    assert result.success
    binding = inv.get("software.review")
    assert binding is not None
    assert binding.access_mode == CapabilityAccessMode.REMOTE_ACCESS
    assert binding.kind == CapabilityKind.REMOTE_AGENT
    assert "software.review" not in inv.local_ids()
    assert "software.review" in inv.remote_access_ids()


def test_runtime_runs_without_network_and_old_snapshot_restores(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.step(Observation.self_and_world(self_metrics={"compute": 0.8}, world={}))
    agent_id = rt.identity.agent_id
    path = tmp_path / "agent_state.json"
    rt.persist()
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("task_contracts", "verification_receipts"):
        raw["data"].pop(key, None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.identity.agent_id == agent_id
    assert restored.task_contracts == []
    assert restored.verification_receipts == []


def test_delegation_modules_do_not_import_virtualbrain_or_network_sdk() -> None:
    root = SRC / "capabilities" / "network"
    forbidden = ("virtualbrain", "agent_network", "agent-network")
    files = list(root.rglob("*.py")) if root.exists() else []
    assert files, "network capability package must exist"
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
