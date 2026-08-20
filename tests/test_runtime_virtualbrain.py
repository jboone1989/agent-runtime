"""Step E: the same AgentRuntime can run DefaultMotivationEngine or VirtualBrain."""
from __future__ import annotations

import pytest

from agent_runtime.clock import new_id
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.observations import Event, Observation
from agent_runtime.runtime import AgentRuntime

pytest.importorskip("virtualbrain")

from virtualbrain.cognition.rule_engine import RuleBasedProvider  # noqa: E402
from virtualbrain.config.loader import PersonProfile, load_config  # noqa: E402
from virtualbrain.experiment.person import VirtualPerson  # noqa: E402

from agent_runtime.integrations.virtualbrain import VirtualBrainMotivationEngine  # noqa: E402


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


def test_runtime_runs_with_real_virtual_person(tmp_path) -> None:
    person = VirtualPerson(PersonProfile(name="被试"), load_config())
    engine = VirtualBrainMotivationEngine.wrap(person, provider=RuleBasedProvider())
    rt = AgentRuntime.bootstrap(
        state_dir=tmp_path,
        motivation_engine=engine,
        autonomy=AutonomyPolicy.default(),
    )
    p0, a0, _ = person.pad
    event = Event(
        event_id=new_id("evt"),
        kind="world",
        description="工作中被上司当众批评",
    )
    result = rt.step(
        Observation.self_and_world(
            self_metrics=_healthy(),
            world={"scene": "office"},
            event=event,
        )
    )
    assert result.motivation.valence == pytest.approx(person.pad[0], abs=1e-6)
    assert result.motivation.arousal == pytest.approx(person.pad[1], abs=1e-6)
    assert result.motivation.threat == pytest.approx(person.threat, abs=1e-6)
    assert person.pad[0] < p0
    assert person.pad[1] > a0
    assert person.threat > 0.0
    assert result.motivation.drives
    assert result.motivation.learning_signal == pytest.approx(
        person.event_log[-1]["appraisal"]["reward_delta"], abs=1e-6
    )
    assert person.emotion.label in result.motivation.attention_bias
