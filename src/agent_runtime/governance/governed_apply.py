"""Crash-safe generic governed-apply protocol (GAI v1).

The Runtime owns authorization/admission/audit. Domain executors still own the
actual target write, authoritative read-back, compensation, and recovery.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_RE = r"^sha256:[0-9a-f]{64}$"
MAX_METADATA_BYTES = 32_768
UNRESOLVED_STATUSES = {"STARTED", "UNKNOWN", "RECOVERY_REQUIRED"}


class GovernedApplyError(RuntimeError):
    """Base error for governed-apply protocol failures."""


class GovernanceRejected(GovernedApplyError):
    """Authorization, policy, epoch, or baseline rejected an operation."""


class GovernanceConflict(GovernedApplyError):
    """A conflicting immutable record or unresolved attempt exists."""


class GovernanceStateError(GovernedApplyError):
    """Persisted governance state is corrupt or internally inconsistent."""


def _bounded(value: str, *, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return value


def _aware(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: str
    digest: str = Field(pattern=SHA256_RE)
    media_type: str

    @field_validator("locator", "media_type")
    @classmethod
    def _validate_text(cls, value: str, info):
        return _bounded(value, name=info.field_name, limit=1000)


class AttemptStatus(str, Enum):
    STARTED = "STARTED"
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class ReceiptOutcome(str, Enum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class ObjectBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.object-binding"] = "gai.v1.object-binding"
    object_id: str
    authority_id: str
    authority_epoch: int = Field(ge=0, strict=True)
    executor_id: str
    revision: str
    confirmed_state_ref: ArtifactRef

    @field_validator("object_id", "authority_id", "executor_id", "revision")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)


class ChangeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.change-candidate"] = "gai.v1.change-candidate"
    candidate_id: str
    object_id: str
    expected_revision: str
    change_artifact_ref: ArtifactRef
    submitted_principal: str
    submitted_at: datetime
    source_metadata: dict[str, Any] | None = None

    @field_validator("candidate_id", "object_id", "expected_revision", "submitted_principal")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("submitted_at")
    @classmethod
    def _validate_time(cls, value: datetime):
        return _aware(value, name="submitted_at")

    @field_validator("source_metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any] | None):
        if value is None:
            return None
        try:
            encoded = json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("source_metadata must be bounded JSON") from exc
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError("source_metadata exceeds byte budget")
        return value

    def digest(self) -> str:
        return _canonical_digest(self.model_dump(mode="json"))


class EvaluationAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.evaluation-attestation"] = "gai.v1.evaluation-attestation"
    report_id: str
    candidate_digest: str = Field(pattern=SHA256_RE)
    evaluation_profile_ref: str
    evaluator_principal: str
    report_artifact_ref: ArtifactRef
    completed_at: datetime

    @field_validator("report_id", "evaluation_profile_ref", "evaluator_principal")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("completed_at")
    @classmethod
    def _validate_time(cls, value: datetime):
        return _aware(value, name="completed_at")


class AdoptionGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.adoption-grant"] = "gai.v1.adoption-grant"
    grant_id: str
    object_id: str
    authority_id: str
    authority_epoch: int = Field(ge=0, strict=True)
    candidate_digest: str = Field(pattern=SHA256_RE)
    expected_revision: str
    governance_policy_ref: str
    evaluation_refs: tuple[str, ...] = Field(default=(), max_length=128)
    issued_principal: str
    issued_at: datetime
    execute_before: datetime

    @field_validator(
        "grant_id", "object_id", "authority_id", "expected_revision",
        "governance_policy_ref", "issued_principal",
    )
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("issued_at", "execute_before")
    @classmethod
    def _validate_time(cls, value: datetime, info):
        return _aware(value, name=info.field_name)

    @field_validator("evaluation_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]):
        if len(value) != len(set(value)):
            raise ValueError("evaluation_refs must be unique")
        return tuple(_bounded(item, name="evaluation_ref") for item in value)

    @model_validator(mode="after")
    def _validate_window(self):
        if self.execute_before <= self.issued_at:
            raise ValueError("execute_before must be after issued_at")
        return self


class ApplyAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.apply-attempt"] = "gai.v1.apply-attempt"
    attempt_id: str
    grant_id: str
    started_at: datetime
    status: AttemptStatus

    @field_validator("attempt_id", "grant_id")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("started_at")
    @classmethod
    def _validate_time(cls, value: datetime):
        return _aware(value, name="started_at")


class ApplyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.apply-receipt"] = "gai.v1.apply-receipt"
    receipt_id: str
    attempt_id: str
    receipt_sequence: int = Field(ge=1, strict=True)
    executor_id: str
    outcome: ReceiptOutcome
    observed_revision: str | None = None
    observed_state_ref: ArtifactRef | None = None
    observed_at: datetime
    details_ref: ArtifactRef | None = None

    @field_validator("receipt_id", "attempt_id", "executor_id")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("observed_revision")
    @classmethod
    def _validate_optional_revision(cls, value: str | None):
        return None if value is None else _bounded(value, name="observed_revision")

    @field_validator("observed_at")
    @classmethod
    def _validate_time(cls, value: datetime):
        return _aware(value, name="observed_at")

    @model_validator(mode="after")
    def _validate_authoritative_outcome(self):
        if self.outcome in {ReceiptOutcome.APPLIED, ReceiptOutcome.NOT_APPLIED}:
            if self.observed_revision is None or self.observed_state_ref is None:
                raise ValueError(
                    "APPLIED/NOT_APPLIED require authoritative revision and state reference"
                )
        return self


class GrantRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["gai.v1.grant-revocation"] = "gai.v1.grant-revocation"
    grant_id: str
    revoked_principal: str
    revoked_at: datetime
    reason_ref: ArtifactRef | None = None

    @field_validator("grant_id", "revoked_principal")
    @classmethod
    def _validate_id(cls, value: str, info):
        return _bounded(value, name=info.field_name)

    @field_validator("revoked_at")
    @classmethod
    def _validate_time(cls, value: datetime):
        return _aware(value, name="revoked_at")


class _LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["gai.v1.ledger"] = "gai.v1.ledger"
    ledger_revision: int = Field(default=0, ge=0, strict=True)
    bindings: dict[str, ObjectBinding] = Field(default_factory=dict)
    candidates: dict[str, ChangeCandidate] = Field(default_factory=dict)
    evaluations: dict[str, EvaluationAttestation] = Field(default_factory=dict)
    grants: dict[str, AdoptionGrant] = Field(default_factory=dict)
    revocations: dict[str, GrantRevocation] = Field(default_factory=dict)
    attempts: dict[str, ApplyAttempt] = Field(default_factory=dict)
    grant_attempts: dict[str, str] = Field(default_factory=dict)
    receipts: dict[str, list[ApplyReceipt]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_internal_indexes(self):
        for object_id, binding in self.bindings.items():
            if object_id != binding.object_id:
                raise ValueError("binding key/object_id mismatch")
        for candidate_id, candidate in self.candidates.items():
            if candidate_id != candidate.candidate_id:
                raise ValueError("candidate key/candidate_id mismatch")
        candidate_digests = {candidate.digest(): candidate for candidate in self.candidates.values()}
        for report_id, report in self.evaluations.items():
            if report_id != report.report_id:
                raise ValueError("evaluation key/report_id mismatch")
            if report.candidate_digest not in candidate_digests:
                raise ValueError("evaluation references missing candidate")
        for grant_id, grant in self.grants.items():
            if grant_id != grant.grant_id:
                raise ValueError("grant key/grant_id mismatch")
            candidate = candidate_digests.get(grant.candidate_digest)
            if candidate is None:
                raise ValueError("grant references missing candidate")
            if candidate.object_id != grant.object_id or candidate.expected_revision != grant.expected_revision:
                raise ValueError("grant/candidate binding mismatch")
            for report_id in grant.evaluation_refs:
                report = self.evaluations.get(report_id)
                if report is None or report.candidate_digest != grant.candidate_digest:
                    raise ValueError("grant/evaluation binding mismatch")
        for grant_id, revocation in self.revocations.items():
            if grant_id != revocation.grant_id or grant_id not in self.grants:
                raise ValueError("revocation/grant index mismatch")
        seen_receipts: set[str] = set()
        for attempt_id, attempt in self.attempts.items():
            if attempt_id != attempt.attempt_id:
                raise ValueError("attempt key/attempt_id mismatch")
            if attempt.grant_id not in self.grants:
                raise ValueError("attempt references missing grant")
            if self.grant_attempts.get(attempt.grant_id) != attempt_id:
                raise ValueError("attempt/grant index mismatch")
            rows = sorted(self.receipts.get(attempt_id, []), key=lambda row: row.receipt_sequence)
            if rows and len({row.executor_id for row in rows}) != 1:
                raise ValueError("attempt receipts disagree on executor")
            for expected, receipt in enumerate(rows, start=1):
                if receipt.attempt_id != attempt_id or receipt.receipt_sequence != expected:
                    raise ValueError("receipt attempt/sequence mismatch")
                if receipt.receipt_id in seen_receipts:
                    raise ValueError("duplicate receipt_id")
                seen_receipts.add(receipt.receipt_id)
            if rows and attempt.status.value != rows[-1].outcome.value:
                raise ValueError("attempt status/latest receipt mismatch")
            if not rows and attempt.status is not AttemptStatus.STARTED:
                raise ValueError("resolved attempt requires authoritative receipt")
        for grant_id, attempt_id in self.grant_attempts.items():
            if grant_id not in self.grants or attempt_id not in self.attempts:
                raise ValueError("grant_attempts contains dangling reference")
            if self.attempts[attempt_id].grant_id != grant_id:
                raise ValueError("grant_attempts points to attempt owned by another grant")
        for attempt_id in self.receipts:
            if attempt_id not in self.attempts:
                raise ValueError("receipts contain unknown attempt")
        return self


GovernancePolicyCheck = Callable[[AdoptionGrant], bool]


@contextmanager
def _process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":  # pragma: no cover - Windows CI exercises this branch separately.
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GovernedApplyLedger:
    """Single-file durable ledger with process-safe atomic mutation."""

    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path).expanduser().resolve()
        self.lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        self._thread_lock = threading.RLock()

    def _load(self) -> _LedgerState:
        if not self.state_path.exists():
            return _LedgerState()
        try:
            return _LedgerState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            raise GovernanceStateError("invalid governed-apply ledger state") from exc

    def _save(self, state: _LedgerState) -> None:
        payload = state.model_dump_json(indent=2) + "\n"
        _atomic_write(self.state_path, payload)

    def _mutate(self, operation):
        with self._thread_lock:
            with _process_lock(self.lock_path):
                state = self._load()
                result, changed = operation(state)
                if changed:
                    state.ledger_revision += 1
                    self._save(state)
                return result

    def snapshot(self) -> dict[str, Any]:
        with self._thread_lock:
            with _process_lock(self.lock_path):
                return self._load().model_dump(mode="json")

    def binding(self, object_id: str) -> ObjectBinding | None:
        object_id = _bounded(object_id, name="object_id")
        with self._thread_lock:
            with _process_lock(self.lock_path):
                return self._load().bindings.get(object_id)

    def attempt(self, attempt_id: str) -> ApplyAttempt | None:
        attempt_id = _bounded(attempt_id, name="attempt_id")
        with self._thread_lock:
            with _process_lock(self.lock_path):
                return self._load().attempts.get(attempt_id)

    def receipts_for(self, attempt_id: str) -> tuple[ApplyReceipt, ...]:
        attempt_id = _bounded(attempt_id, name="attempt_id")
        with self._thread_lock:
            with _process_lock(self.lock_path):
                receipts = self._load().receipts.get(attempt_id, [])
                return tuple(sorted(receipts, key=lambda row: row.receipt_sequence))

    def record_binding(self, binding: ObjectBinding) -> ObjectBinding:
        binding = ObjectBinding.model_validate(binding)

        def operation(state: _LedgerState):
            current = state.bindings.get(binding.object_id)
            if current == binding:
                return current, False
            if current is not None:
                if self._has_unresolved_for_object(state, binding.object_id):
                    raise GovernanceConflict(
                        "cannot change governed binding while an attempt is unresolved"
                    )
                if binding.authority_epoch < current.authority_epoch:
                    raise GovernanceRejected("authority epoch cannot move backwards")
                if (
                    binding.authority_id != current.authority_id
                    and binding.authority_epoch <= current.authority_epoch
                ):
                    raise GovernanceRejected(
                        "authority transfer requires a strictly newer authority epoch"
                    )
            state.bindings[binding.object_id] = binding
            return binding, True

        return self._mutate(operation)

    def record_candidate(self, candidate: ChangeCandidate) -> str:
        candidate = ChangeCandidate.model_validate(candidate)
        candidate_digest = candidate.digest()

        def operation(state: _LedgerState):
            if candidate.object_id not in state.bindings:
                raise GovernanceRejected("candidate object has no governed binding")
            existing = state.candidates.get(candidate.candidate_id)
            if existing is not None:
                if existing != candidate:
                    raise GovernanceConflict("candidate_id already has different immutable payload")
                return existing.digest(), False
            state.candidates[candidate.candidate_id] = candidate
            return candidate_digest, True

        return self._mutate(operation)

    def record_evaluation(self, report: EvaluationAttestation) -> EvaluationAttestation:
        report = EvaluationAttestation.model_validate(report)

        def operation(state: _LedgerState):
            if report.candidate_digest not in self._candidate_digests(state):
                raise GovernanceRejected("evaluation references unknown candidate digest")
            existing = state.evaluations.get(report.report_id)
            if existing is not None:
                if existing != report:
                    raise GovernanceConflict("report_id already has different immutable payload")
                return existing, False
            state.evaluations[report.report_id] = report
            return report, True

        return self._mutate(operation)

    def issue_grant(self, grant: AdoptionGrant) -> AdoptionGrant:
        grant = AdoptionGrant.model_validate(grant)

        def operation(state: _LedgerState):
            existing = state.grants.get(grant.grant_id)
            if existing is not None:
                if existing != grant:
                    raise GovernanceConflict("grant_id already has different immutable payload")
                return existing, False
            binding = self._require_binding(state, grant.object_id)
            if (grant.authority_id, grant.authority_epoch, grant.expected_revision) != (
                binding.authority_id, binding.authority_epoch, binding.revision
            ):
                raise GovernanceRejected("grant does not match governed authority/baseline")
            candidate = self._candidate_by_digest(state, grant.candidate_digest)
            if candidate is None:
                raise GovernanceRejected("grant references unknown candidate digest")
            if candidate.object_id != grant.object_id:
                raise GovernanceRejected("grant candidate belongs to a different object")
            if candidate.expected_revision != grant.expected_revision:
                raise GovernanceRejected("grant/candidate baseline mismatch")
            for report_id in grant.evaluation_refs:
                report = state.evaluations.get(report_id)
                if report is None:
                    raise GovernanceRejected("grant references unknown evaluation")
                if report.candidate_digest != grant.candidate_digest:
                    raise GovernanceRejected("grant evaluation belongs to another candidate")
            state.grants[grant.grant_id] = grant
            return grant, True

        return self._mutate(operation)

    def revoke_grant(self, revocation: GrantRevocation) -> GrantRevocation:
        revocation = GrantRevocation.model_validate(revocation)

        def operation(state: _LedgerState):
            grant = state.grants.get(revocation.grant_id)
            if grant is None:
                raise GovernanceRejected("cannot revoke unknown grant")
            if revocation.revoked_at < grant.issued_at:
                raise GovernanceRejected("revocation cannot predate grant issuance")
            existing = state.revocations.get(revocation.grant_id)
            if existing is not None:
                if existing != revocation:
                    raise GovernanceConflict("grant already has a different revocation")
                return existing, False
            state.revocations[revocation.grant_id] = revocation
            return revocation, True

        return self._mutate(operation)

    def admit_attempt(
        self,
        *,
        grant_id: str,
        attempt_id: str,
        now: datetime,
        current_binding: ObjectBinding,
        active_governance_policy_ref: str,
        policy_check: GovernancePolicyCheck,
    ) -> ApplyAttempt:
        grant_id = _bounded(grant_id, name="grant_id")
        attempt_id = _bounded(attempt_id, name="attempt_id")
        now = _aware(now, name="now")
        current_binding = ObjectBinding.model_validate(current_binding)
        active_governance_policy_ref = _bounded(
            active_governance_policy_ref, name="active_governance_policy_ref"
        )

        def operation(state: _LedgerState):
            grant = state.grants.get(grant_id)
            if grant is None:
                raise GovernanceRejected("unknown adoption grant")
            consumed_attempt_id = state.grant_attempts.get(grant_id)
            if consumed_attempt_id is not None:
                if consumed_attempt_id != attempt_id:
                    raise GovernanceConflict("one grant may admit at most one attempt")
                return state.attempts[consumed_attempt_id], False
            if attempt_id in state.attempts:
                raise GovernanceConflict("attempt_id already belongs to another grant")
            if grant_id in state.revocations:
                raise GovernanceRejected("adoption grant is revoked")
            if now < grant.issued_at or now > grant.execute_before:
                raise GovernanceRejected("adoption grant is outside its execution window")
            stored_binding = self._require_binding(state, grant.object_id)
            if stored_binding != current_binding:
                raise GovernanceRejected("current binding does not match governed ledger state")
            if (
                current_binding.object_id != grant.object_id
                or current_binding.authority_id != grant.authority_id
                or current_binding.authority_epoch != grant.authority_epoch
                or current_binding.revision != grant.expected_revision
            ):
                raise GovernanceRejected("current binding does not satisfy grant fences")
            if grant.governance_policy_ref != active_governance_policy_ref:
                raise GovernanceRejected("governance policy changed after grant issuance")
            if not policy_check(grant):
                raise GovernanceRejected("active governance policy rejects grant execution")
            if self._has_unresolved_for_object(state, grant.object_id):
                raise GovernanceConflict("governed object already has an unresolved attempt")

            attempt = ApplyAttempt(
                attempt_id=attempt_id,
                grant_id=grant_id,
                started_at=now,
                status=AttemptStatus.STARTED,
            )
            state.attempts[attempt_id] = attempt
            state.grant_attempts[grant_id] = attempt_id
            state.receipts.setdefault(attempt_id, [])
            return attempt, True

        return self._mutate(operation)
    def append_receipt(
        self,
        *,
        receipt_id: str,
        attempt_id: str,
        executor_id: str,
        outcome: ReceiptOutcome | str,
        observed_at: datetime,
        observed_revision: str | None = None,
        observed_state_ref: ArtifactRef | None = None,
        details_ref: ArtifactRef | None = None,
    ) -> ApplyReceipt:
        """Atomically append an authoritative observation and resolve the attempt."""

        receipt_id = _bounded(receipt_id, name="receipt_id")
        attempt_id = _bounded(attempt_id, name="attempt_id")
        executor_id = _bounded(executor_id, name="executor_id")
        observed_at = _aware(observed_at, name="observed_at")
        outcome = ReceiptOutcome(outcome)
        if observed_revision is not None:
            observed_revision = _bounded(observed_revision, name="observed_revision")
        if observed_state_ref is not None:
            observed_state_ref = ArtifactRef.model_validate(observed_state_ref)
        if details_ref is not None:
            details_ref = ArtifactRef.model_validate(details_ref)
        def operation(state: _LedgerState):
            attempt = state.attempts.get(attempt_id)
            if attempt is None:
                raise GovernanceRejected("cannot append receipt for unknown attempt")
            grant = state.grants.get(attempt.grant_id)
            if grant is None:
                raise GovernanceStateError("attempt references missing grant")

            existing = self._find_receipt(state, receipt_id)
            if existing is not None:
                proposed = {
                    "receipt_id": receipt_id,
                    "attempt_id": attempt_id,
                    "executor_id": executor_id,
                    "outcome": outcome,
                    "observed_revision": observed_revision,
                    "observed_state_ref": observed_state_ref,
                    "observed_at": observed_at,
                    "details_ref": details_ref,
                }
                if not self._receipt_matches(existing, proposed):
                    raise GovernanceConflict("receipt_id already has different immutable payload")
                return existing, False

            previous = state.receipts.get(attempt_id, [])
            expected_executor = (
                previous[0].executor_id if previous
                else self._require_binding(state, grant.object_id).executor_id
            )
            if executor_id != expected_executor:
                raise GovernanceRejected("receipt executor does not match admitted object executor")
            next_sequence = 1 + max(
                (row.receipt_sequence for row in previous), default=0
            )
            receipt = ApplyReceipt(
                receipt_id=receipt_id,
                attempt_id=attempt_id,
                receipt_sequence=next_sequence,
                executor_id=executor_id,
                outcome=outcome,
                observed_revision=observed_revision,
                observed_state_ref=observed_state_ref,
                observed_at=observed_at,
                details_ref=details_ref,
            )
            state.receipts.setdefault(attempt_id, []).append(receipt)
            state.attempts[attempt_id] = attempt.model_copy(
                update={"status": AttemptStatus(outcome.value)}
            )
            return receipt, True

        return self._mutate(operation)

    @staticmethod
    def _candidate_digests(state: _LedgerState) -> set[str]:
        return {candidate.digest() for candidate in state.candidates.values()}

    @staticmethod
    def _candidate_by_digest(state: _LedgerState, digest_value: str) -> ChangeCandidate | None:
        for candidate in state.candidates.values():
            if candidate.digest() == digest_value:
                return candidate
        return None
    @staticmethod
    def _require_binding(state: _LedgerState, object_id: str) -> ObjectBinding:
        binding = state.bindings.get(object_id)
        if binding is None:
            raise GovernanceRejected("governed object has no binding")
        return binding

    @staticmethod
    def _grant_object_id_for_attempt(state: _LedgerState, attempt: ApplyAttempt) -> str:
        grant = state.grants.get(attempt.grant_id)
        if grant is None:
            raise GovernanceStateError("attempt references missing grant")
        return grant.object_id

    @classmethod
    def _has_unresolved_for_object(cls, state: _LedgerState, object_id: str) -> bool:
        for attempt in state.attempts.values():
            if attempt.status.value not in UNRESOLVED_STATUSES:
                continue
            if cls._grant_object_id_for_attempt(state, attempt) == object_id:
                return True
        return False

    @staticmethod
    def _find_receipt(state: _LedgerState, receipt_id: str) -> ApplyReceipt | None:
        for rows in state.receipts.values():
            for receipt in rows:
                if receipt.receipt_id == receipt_id:
                    return receipt
        return None
    @staticmethod
    def _receipt_matches(receipt: ApplyReceipt, proposed: dict[str, Any]) -> bool:
        current = {
            "receipt_id": receipt.receipt_id,
            "attempt_id": receipt.attempt_id,
            "executor_id": receipt.executor_id,
            "outcome": receipt.outcome,
            "observed_revision": receipt.observed_revision,
            "observed_state_ref": receipt.observed_state_ref,
            "observed_at": receipt.observed_at,
            "details_ref": receipt.details_ref,
        }
        return current == proposed


__all__ = [
    "AdoptionGrant",
    "ApplyAttempt",
    "ApplyReceipt",
    "ArtifactRef",
    "AttemptStatus",
    "ChangeCandidate",
    "EvaluationAttestation",
    "GovernanceConflict",
    "GovernanceRejected",
    "GovernanceStateError",
    "GovernedApplyError",
    "GovernedApplyLedger",
    "GrantRevocation",
    "ObjectBinding",
    "ReceiptOutcome",
]
