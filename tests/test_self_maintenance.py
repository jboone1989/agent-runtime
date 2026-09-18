from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from agent_runtime.communication.operator.approval import ApprovalStore
from agent_runtime.self_maintenance import (
    CHANGE_DEPLOYED,
    DeploymentProfile,
    PullRequestRef,
    SelfChangePolicy,
    SelfChangeWorkflow,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    bare = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", str(bare)), check=True, capture_output=True)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "agent@example.invalid")
    _git(root, "config", "user.name", "Runtime Agent")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "initial")
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-u", "origin", "main")
    _git(root, "switch", "-c", "agent/self-fix")
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "fix: self change")
    return root, _git(root, "rev-parse", "HEAD")


class FakeForge:
    def __init__(self, head_sha: str) -> None:
        self.ref = PullRequestRef(
            number=7,
            url="https://example.invalid/pr/7",
            head_sha=head_sha,
            head_branch="agent/self-fix",
            base_branch="main",
        )
        self.merge_calls = 0

    def ensure_pull_request(self, **kwargs):
        assert kwargs["head_sha"] == self.ref.head_sha
        return self.ref

    def get_pull_request(self, number: int):
        assert number == 7
        return self.ref
    def merge_pull_request(self, number: int, *, expected_head_sha: str, method: str):
        assert number == 7
        assert expected_head_sha == self.ref.head_sha
        assert method == "squash"
        self.merge_calls += 1
        self.ref = PullRequestRef(
            number=7,
            url=self.ref.url,
            head_sha=self.ref.head_sha,
            head_branch=self.ref.head_branch,
            base_branch=self.ref.base_branch,
            merged=True,
            merge_commit_sha="merge-sha-7",
        )
        return self.ref


class FakeDeployer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def deploy(
        self,
        profile: DeploymentProfile,
        *,
        approved_head_sha: str,
        merged_sha: str,
    ):
        self.calls.append((profile.name, approved_head_sha, merged_sha))
        return {"ok": True}


def _workflow(tmp_path: Path):
    root, head = _repo(tmp_path)
    profile = DeploymentProfile(
        name="content-prod",
        command=("approved-deploy",),
        health_url="https://content.example.invalid/health",
    )
    policy = SelfChangePolicy(
        repo_root=root,
        repo_slug="owner/content-agent",
        state_dir=tmp_path / "state",
        deployment_profiles={"content-prod": profile},
    )
    approvals = ApprovalStore(tmp_path / "state")
    forge = FakeForge(head)
    deployer = FakeDeployer()
    workflow = SelfChangeWorkflow(
        policy, approval_store=approvals, forge=forge, deployer=deployer
    )
    return workflow, approvals, forge, deployer, head


def test_self_change_requires_exact_approval_then_merges_and_deploys(tmp_path):
    workflow, approvals, forge, deployer, head = _workflow(tmp_path)
    change = workflow.publish(
        agent_id="ferro",
        cycle=12,
        title="Fix my operator console",
        summary="Make the credential handoff safer.",
        deployment_profile="content-prod",
        evidence_refs=("test-suite:passed",),
    )

    pending = approvals.get(change.approval_id)
    assert pending is not None
    assert pending.status == "PENDING"
    assert pending.action_snapshot["payload"]["head_sha"] == head
    assert pending.action_snapshot["payload"]["pull_request_number"] == 7

    with pytest.raises(PermissionError, match="not been approved"):
        workflow.apply_approved(change.change_id)

    approvals.approve(
        change.approval_id,
        operator_id="human:owner",
        channel="operator-console",
    )
    applied = workflow.apply_approved(change.change_id)

    assert applied.status == CHANGE_DEPLOYED
    assert applied.merge_commit_sha == "merge-sha-7"
    assert forge.merge_calls == 1
    assert deployer.calls == [("content-prod", head, "merge-sha-7")]
    assert approvals.get(change.approval_id).status == "CONSUMED"


def test_approval_is_invalid_if_pr_head_changes_after_decision(tmp_path):
    workflow, approvals, forge, deployer, _head = _workflow(tmp_path)
    change = workflow.publish(
        agent_id="ferro",
        cycle=13,
        title="Self fix",
        summary="Change one behavior.",
        deployment_profile="content-prod",
    )
    approvals.approve(
        change.approval_id,
        operator_id="human:owner",
        channel="operator-console",
    )
    forge.ref = PullRequestRef(
        number=7,
        url=forge.ref.url,
        head_sha="different-head",
        head_branch=forge.ref.head_branch,
        base_branch=forge.ref.base_branch,
    )

    with pytest.raises(PermissionError, match="head changed"):
        workflow.apply_approved(change.change_id)
    assert forge.merge_calls == 0
    assert deployer.calls == []
def test_same_approval_contract_accepts_a_trusted_agent_authority(tmp_path):
    workflow, approvals, forge, deployer, head = _workflow(tmp_path)
    change = workflow.publish(
        agent_id="ferro",
        cycle=14,
        title="Small autonomous maintenance",
        summary="Repair a bounded local defect.",
        deployment_profile="content-prod",
    )
    decision = approvals.approve_by_authority(
        change.approval_id,
        authority_id="agent:reviewer-01",
        channel="agent-network",
    )

    assert decision.operator_id == "agent:reviewer-01"
    applied = workflow.apply_approved(change.change_id)
    assert applied.status == CHANGE_DEPLOYED
    assert deployer.calls == [("content-prod", head, "merge-sha-7")]


def test_deployment_profile_is_part_of_approved_action(tmp_path):
    workflow, approvals, _forge, deployer, _head = _workflow(tmp_path)
    change = workflow.publish(
        agent_id="ferro",
        cycle=15,
        title="Bounded change",
        summary="Keep deployment authority immutable.",
        deployment_profile="content-prod",
    )
    approvals.approve(
        change.approval_id,
        operator_id="human:owner",
        channel="operator-console",
    )
    workflow.policy.deployment_profiles["content-prod"] = DeploymentProfile(
        name="content-prod",
        command=("different-deploy",),
        health_url="https://content.example.invalid/health",
    )

    with pytest.raises(PermissionError, match="profile changed"):
        workflow.apply_approved(change.change_id)
    assert deployer.calls == []
