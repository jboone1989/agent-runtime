"""Approval-bound self-change lifecycle for Runtime-hosted agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Protocol
import urllib.error
import urllib.parse
import urllib.request

from .action.proposal import ActionProposal
from .communication.operator.approval import ApprovalStore, compute_action_digest
from .clock import new_id

CHANGE_PENDING_APPROVAL = "PENDING_APPROVAL"
CHANGE_MERGED = "MERGED"
CHANGE_DEPLOYED = "DEPLOYED"
CHANGE_FAILED = "FAILED"


@dataclass(frozen=True)
class DeploymentProfile:
    name: str
    command: tuple[str, ...]
    health_url: str | None = None
    timeout_seconds: float = 120.0
    @property
    def digest(self) -> str:
        payload = {
            "name": self.name,
            "command": list(self.command),
            "health_url": self.health_url,
            "timeout_seconds": self.timeout_seconds,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelfChangePolicy:
    repo_root: Path
    repo_slug: str
    state_dir: Path
    base_branch: str = "main"
    remote_name: str = "origin"
    branch_prefix: str = "agent/"
    token_env: str = "GITHUB_AUTOMATION_TOKEN"
    merge_method: str = "squash"
    deployment_profiles: Mapping[str, DeploymentProfile] | None = None

    def __post_init__(self) -> None:
        root = Path(self.repo_root).resolve()
        state = Path(self.state_dir).resolve()
        if not root.is_dir():
            raise ValueError("self-change repo_root must exist")
        if "/" not in self.repo_slug or self.repo_slug.count("/") != 1:
            raise ValueError("repo_slug must be owner/repository")
        if self.merge_method not in {"merge", "squash", "rebase"}:
            raise ValueError("unsupported merge method")
        object.__setattr__(self, "repo_root", root)
        object.__setattr__(self, "state_dir", state)
@dataclass(frozen=True)
class PullRequestRef:
    number: int
    url: str
    head_sha: str
    head_branch: str
    base_branch: str
    merged: bool = False
    merge_commit_sha: str | None = None


@dataclass
class SelfChangeRecord:
    change_id: str
    agent_id: str
    title: str
    summary: str
    branch: str
    head_sha: str
    base_branch: str
    pull_request_number: int
    pull_request_url: str
    deployment_profile: str
    deployment_profile_digest: str
    approval_id: str
    action_digest: str
    created_at: str
    status: str = CHANGE_PENDING_APPROVAL
    merged_at: str | None = None
    merge_commit_sha: str | None = None
    deployed_at: str | None = None
    last_error: str | None = None


class ForgePort(Protocol):
    def ensure_pull_request(
        self, *, branch: str, base_branch: str, title: str, body: str, head_sha: str
    ) -> PullRequestRef: ...
    def get_pull_request(self, number: int) -> PullRequestRef: ...

    def merge_pull_request(
        self, number: int, *, expected_head_sha: str, method: str
    ) -> PullRequestRef: ...


class DeploymentPort(Protocol):
    def deploy(
        self,
        profile: DeploymentProfile,
        *,
        approved_head_sha: str,
        merged_sha: str,
    ) -> Mapping[str, Any]: ...


class GitHubApiPort:
    """Minimal GitHub REST adapter. Token is read at call time and never returned."""

    def __init__(
        self, repo_slug: str, *, token_env: str = "GITHUB_AUTOMATION_TOKEN"
    ) -> None:
        self.repo_slug = repo_slug
        self.token_env = token_env
        self.api = "https://api.github.com"

    def ensure_pull_request(
        self, *, branch: str, base_branch: str, title: str, body: str, head_sha: str
    ) -> PullRequestRef:
        owner = self.repo_slug.split("/", 1)[0]
        query = urllib.parse.urlencode(
            {"state": "open", "head": f"{owner}:{branch}", "base": base_branch}
        )
        found = self._request("GET", f"/repos/{self.repo_slug}/pulls?{query}")
        if isinstance(found, list) and found:
            ref = self._ref(found[0])
            if ref.head_sha != head_sha:
                raise RuntimeError("open pull request head does not match published commit")
            return ref
        created = self._request(
            "POST",
            f"/repos/{self.repo_slug}/pulls",
            {"title": title, "head": branch, "base": base_branch, "body": body},
        )
        ref = self._ref(created)
        if ref.head_sha != head_sha:
            raise RuntimeError("created pull request head does not match published commit")
        return ref

    def get_pull_request(self, number: int) -> PullRequestRef:
        return self._ref(
            self._request("GET", f"/repos/{self.repo_slug}/pulls/{int(number)}")
        )

    def merge_pull_request(
        self, number: int, *, expected_head_sha: str, method: str
    ) -> PullRequestRef:
        current = self.get_pull_request(number)
        if current.head_sha != expected_head_sha:
            raise PermissionError("pull request head changed after approval")
        if current.merged:
            return current
        self._request(
            "PUT",
            f"/repos/{self.repo_slug}/pulls/{int(number)}/merge",
            {"sha": expected_head_sha, "merge_method": method},
        )
        merged = self.get_pull_request(number)
        if not merged.merged:
            raise RuntimeError("forge did not report pull request as merged")
        return merged
    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Any:
        token = str(os.environ.get(self.token_env) or "").strip()
        if not token:
            raise RuntimeError(f"{self.token_env} is not configured")
        data = None if body is None else json.dumps(dict(body)).encode("utf-8")
        request = urllib.request.Request(
            self.api + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"github api error {exc.code}") from exc
        except OSError as exc:
            raise RuntimeError("github api request failed") from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    @staticmethod
    def _ref(value: Mapping[str, Any]) -> PullRequestRef:
        head = value.get("head") if isinstance(value.get("head"), Mapping) else {}
        base = value.get("base") if isinstance(value.get("base"), Mapping) else {}
        return PullRequestRef(
            number=int(value["number"]),
            url=str(value.get("html_url") or ""),
            head_sha=str(head.get("sha") or ""),
            head_branch=str(head.get("ref") or ""),
            base_branch=str(base.get("ref") or ""),
            merged=bool(value.get("merged") or value.get("merged_at")),
            merge_commit_sha=(
                None
                if value.get("merge_commit_sha") is None
                else str(value.get("merge_commit_sha"))
            ),
        )


class CommandDeploymentPort:
    """Runs only deployment commands already frozen in a DeploymentProfile."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self.cwd = None if cwd is None else Path(cwd)

    def deploy(
        self,
        profile: DeploymentProfile,
        *,
        approved_head_sha: str,
        merged_sha: str,
    ) -> Mapping[str, Any]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                word in key.upper()
                for word in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY")
            )
        }
        env["AGENT_APPROVED_HEAD_SHA"] = approved_head_sha
        env["AGENT_MERGED_SHA"] = merged_sha
        completed = subprocess.run(
            profile.command,
            cwd=self.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=profile.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"deployment command failed with exit {completed.returncode}"
            )
        if profile.health_url:
            request = urllib.request.Request(profile.health_url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=20.0) as response:
                    status = int(getattr(response, "status", 200))
            except OSError as exc:
                raise RuntimeError("post-deploy health check failed") from exc
            if not 200 <= status < 300:
                raise RuntimeError(f"post-deploy health check returned {status}")
        return {
            "ok": True,
            "approved_head_sha": approved_head_sha,
            "merged_sha": merged_sha,
            "health_url": profile.health_url,
        }


class SelfChangeWorkflow:
    def __init__(
        self,
        policy: SelfChangePolicy,
        *,
        approval_store: ApprovalStore,
        forge: ForgePort | None = None,
        deployer: DeploymentPort | None = None,
    ) -> None:
        self.policy = policy
        self.approvals = approval_store
        self.forge = forge or GitHubApiPort(
            policy.repo_slug, token_env=policy.token_env
        )
        self.deployer = deployer or CommandDeploymentPort(cwd=policy.repo_root)
        self.root = policy.state_dir / "self_maintenance" / "changes"
        self.root.mkdir(parents=True, exist_ok=True)
    def publish(
        self,
        *,
        agent_id: str,
        cycle: int,
        title: str,
        summary: str,
        deployment_profile: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> SelfChangeRecord:
        branch = self._git("branch", "--show-current").stdout.strip()
        if not branch.startswith(self.policy.branch_prefix):
            raise PermissionError(
                "self-change publication requires an isolated agent task branch"
            )
        if self._git("status", "--porcelain").stdout.strip():
            raise ValueError(
                "self-change publication requires a clean working tree"
            )
        head_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._git(
            "push", "--set-upstream", self.policy.remote_name, branch
        )
        body = (
            summary.strip()
            + "\n\nAutomated self-maintenance proposal. "
            + "The approved commit SHA must remain unchanged."
        )
        pr = self.forge.ensure_pull_request(
            branch=branch,
            base_branch=self.policy.base_branch,
            title=title.strip(),
            body=body,
            head_sha=head_sha,
        )
        profile = self._profile(deployment_profile)
        proposal = self._proposal(
            pr=pr,
            profile=profile,
            agent_id=agent_id,
            summary=summary,
            evidence_refs=evidence_refs,
        )
        approval = self.approvals.create_pending(
            agent_id=agent_id,
            cycle=cycle,
            proposal=proposal,
            summary=f"Approve self-change PR #{pr.number}: {title.strip()}",
        )
        record = SelfChangeRecord(
            change_id=new_id("chg"),
            agent_id=agent_id,
            title=title.strip(),
            summary=summary.strip(),
            branch=branch,
            head_sha=head_sha,
            base_branch=self.policy.base_branch,
            pull_request_number=pr.number,
            pull_request_url=pr.url,
            deployment_profile=profile.name,
            deployment_profile_digest=profile.digest,
            approval_id=approval.approval_id,
            action_digest=compute_action_digest(proposal),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(record)
        return record

    def apply_approved(self, change_id: str) -> SelfChangeRecord:
        record = self._require(change_id)
        approval = self.approvals.get(record.approval_id)
        if approval is None or approval.status != "APPROVED":
            raise PermissionError("self-change has not been approved")
        profile = self._profile(record.deployment_profile)
        if profile.digest != record.deployment_profile_digest:
            raise PermissionError(
                "deployment profile changed after approval request"
            )
        current = self.forge.get_pull_request(record.pull_request_number)
        if current.head_sha != record.head_sha:
            raise PermissionError(
                "pull request head changed after approval request"
            )
        proposal = self._proposal(
            pr=current,
            profile=profile,
            agent_id=record.agent_id,
            summary=record.summary,
            evidence_refs=tuple(approval.evidence_refs),
        )
        digest = compute_action_digest(proposal)
        if (
            digest != record.action_digest
            or approval.action_digest != record.action_digest
        ):
            raise PermissionError(
                "approval is not bound to this exact self-change"
            )
        try:
            merged = self.forge.merge_pull_request(
                record.pull_request_number,
                expected_head_sha=record.head_sha,
                method=self.policy.merge_method,
            )
            record.status = CHANGE_MERGED
            record.merged_at = datetime.now(timezone.utc).isoformat()
            record.merge_commit_sha = merged.merge_commit_sha
            self._write(record)

            if not merged.merge_commit_sha:
                raise RuntimeError("forge did not provide merged main commit sha")
            self.deployer.deploy(
                profile,
                approved_head_sha=record.head_sha,
                merged_sha=merged.merge_commit_sha,
            )
            record.status = CHANGE_DEPLOYED
            record.deployed_at = datetime.now(timezone.utc).isoformat()
            record.last_error = None
            self.approvals.consume(record.approval_id)
            self._write(record)
            return record
        except Exception as exc:
            record.status = CHANGE_FAILED
            record.last_error = f"{type(exc).__name__}: {exc}"
            self._write(record)
            raise
    def get(self, change_id: str) -> SelfChangeRecord | None:
        path = self.root / f"{change_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return SelfChangeRecord(**value)

    def _proposal(
        self,
        *,
        pr: PullRequestRef,
        profile: DeploymentProfile,
        agent_id: str,
        summary: str,
        evidence_refs: tuple[str, ...],
    ) -> ActionProposal:
        return ActionProposal(
            kind="self_change_apply",
            resource=f"repository:{self.policy.repo_slug}",
            compute_cost=0.02,
            financial_cost=0.0,
            goal_id=None,
            reason=summary.strip() or "apply approved self change",
            trigger="INTERNAL",
            goal_origin="INTERNAL",
            evidence=list(evidence_refs),
            payload={
                "repo_slug": self.policy.repo_slug,
                "pull_request_number": pr.number,
                "pull_request_url": pr.url,
                "head_sha": pr.head_sha,
                "base_branch": pr.base_branch,
                "deployment_profile": profile.name,
                "deployment_profile_digest": profile.digest,
                "agent_id": agent_id,
            },
        )

    def _profile(self, name: str) -> DeploymentProfile:
        profiles = dict(self.policy.deployment_profiles or {})
        try:
            return profiles[name]
        except KeyError as exc:
            raise ValueError("unknown deployment profile") from exc
    def _require(self, change_id: str) -> SelfChangeRecord:
        record = self.get(change_id)
        if record is None:
            raise KeyError(change_id)
        return record

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                word in key.upper()
                for word in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY")
            )
        }
        result = subprocess.run(
            ("git", *args),
            cwd=self.policy.repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("git self-maintenance command failed")
        return result

    def _write(self, record: SelfChangeRecord) -> None:
        path = self.root / f"{record.change_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                asdict(record), sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)


__all__ = [
    "DeploymentProfile",
    "SelfChangePolicy",
    "SelfChangeRecord",
    "SelfChangeWorkflow",
    "PullRequestRef",
    "ForgePort",
    "GitHubApiPort",
    "DeploymentPort",
    "CommandDeploymentPort",
]
