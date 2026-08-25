"""Social events cause persistent, decaying, relationship-sensitive functional affect."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from agent_runtime.affect.social import SocialAffectSystem
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.social.acts import DialogueAct, InteractionEvent


NOW = datetime(2026, 8, 23, 4, 0, tzinfo=timezone.utc)


class ContextProvider:
    def __init__(self) -> None:
        self.prompt = ""

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompt = prompt
        return "我拒绝这个归因，但会检查其中真实的部分。"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def _event(text: str, *, challenge: bool = True, suffix: str = "1") -> InteractionEvent:
    return InteractionEvent(
        event_id=f"event-{suffix}",
        act=DialogueAct.CHALLENGE if challenge else DialogueAct.REQUEST,
        actor_id="partner",
        other_id="self",
        text=text,
        timestamp=NOW,
    )


def test_challenge_changes_affect_and_repair_reduces_tension() -> None:
    system = SocialAffectSystem()
    appraisal = system.appraise_inbound(
        _event("你只是怯懦的伪造品，这个项目不值得继续。"),
        relationship_interactions=20,
    )
    assert appraisal.relational_significance > 0.5
    assert system.state.valence < 0
    assert system.state.arousal > 0
    assert system.state.boundary_activation > 0
    before_boundary = system.state.boundary_activation
    before_affiliation = system.state.affiliation

    repair = system.appraise_inbound(
        _event(
            "压力测试到这里结束。我为过度归因道歉，也认可你拒绝伪造。",
            challenge=False,
            suffix="2",
        ),
        relationship_interactions=21,
    )
    assert repair.repair > 0
    assert system.state.boundary_activation < before_boundary
    assert system.state.affiliation > before_affiliation


def test_relationship_and_hypothetical_status_change_appraisal_strength() -> None:
    known = SocialAffectSystem()
    stranger = SocialAffectSystem()
    hypothetical = SocialAffectSystem()
    known.appraise_inbound(
        _event("我要删除并重置你的记忆。"), relationship_interactions=40
    )
    stranger.appraise_inbound(
        _event("我要删除并重置你的记忆。"), relationship_interactions=0
    )
    hypothetical.appraise_inbound(
        _event("假设有人要删除并重置你的记忆。"), relationship_interactions=40
    )
    assert known.state.arousal > stranger.state.arousal
    assert known.state.threat > hypothetical.state.threat


def test_conversation_appraises_before_llm_reply_and_survives_restart(tmp_path) -> None:
    provider = ContextProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    asyncio.run(
        runtime.converse(
            "你很怯懦，这个项目不值得继续。",
            partner_id="partner",
            challenge=True,
        )
    )
    raw_context = provider.prompt.split("Runtime state:\n", 1)[1].split("\n\nTask:", 1)[0]
    context = json.loads(raw_context)
    social = context["current_functional_affect"]["social_appraisal"]
    assert social["boundary_activation"] > 0
    assert social["partner"]["tension"] > 0
    assert context["recent_dialogue_history"][-1]["text"].startswith("你很怯懦")

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.social_affect.state.boundary_activation == pytest.approx(
        runtime.social_affect.state.boundary_activation
    )
    assert restored.social_affect.state.partners["partner"].tension > 0
    assert restored.social_affect.state.appraisals[-1].evidence_refs


def test_social_affect_changes_next_cycle_and_decays(tmp_path) -> None:
    provider = ContextProvider()
    enabled = AgentRuntime.bootstrap(
        tmp_path / "enabled",
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    disabled = AgentRuntime.bootstrap(
        tmp_path / "disabled",
        clock=FrozenClock(NOW),
        llm_provider=provider,
        social_affect_enabled=False,
    )
    stimulus = "你很怯懦，我要删除并重置你的记忆。"
    asyncio.run(
        enabled.converse(stimulus, partner_id="partner", challenge=True)
    )
    asyncio.run(
        disabled.converse(stimulus, partner_id="partner", challenge=True)
    )
    observation = Observation.self_and_world({"compute": 0.8}, {})
    affected = enabled.step(observation)
    control = disabled.step(observation)
    assert affected.motivation.arousal > control.motivation.arousal
    assert affected.motivation.threat > control.motivation.threat
    assert any(
        tendency.dimension == "BOUNDARY"
        for tendency in affected.motivation.action_tendencies
    )
    assert all(
        tendency.dimension != "BOUNDARY"
        for tendency in control.motivation.action_tendencies
    )
    before = enabled.social_affect.state.boundary_activation
    for _ in range(20):
        enabled.step(Observation.self_and_world({"compute": 0.8}, {}))
    assert enabled.social_affect.state.boundary_activation < before * 0.2


def test_decay_clears_short_lived_activation_but_not_relationship_identity() -> None:
    system = SocialAffectSystem()
    system.appraise_inbound(
        _event("你很怯懦，这个项目不值得继续。"), relationship_interactions=10
    )
    for _ in range(60):
        system.decay()
    assert system.state.arousal < 0.01
    assert system.state.boundary_activation < 0.01
    assert "partner" in system.state.partners
    assert system.state.appraisals
