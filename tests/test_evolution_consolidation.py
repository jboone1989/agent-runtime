from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    CapabilityState,
    CapabilityVersion,
    EvidenceAge,
    EvidenceClass,
    EvidenceMetric,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)


def _evidence(rt: AgentRuntime, label: str, *, success: bool | None = True):
    rt.step()
    experience = rt.evolution_experiences[-1]
    return rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=rt.clock.now(),
        outcome_success=success,
        metrics=[EvidenceMetric("engagement", 1.0, "score")],
        evidence_refs=[f"world:{label}"],
    )


def test_one_event_does_not_become_permanent_learning(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _evidence(rt, "one")
    report = rt.consolidate_evolution()

    assert len(report.clusters) >= 1
    world_cluster = next(
        row for row in report.clusters if "engagement" in row.signature
    )
    assert world_cluster.recurring is False
    assert report.suggestions == ()
    assert rt.evolution_candidates == []


def test_repeated_evidence_creates_suggestion_but_not_candidate_or_activation(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _evidence(rt, "a")
    _evidence(rt, "b")

    report = rt.consolidate_evolution()

    world_cluster = next(
        row for row in report.clusters if "engagement" in row.signature
    )
    assert world_cluster.recurring is True
    assert any(
        item.cluster_id == world_cluster.cluster_id
        for item in report.suggestions
    )
    assert rt.evolution_candidates == []
    assert rt.evolution_capability_versions == []


def test_conflicting_repeated_outcomes_remain_visible(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    first = _evidence(rt, "positive", success=True)
    second = _evidence(rt, "negative", success=False)
    report = rt.consolidate_evolution()

    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert set(conflict.evidence_refs) == {
        first.evidence_id,
        second.evidence_id,
    }


def test_stale_evidence_is_aged_without_deleting_provenance(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    item = _evidence(rt, "old")
    before = list(rt.evolution_evidence)
    clock.advance(31 * 24 * 3600)

    report = rt.consolidate_evolution()

    age = next(row for row in report.evidence_ages if row.evidence_id == item.evidence_id)
    assert age.age is EvidenceAge.STALE
    assert rt.evolution_evidence == before


def test_sleep_only_proposes_deprecation_and_report_persists(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    version = CapabilityVersion(
        capability_id="content.strategy",
        version_id="cap:v1",
        implementation_ref="strategy:v1",
        born_at=NOW - timedelta(days=120),
        candidate_id="candidate:v1",
        experiment_id="experiment:v1",
        evaluation_id="evaluation:v1",
        parent_version_id=None,
        rollback_target=None,
        supporting_evidence_refs=["world:old"],
        confidence=0.8,
        last_verified_at=NOW - timedelta(days=100),
        state=CapabilityState.ACTIVE,
    )
    rt.evolution_capability_registry.versions.append(version)

    report = rt.consolidate_evolution()

    assert len(report.deprecation_proposals) == 1
    assert version.state is CapabilityState.ACTIVE
    restored = AgentRuntime.restore(tmp_path, clock=clock)
    assert restored.evolution_sleep_reports == [report]
    assert restored.evolution_capability_versions[0].state is CapabilityState.ACTIVE
