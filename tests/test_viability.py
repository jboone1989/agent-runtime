from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.cognition.world_model import WorldState
from agent_runtime.config import load_config
from agent_runtime.identity import create_genesis_identity
from agent_runtime.self_model.model import SelfState
from agent_runtime.clock import FrozenClock
from agent_runtime.viability.evaluator import DefaultViabilityModel


def test_viability_keeps_dimensions_and_does_not_collapse_to_score() -> None:
    cfg = load_config()
    model = DefaultViabilityModel(cfg.viability)
    clock = FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    ident = create_genesis_identity("0.1.0", clock)
    self_state = SelfState(
        identity=ident,
        metrics={
            "compute": 0.21,
            "operational_integrity": 0.9,
            "identity_integrity": 0.95,
            "memory_integrity": 0.9,
            "storage": 0.8,
            "network_connectivity": 0.9,
            "financial_resources": 0.7,
            "capability": 0.8,
            "security": 0.85,
            "social_support": 0.5,
            "goal_progress": 0.6,
            "future_survivability": 0.7,
        },
    )
    world = WorldState(timestamp=clock.now())
    observed = model.observe(self_state, world)
    dim = observed.get("compute")
    assert dim.value == 0.21
    assert dim.preferred_min == 0.40
    assert dim.critical_min == 0.10
    prediction = model.predict(observed, timedelta(hours=1))
    assessment = model.evaluate(prediction)
    assert assessment.dimensions
    assert assessment.threats
    assert any(t.dimension == "compute" for t in assessment.threats)
    assert 0.0 <= assessment.score <= 1.0
    # score exists but threats/dimensions remain the decision surface
    assert assessment.score != assessment.threats[0].severity or len(assessment.dimensions) > 1
