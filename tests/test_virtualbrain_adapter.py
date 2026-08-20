from __future__ import annotations

from agent_runtime.integrations.virtualbrain import VirtualBrainMotivationEngine, map_from_readout
from agent_runtime.cognition.world_model import WorldState
from agent_runtime.memory.view import MemoryView
from agent_runtime.self_model.model import SelfModel
from agent_runtime.identity import create_genesis_identity
from agent_runtime.clock import SystemClock
from agent_runtime.viability.state import ViabilityAssessment


class _FakeBrain:
    def pad(self) -> tuple[float, float, float]:
        return (0.2, 0.8, -0.1)

    def threat_level(self) -> float:
        return 0.7

    def opportunity_level(self) -> float:
        return 0.1

    def needs_pressures(self) -> dict[str, float]:
        return {"safety": 0.9}

    def emotion_label(self) -> str:
        return "fear"

    def rpe(self) -> float:
        return -0.3


def test_virtualbrain_mapping_pad_to_motivation() -> None:
    mapped = map_from_readout(
        {
            "pad": (0.5, 0.2, 0.1),
            "threat": 0.4,
            "opportunity": 0.1,
            "pressures": {"competence": 0.3},
            "emotion": "anticipation",
            "rpe": 0.2,
        }
    )
    assert mapped.valence == 0.5
    assert mapped.arousal == 0.2
    assert mapped.threat == 0.4
    assert mapped.drives["competence"] == 0.3
    assert mapped.learning_signal == 0.2
    assert "anticipation" in mapped.attention_bias


def test_virtualbrain_engine_is_pluggable() -> None:
    ident = create_genesis_identity("0.1.0", SystemClock())
    model = SelfModel.bootstrap(ident, ident.created_at)
    engine = VirtualBrainMotivationEngine(_FakeBrain())
    state = engine.process(
        self_model=model,
        viability=ViabilityAssessment(
            score=0.5, threats=[], opportunities=[], urgency=0.2, uncertainty=0.1
        ),
        world_state=WorldState(timestamp=ident.created_at),
        event=None,
        memory=MemoryView(0, [], {}),
    )
    assert state.valence == 0.2
    assert state.arousal == 0.8
    assert state.threat == 0.7
    assert state.learning_signal == -0.3
    assert state.drives["safety"] == 0.9
    assert state.attention_bias["fear"] == 1.0
