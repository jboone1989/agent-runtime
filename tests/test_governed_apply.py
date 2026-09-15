from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path

import pytest

from agent_runtime.governance.governed_apply import (
    AdoptionGrant, ArtifactRef, ChangeCandidate, EvaluationAttestation,
    GovernanceConflict, GovernanceRejected, GovernanceStateError,
    GovernedApplyLedger, GrantRevocation, ObjectBinding, ReceiptOutcome,
)

NOW = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
OBJECT_ID = "content:website-opening-policy"


def artifact(name: str) -> ArtifactRef:
    import hashlib
    return ArtifactRef(
        locator=f"state://{name}",
        digest="sha256:" + hashlib.sha256(name.encode()).hexdigest(),
        media_type="application/json",
    )

def binding(*, epoch: int = 1, revision: str = "r1", authority: str = "authority-a") -> ObjectBinding:
    return ObjectBinding(
        object_id=OBJECT_ID,
        authority_id=authority,
        authority_epoch=epoch,
        executor_id="executor-a",
        revision=revision,
        confirmed_state_ref=artifact(f"state-{revision}"),
    )


def candidate(*, candidate_id: str = "candidate-1", revision: str = "r1") -> ChangeCandidate:
    return ChangeCandidate(
        candidate_id=candidate_id,
        object_id=OBJECT_ID,
        expected_revision=revision,
        change_artifact_ref=artifact(f"change-{candidate_id}"),
        submitted_principal="content-agent",
        submitted_at=NOW,
        source_metadata={"experiment": "website-opening-mode:v1"},
    )


def report(candidate_digest: str, *, report_id: str = "report-1") -> EvaluationAttestation:
    return EvaluationAttestation(
        report_id=report_id,
        candidate_digest=candidate_digest,
        evaluation_profile_ref="profile:fixed-horizon-v1",
        evaluator_principal="content-evaluator",
        report_artifact_ref=artifact(report_id),
        completed_at=NOW + timedelta(minutes=1),
    )

def grant(candidate_digest: str, *, grant_id: str = "grant-1", epoch: int = 1,
          revision: str = "r1", authority: str = "authority-a") -> AdoptionGrant:
    return AdoptionGrant(
        grant_id=grant_id,
        object_id=OBJECT_ID,
        authority_id=authority,
        authority_epoch=epoch,
        candidate_digest=candidate_digest,
        expected_revision=revision,
        governance_policy_ref="policy:v1",
        evaluation_refs=("report-1",),
        issued_principal="runtime-governance",
        issued_at=NOW + timedelta(minutes=2),
        execute_before=NOW + timedelta(hours=1),
    )


def prepared_ledger(tmp_path: Path, *, grant_id: str = "grant-1"):
    ledger = GovernedApplyLedger(tmp_path / "gai.json")
    current = binding()
    ledger.record_binding(current)
    change = candidate()
    digest = ledger.record_candidate(change)
    ledger.record_evaluation(report(digest))
    issued = grant(digest, grant_id=grant_id)
    ledger.issue_grant(issued)
    return ledger, current, issued


def admit(ledger, current, issued, *, attempt_id="attempt-1", now=None):
    return ledger.admit_attempt(
        grant_id=issued.grant_id,
        attempt_id=attempt_id,
        now=now or NOW + timedelta(minutes=3),
        current_binding=current,
        active_governance_policy_ref="policy:v1",
        policy_check=lambda _: True,
    )

def test_basic_admission_receipt_and_restart(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    assert attempt.status.value == "STARTED"

    first = ledger.append_receipt(
        receipt_id="receipt-1", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.APPLIED,
        observed_revision="r2", observed_state_ref=artifact("state-r2"),
        observed_at=NOW + timedelta(minutes=4),
    )
    assert first.receipt_sequence == 1
    assert ledger.attempt(attempt.attempt_id).status.value == "APPLIED"

    restarted = GovernedApplyLedger(tmp_path / "gai.json")
    assert restarted.attempt(attempt.attempt_id).status.value == "APPLIED"
    assert restarted.receipts_for(attempt.attempt_id) == (first,)
    assert restarted.snapshot()["ledger_revision"] == 6


def test_same_grant_reuses_attempt_and_rejects_second_attempt_id(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    first = admit(ledger, current, issued, attempt_id="attempt-a")
    replay = admit(ledger, current, issued, attempt_id="attempt-a")
    assert replay == first
    with pytest.raises(GovernanceConflict, match="at most one attempt"):
        admit(ledger, current, issued, attempt_id="attempt-b")

def test_revocation_and_expiry_block_only_new_admission(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    ledger.revoke_grant(GrantRevocation(
        grant_id=issued.grant_id,
        revoked_principal="operator",
        revoked_at=NOW + timedelta(minutes=3),
    ))
    with pytest.raises(GovernanceRejected, match="revoked"):
        admit(ledger, current, issued)

    other_root = tmp_path / "other"
    other_root.mkdir()
    ledger2, current2, issued2 = prepared_ledger(other_root)
    with pytest.raises(GovernanceRejected, match="execution window"):
        admit(
            ledger2, current2, issued2,
            now=NOW + timedelta(hours=2),
        )


def test_policy_and_binding_fences_are_checked_atomically(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    with pytest.raises(GovernanceRejected, match="policy changed"):
        ledger.admit_attempt(
            grant_id=issued.grant_id, attempt_id="attempt-1",
            now=NOW + timedelta(minutes=3), current_binding=current,
            active_governance_policy_ref="policy:v2", policy_check=lambda _: True,
        )
    with pytest.raises(GovernanceRejected, match="policy rejects"):
        ledger.admit_attempt(
            grant_id=issued.grant_id, attempt_id="attempt-1",
            now=NOW + timedelta(minutes=3), current_binding=current,
            active_governance_policy_ref="policy:v1", policy_check=lambda _: False,
        )

def test_unresolved_attempt_blocks_second_grant_and_authority_transfer(tmp_path):
    ledger, current, first_grant = prepared_ledger(tmp_path)
    digest = first_grant.candidate_digest
    second = grant(digest, grant_id="grant-2")
    ledger.issue_grant(second)
    admit(ledger, current, first_grant, attempt_id="attempt-1")

    with pytest.raises(GovernanceConflict, match="unresolved attempt"):
        admit(ledger, current, second, attempt_id="attempt-2")
    with pytest.raises(GovernanceConflict, match="while an attempt is unresolved"):
        ledger.record_binding(binding(epoch=2, authority="authority-b"))


def test_authority_transfer_invalidates_unused_old_grant(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    newer = binding(epoch=2, revision="r1", authority="authority-b")
    ledger.record_binding(newer)
    with pytest.raises(GovernanceRejected, match="current binding"):
        ledger.admit_attempt(
            grant_id=issued.grant_id, attempt_id="attempt-old",
            now=NOW + timedelta(minutes=3), current_binding=newer,
            active_governance_policy_ref="policy:v1", policy_check=lambda _: True,
        )


def test_out_of_band_revision_change_rejects_old_grant(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    observed = binding(revision="r2")
    with pytest.raises(GovernanceRejected, match="current binding"):
        ledger.admit_attempt(
            grant_id=issued.grant_id, attempt_id="attempt-1",
            now=NOW + timedelta(minutes=3), current_binding=observed,
            active_governance_policy_ref="policy:v1", policy_check=lambda _: True,
        )

def test_receipt_replay_is_idempotent_and_conflict_is_rejected(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    kwargs = dict(
        receipt_id="receipt-1", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=4),
    )
    first = ledger.append_receipt(**kwargs)
    replay = ledger.append_receipt(**kwargs)
    assert replay == first
    assert len(ledger.receipts_for(attempt.attempt_id)) == 1

    with pytest.raises(GovernanceConflict, match="different immutable payload"):
        ledger.append_receipt(
            **{**kwargs, "outcome": ReceiptOutcome.RECOVERY_REQUIRED}
        )


def test_unknown_can_be_reconciled_to_applied_without_rewriting_history(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    unknown = ledger.append_receipt(
        receipt_id="receipt-unknown", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=4),
    )
    assert unknown.receipt_sequence == 1
    assert ledger.attempt(attempt.attempt_id).status.value == "UNKNOWN"
    applied = ledger.append_receipt(
        receipt_id="receipt-applied", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.APPLIED,
        observed_revision="r2", observed_state_ref=artifact("state-r2"),
        observed_at=NOW + timedelta(minutes=6),
    )
    assert applied.receipt_sequence == 2
    assert [row.outcome.value for row in ledger.receipts_for(attempt.attempt_id)] == [
        "UNKNOWN", "APPLIED",
    ]
    assert ledger.attempt(attempt.attempt_id).status.value == "APPLIED"

    replay_old = ledger.append_receipt(
        receipt_id="receipt-unknown", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=4),
    )
    assert replay_old.receipt_sequence == 1
    assert ledger.attempt(attempt.attempt_id).status.value == "APPLIED"


def test_receipt_executor_is_bound_to_admitted_object(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    with pytest.raises(GovernanceRejected, match="executor"):
        ledger.append_receipt(
            receipt_id="receipt-1", attempt_id=attempt.attempt_id,
            executor_id="executor-b", outcome=ReceiptOutcome.UNKNOWN,
            observed_at=NOW + timedelta(minutes=4),
        )

def test_restart_rejects_status_receipt_mismatch(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    ledger.append_receipt(
        receipt_id="receipt-1", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=4),
    )
    state_path = tmp_path / "gai.json"
    raw = json.loads(state_path.read_text())
    raw["attempts"][attempt.attempt_id]["status"] = "APPLIED"
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GovernanceStateError, match="invalid governed-apply ledger state"):
        GovernedApplyLedger(state_path).snapshot()


def _race_admit(args):
    state_path, grant_id, attempt_id, binding_payload = args
    ledger = GovernedApplyLedger(state_path)
    try:
        attempt = ledger.admit_attempt(
            grant_id=grant_id, attempt_id=attempt_id,
            now=NOW + timedelta(minutes=3),
            current_binding=ObjectBinding.model_validate(binding_payload),
            active_governance_policy_ref="policy:v1", policy_check=lambda _: True,
        )
        return ("ok", attempt.attempt_id)
    except (GovernanceConflict, GovernanceRejected) as exc:
        return (type(exc).__name__, str(exc))

def test_two_processes_cannot_admit_two_attempts_for_one_grant(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    args = [
        (str(tmp_path / "gai.json"), issued.grant_id, "attempt-a", current.model_dump(mode="json")),
        (str(tmp_path / "gai.json"), issued.grant_id, "attempt-b", current.model_dump(mode="json")),
    ]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(2) as pool:
        results = pool.map(_race_admit, args)
    assert sum(1 for status, _ in results if status == "ok") == 1
    assert sum(1 for status, _ in results if status == "GovernanceConflict") == 1
    snapshot = ledger.snapshot()
    assert len(snapshot["attempts"]) == 1
    assert snapshot["grant_attempts"][issued.grant_id] in {"attempt-a", "attempt-b"}


def test_two_grants_on_same_baseline_cannot_run_concurrently(tmp_path):
    ledger, current, first = prepared_ledger(tmp_path)
    second = grant(first.candidate_digest, grant_id="grant-2")
    ledger.issue_grant(second)
    args = [
        (str(tmp_path / "gai.json"), first.grant_id, "attempt-a", current.model_dump(mode="json")),
        (str(tmp_path / "gai.json"), second.grant_id, "attempt-b", current.model_dump(mode="json")),
    ]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(2) as pool:
        results = pool.map(_race_admit, args)
    assert sum(1 for status, _ in results if status == "ok") == 1
    assert sum(1 for status, _ in results if status == "GovernanceConflict") == 1

def test_restart_preserves_started_as_unresolved(tmp_path):
    ledger, current, first = prepared_ledger(tmp_path)
    second = grant(first.candidate_digest, grant_id="grant-2")
    ledger.issue_grant(second)
    admit(ledger, current, first, attempt_id="attempt-1")

    restarted = GovernedApplyLedger(tmp_path / "gai.json")
    assert restarted.attempt("attempt-1").status.value == "STARTED"
    with pytest.raises(GovernanceConflict, match="unresolved attempt"):
        admit(restarted, current, second, attempt_id="attempt-2")


def test_revocation_after_admission_keeps_attempt_and_allows_observation(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    ledger.revoke_grant(GrantRevocation(
        grant_id=issued.grant_id,
        revoked_principal="operator",
        revoked_at=NOW + timedelta(minutes=4),
    ))
    receipt = ledger.append_receipt(
        receipt_id="receipt-after-revoke", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=5),
    )
    assert receipt.receipt_sequence == 1
    assert ledger.attempt(attempt.attempt_id).status.value == "UNKNOWN"

def test_restart_rejects_two_grants_indexed_to_one_attempt(tmp_path):
    ledger, current, first = prepared_ledger(tmp_path)
    second = grant(first.candidate_digest, grant_id="grant-2")
    ledger.issue_grant(second)
    admit(ledger, current, first, attempt_id="attempt-1")
    state_path = tmp_path / "gai.json"
    raw = json.loads(state_path.read_text())
    raw["grant_attempts"]["grant-2"] = "attempt-1"
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GovernanceStateError, match="invalid governed-apply ledger state"):
        GovernedApplyLedger(state_path).snapshot()


def test_restart_rejects_receipts_with_mixed_executors(tmp_path):
    ledger, current, issued = prepared_ledger(tmp_path)
    attempt = admit(ledger, current, issued)
    ledger.append_receipt(
        receipt_id="receipt-1", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.UNKNOWN,
        observed_at=NOW + timedelta(minutes=4),
    )
    ledger.append_receipt(
        receipt_id="receipt-2", attempt_id=attempt.attempt_id,
        executor_id="executor-a", outcome=ReceiptOutcome.APPLIED,
        observed_revision="r2", observed_state_ref=artifact("state-r2"),
        observed_at=NOW + timedelta(minutes=5),
    )
    state_path = tmp_path / "gai.json"
    raw = json.loads(state_path.read_text())
    raw["receipts"][attempt.attempt_id][1]["executor_id"] = "executor-b"
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GovernanceStateError, match="invalid governed-apply ledger state"):
        GovernedApplyLedger(state_path).snapshot()
