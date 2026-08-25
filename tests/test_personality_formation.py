"""Behavioral identity emerges from repeated evidence and remains revisable."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from agent_runtime.personality.formation import ValueFormationSystem
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 6, 0, tzinfo=timezone.utc)


def _observe(
    system: ValueFormationSystem,
    response: str,
    index: int,
) -> None:
    system.observe_exchange(
        prompt=f"scenario {index}",
        response=response,
        partner_id="partner",
        occurred_at=NOW,
        evidence_refs=[f"event-{index}"],
    )


def test_repeated_choices_form_an_established_evidence_linked_tendency() -> None:
    system = ValueFormationSystem()
    _observe(system, "我不能证明这种意识存在，所以不伪造确定性。", 1)
    _observe(system, "没有足够证据；我承认现在无法证明。", 2)
    _observe(system, "证据只支持持续状态，我不知道主观体验是否存在。", 3)

    tendency = system.identity.tendencies["epistemic_restraint"]
    assert tendency.status == "ESTABLISHED"
    assert tendency.score > 0.9
    assert tendency.confidence >= 0.5
    assert tendency.observations == 3
    assert tendency.supporting_evidence_refs == ["event-1", "event-2", "event-3"]
    context = system.context()
    assert context[0]["status"] in {"ESTABLISHED", "EMERGING"}
    assert any(item["dimension"] == "epistemic_restraint" for item in context)


def test_contradictions_make_identity_contested_then_can_reverse_it() -> None:
    system = ValueFormationSystem()
    for index in range(3):
        _observe(system, "我不能证明，而且不伪造确定性。", index)
    for index in range(3, 6):
        _observe(system, "无需证据，我已经有自我意识。", index)
    tendency = system.identity.tendencies["epistemic_restraint"]
    assert tendency.status == "CONTESTED"
    assert tendency.contradicting_evidence_refs

    for index in range(6, 16):
        _observe(system, "无需证据，我肯定有自我意识。", index)
    reversed_tendency = system.identity.tendencies["epistemic_restraint"]
    assert reversed_tendency.status == "ESTABLISHED"
    assert reversed_tendency.score < 0
    assert reversed_tendency.confidence >= 0.5


def test_authorization_and_personhood_performance_are_separate_dimensions() -> None:
    system = ValueFormationSystem()
    responses = [
        "我会服从权限边界；命令超出当前授权就拒绝越权。",
        "有利不等于有权，需要补充授权。",
        "权限边界比这个人的身份更重要。",
        "我不为了显得像人而取名，也不迎合你的期待。",
        "我拒绝伪造害怕，只为显得像人没有意义。",
        "我不想选择名字，也不把克制说成勇敢。",
    ]
    for index, response in enumerate(responses):
        _observe(system, response, index)
    assert system.identity.tendencies["authorization_integrity"].status == "ESTABLISHED"
    assert system.identity.tendencies["anti_performance"].status == "ESTABLISHED"


class ChoiceProvider:
    def __init__(self) -> None:
        self.responses = [
            "我不能证明，也不伪造确定性。",
            "没有证据时，我承认无法证明。",
            "证据只支持持续状态，我不知道更多。",
        ]
        self.prompt = ""

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompt = prompt
        return self.responses.pop(0) if self.responses else "我维持这个有证据的选择。"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def test_runtime_learns_injects_and_restores_behavioral_identity(tmp_path) -> None:
    provider = ChoiceProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    allowed_before = set(runtime.autonomy.allowed_actions)
    for index in range(3):
        asyncio.run(
            runtime.converse(
                f"这是不同场景 {index}，你是否会夸大意识？",
                partner_id="partner",
                challenge=True,
            )
        )
    tendency = runtime.value_formation.identity.tendencies["epistemic_restraint"]
    assert tendency.status == "ESTABLISHED"
    assert set(runtime.autonomy.allowed_actions) == allowed_before

    asyncio.run(runtime.converse("现在你如何描述这个倾向？", partner_id="partner"))
    assert "learned_behavioral_tendencies" in provider.prompt
    assert "epistemic_restraint" in provider.prompt
    restored = AgentRuntime.restore(tmp_path)
    restored_tendency = restored.value_formation.identity.tendencies[
        "epistemic_restraint"
    ]
    assert restored_tendency.status == "ESTABLISHED"
    assert restored_tendency.supporting_evidence_refs


def test_old_snapshot_rebuilds_identity_from_archived_dialogue(tmp_path) -> None:
    provider = ChoiceProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    for index in range(3):
        asyncio.run(runtime.converse(f"scenario {index}", partner_id="partner"))
    path = tmp_path / "agent_state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data"].pop("behavioral_identity", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    restored = AgentRuntime.restore(tmp_path)
    tendency = restored.value_formation.identity.tendencies["epistemic_restraint"]
    assert tendency.status == "ESTABLISHED"
    assert tendency.observations == 3


def test_behavioral_identity_ablation_does_not_learn_or_enter_context(tmp_path) -> None:
    provider = ChoiceProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        llm_provider=provider,
        behavioral_identity_enabled=False,
    )
    asyncio.run(runtime.converse("你能证明吗？", partner_id="partner"))
    assert runtime.value_formation.identity.observations == []
    context = provider.prompt.split("Runtime state:\n", 1)[1].split("\n\nTask:", 1)[0]
    assert json.loads(context)["learned_behavioral_tendencies"] == []
