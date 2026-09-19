from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.evolution import (
    EvolutionEngine,
    EvolutionFactKind,
    EvolutionNeedSource,
)


NOW = datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        (EvolutionFactKind.EXTERNAL_MISSION, EvolutionNeedSource.EXTERNAL_MISSION),
        (EvolutionFactKind.FAILURE, EvolutionNeedSource.FAILURE),
        (EvolutionFactKind.RESOURCE_PRESSURE, EvolutionNeedSource.RESOURCE_PRESSURE),
        (EvolutionFactKind.CAPABILITY_GAP, EvolutionNeedSource.CAPABILITY_GAP),
        (
            EvolutionFactKind.REPEATED_INEFFICIENCY,
            EvolutionNeedSource.REPEATED_INEFFICIENCY,
        ),
        (EvolutionFactKind.OPPORTUNITY, EvolutionNeedSource.OPPORTUNITY),
        (EvolutionFactKind.CURIOSITY, EvolutionNeedSource.CURIOSITY),
        (EvolutionFactKind.VALUE_CONFLICT, EvolutionNeedSource.VALUE_CONFLICT),
        (
            EvolutionFactKind.META_LEARNING_SIGNAL,
            EvolutionNeedSource.META_LEARNING_SIGNAL,
        ),
    ],
)
def test_canonical_fact_kind_materializes_canonical_need_source(kind, source):
    engine = EvolutionEngine(severe_single_fact=0.0)
    engine.observe_fact(
        cycle=1,
        observed_at=NOW,
        kind=kind,
        subject="subject",
        summary="material signal",
        severity=0.5,
        evidence_refs=["evidence:1"],
    )

    [need] = engine.open_needs()
    assert need.source_kinds == [source]
    assert need.evidence_refs == ["evidence:1"]


def test_legacy_resource_friction_and_resource_pressure_consolidate():
    engine = EvolutionEngine(severe_single_fact=0.0)
    engine.observe_fact(
        cycle=1,
        observed_at=NOW,
        kind=EvolutionFactKind.RESOURCE_FRICTION,
        subject="disk",
        summary="disk pressure",
        severity=0.6,
        evidence_refs=["disk:1"],
    )
    engine.observe_fact(
        cycle=2,
        observed_at=NOW,
        kind=EvolutionFactKind.RESOURCE_PRESSURE,
        subject="disk",
        summary="disk pressure continues",
        severity=0.7,
        evidence_refs=["disk:2"],
    )

    [need] = engine.open_needs()
    assert need.source_kinds == [EvolutionNeedSource.RESOURCE_PRESSURE]
    assert set(need.evidence_refs) == {"disk:1", "disk:2"}
    assert len(need.source_pattern_refs) == 2


def test_stagnation_maps_to_meta_learning_signal():
    engine = EvolutionEngine(
        severe_single_fact=0.0,
        reflection_interval=1,
        stagnation_cycles=1,
    )
    engine.reflect(cycle=2, observed_at=NOW, evidence_refs=["cycle:2"])

    [need] = engine.open_needs()
    assert need.source_kinds == [EvolutionNeedSource.META_LEARNING_SIGNAL]
