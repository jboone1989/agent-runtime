"""Phase-9 meta-evolution using the same candidate/experiment/capability gates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Iterable

from ..experience.refs import stable_ref
from .capability import CapabilityState, CapabilityVersion


class MetaPolicyState(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class MetaPolicyChange:
    change_id: str
    candidate_id: str
    policy_name: str
    proposed_value: float
    previous_value: float
    created_at: datetime
    evidence_refs: tuple[str, ...]


@dataclass
class MetaPolicyVersion:
    version_id: str
    policy_name: str
    value: float
    change_id: str
    candidate_id: str
    capability_version_id: str
    activated_at: datetime
    parent_version_id: str | None
    state: MetaPolicyState = MetaPolicyState.ACTIVE
    superseded_by: str | None = None
class MetaEvolutionRegistry:
    ALLOWED_POLICIES = {
        "experiment.min_samples_per_arm": (1.0, 20.0, True),
        "consolidation.min_recurrence": (2.0, 20.0, True),
    }

    def __init__(
        self,
        *,
        changes: Iterable[MetaPolicyChange] = (),
        versions: Iterable[MetaPolicyVersion] = (),
    ) -> None:
        self.changes: list[MetaPolicyChange] = list(changes)
        self.versions: list[MetaPolicyVersion] = list(versions)

    def propose(
        self,
        *,
        candidate_id: str,
        policy_name: str,
        proposed_value: float,
        previous_value: float,
        created_at: datetime,
        evidence_refs: Iterable[str],
    ) -> MetaPolicyChange:
        name = str(policy_name).strip()
        self.validate_policy(name, proposed_value)
        self.validate_policy(name, previous_value)
        refs = _refs(evidence_refs)
        if not refs:
            raise ValueError("meta-evolution change requires evidence")
        change = MetaPolicyChange(
            change_id=stable_ref(
                "evmeta",
                candidate_id,
                name,
                proposed_value,
                previous_value,
                list(refs),
            ),
            candidate_id=candidate_id,
            policy_name=name,
            proposed_value=float(proposed_value),
            previous_value=float(previous_value),
            created_at=created_at,
            evidence_refs=refs,
        )
        existing = next(
            (row for row in self.changes if row.change_id == change.change_id),
            None,
        )
        if existing is not None:
            return existing
        self.changes.append(change)
        return change
    def activate(
        self,
        change_id: str,
        *,
        capability: CapabilityVersion,
        activated_at: datetime,
    ) -> MetaPolicyVersion:
        change = self._change(change_id)
        if capability.candidate_id != change.candidate_id:
            raise ValueError("meta capability does not belong to change candidate")
        expected_capability_id = self.capability_id(change.policy_name)
        if capability.capability_id != expected_capability_id:
            raise ValueError("meta capability id does not match policy")
        if capability.state is not CapabilityState.ACTIVE:
            raise PermissionError("meta policy activation requires active capability version")

        existing = next(
            (
                row
                for row in self.versions
                if row.change_id == change.change_id
                and row.capability_version_id == capability.version_id
            ),
            None,
        )
        if existing is not None:
            return existing
        try:
            parent = self.active(change.policy_name)
        except KeyError:
            parent = None
        version = MetaPolicyVersion(
            version_id=stable_ref(
                "evmetav",
                change.policy_name,
                change.change_id,
                capability.version_id,
            ),
            policy_name=change.policy_name,
            value=change.proposed_value,
            change_id=change.change_id,
            candidate_id=change.candidate_id,
            capability_version_id=capability.version_id,
            activated_at=activated_at,
            parent_version_id=None if parent is None else parent.version_id,
        )
        if parent is not None:
            parent.state = MetaPolicyState.SUPERSEDED
            parent.superseded_by = version.version_id
        self.versions.append(version)
        return version
    def rollback(
        self,
        policy_name: str,
        *,
        restored_capability_version_id: str,
    ) -> MetaPolicyVersion:
        current = self.active(policy_name)
        if current.parent_version_id is None:
            raise ValueError("meta policy has no previous promoted version")
        prior = next(
            (
                row
                for row in self.versions
                if row.version_id == current.parent_version_id
            ),
            None,
        )
        if prior is None:
            raise ValueError("meta rollback target is missing")
        if prior.capability_version_id != restored_capability_version_id:
            raise ValueError("meta rollback capability mismatch")
        current.state = MetaPolicyState.ROLLED_BACK
        prior.state = MetaPolicyState.ACTIVE
        prior.superseded_by = None
        return prior

    def active(self, policy_name: str) -> MetaPolicyVersion:
        rows = [
            row
            for row in self.versions
            if row.policy_name == policy_name
            and row.state is MetaPolicyState.ACTIVE
        ]
        if len(rows) != 1:
            raise KeyError(policy_name)
        return rows[0]

    def current(self, policy_name: str, default: float) -> float:
        try:
            return self.active(policy_name).value
        except KeyError:
            return float(default)

    def _change(self, change_id: str) -> MetaPolicyChange:
        item = next((row for row in self.changes if row.change_id == change_id), None)
        if item is None:
            raise KeyError(change_id)
        return item

    @classmethod
    def capability_id(cls, policy_name: str) -> str:
        return f"evolution.meta.{policy_name}"
    @classmethod
    def validate_policy(cls, policy_name: str, value: float) -> None:
        if policy_name not in cls.ALLOWED_POLICIES:
            raise PermissionError("meta-evolution policy is not allowlisted")
        lower, upper, integral = cls.ALLOWED_POLICIES[policy_name]
        number = float(value)
        if not isfinite(number) or not lower <= number <= upper:
            raise ValueError("meta-evolution policy value is outside allowed bounds")
        if integral and not number.is_integer():
            raise ValueError("meta-evolution policy requires an integer value")


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "MetaEvolutionRegistry",
    "MetaPolicyChange",
    "MetaPolicyState",
    "MetaPolicyVersion",
]
