"""Legacy RecordingBrain proofs. Primary Life Loop proof is test_life_loop.py."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.embodiment import digital_pack, mock_physical_pack
from agent_runtime.integrations.virtualbrain import VirtualBrainMotivationEngine
from agent_runtime.runtime import AgentRuntime
from agent_runtime.survival.failover import ProviderPool


class RecordingBrain:
    """Duck-typed VirtualBrain surface. Records resolved vs unresolved regulation events."""

    def __init__(self) -> None:
        self.events: list = []
        self._threat = 0.05
        self._arousal = 0.15
        self._valence = 0.1

    def pad(self) -> tuple[float, float, float]:
        return (self._valence, self._arousal, 0.0)

    def threat_level(self) -> float:
        return self._threat

    def opportunity_level(self) -> float:
        return 0.0

    def needs_pressures(self) -> dict[str, float]:
        return {"safety": self._threat}

    def emotion_label(self) -> str:
        return "alert" if self._arousal > 0.3 else "calm"

    def rpe(self) -> float:
        return 0.0

    def ingest(self, event, now) -> None:
        if event is None:
            return
        self.events.append(event)
        self._arousal = min(1.0, self._arousal + 0.25)
        if event.data.get("resolved"):
            self._threat = 0.2
            self._valence = 0.0
        else:
            self._threat = 0.85
            self._valence = -0.4


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))


def test_digital_local_failover_affects_brain_without_extra_recovery_goal(tmp_path) -> None:
    brain = RecordingBrain()
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "d",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
        motivation_engine=VirtualBrainMotivationEngine(brain),
    )
    rt.environment.apply("providers/primary/error_rate", 0.72)
    rt.environment.apply("providers/backup/error_rate", 0.02)
    result = rt.tick()
    assert any(a.kind == "switch_provider" for a in result.local_actions)
    assert all(e.code != "COGNITIVE_CAPABILITY_AT_RISK" for e in result.escalations)
    assert brain.events
    assert brain.events[-1].data.get("resolved") is True
    assert result.motivation.arousal > 0.15
    assert result.motivation.threat < 0.5
    assert not any("capability" in g.description.lower() and g.origin.value == "VIABILITY" for g in result.goals_created)


def test_digital_failed_local_repair_escalates_into_internal_goal(tmp_path) -> None:
    brain = RecordingBrain()
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "d2",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
        motivation_engine=VirtualBrainMotivationEngine(brain),
    )
    rt.environment.apply("providers/primary/error_rate", 0.95)
    rt.environment.apply("providers/backup/error_rate", 0.95)
    result = rt.tick()
    assert any(e.code == "COGNITIVE_CAPABILITY_AT_RISK" for e in result.escalations)
    assert brain.events and brain.events[-1].data.get("resolved") is False
    assert result.motivation.threat >= 0.7
    internals = [g for g in rt.goals if g.origin.value in {"VIABILITY", "INTERNAL"}]
    assert internals


def test_mock_physical_conservation_uses_same_runtime_and_brain(tmp_path) -> None:
    brain = RecordingBrain()
    pack = mock_physical_pack()
    pack.environment.apply("battery", 0.10)
    rt = AgentRuntime.bootstrap(
        tmp_path / "p",
        clock=_clock(),
        embodiment=pack,
        motivation_engine=VirtualBrainMotivationEngine(brain),
    )
    result = rt.tick()
    assert any(a.kind == "conserve" for a in result.local_actions) or rt.regulatory_state.resource_conservation >= 0.6
    assert rt.environment.environment_id == "mock_physical"
    assert result.motivation.arousal >= 0.15
    assert type(rt).__name__ == "AgentRuntime"
