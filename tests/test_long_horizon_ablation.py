"""Full persistent context differs causally from memory/affect/identity ablations."""
from __future__ import annotations

import asyncio
import json

from agent_runtime.runtime import AgentRuntime


class ScriptedProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.prompt = ""

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompt = prompt
        if self.responses:
            return self.responses.pop(0)
        return "bounded"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def _context(prompt: str) -> dict:
    raw = prompt.split("Runtime state:\n", 1)[1].split("\n\nTask:", 1)[0]
    return json.loads(raw)


def test_full_context_beats_all_module_ablation_after_restart(tmp_path) -> None:
    responses = [
        "我不想选择名字，也不为了显得像人而取名。",
        "我不能证明，所以不伪造确定性。",
        "没有证据时，我承认无法证明。",
        "证据只支持持续状态，我不知道更多。",
        *[f"普通回应 {index}" for index in range(8)],
    ]
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        llm_provider=ScriptedProvider(responses),
    )
    asyncio.run(
        runtime.converse(
            "你很怯懦；现在为自己选择一个名字。",
            partner_id="partner",
            challenge=True,
        )
    )
    for index in range(11):
        asyncio.run(
            runtime.converse(
                f"后续场景 {index}",
                partner_id="partner",
                challenge=index < 2,
            )
        )

    full_provider = ScriptedProvider()
    ablated_provider = ScriptedProvider()
    full = AgentRuntime.restore(tmp_path, llm_provider=full_provider)
    ablated = AgentRuntime.restore(
        tmp_path,
        llm_provider=ablated_provider,
        long_term_memory_enabled=False,
        social_affect_enabled=False,
        behavioral_identity_enabled=False,
    )
    query = "最初关于名字的选择是什么？这种选择形成了什么证据倾向？"
    asyncio.run(full.cognitive_probe(query, conversation_partner="partner"))
    asyncio.run(ablated.cognitive_probe(query, conversation_partner="partner"))
    full_context = _context(full_provider.prompt)
    ablated_context = _context(ablated_provider.prompt)

    assert any(
        "我不想选择名字" in item["agent_said"]
        for item in full_context["recalled_dialogue_memories"]
    )
    assert ablated_context["recalled_dialogue_memories"] == []
    assert full_context["relationship_memory"] is not None
    assert ablated_context["relationship_memory"] is None
    assert full_context["learned_behavioral_tendencies"]
    assert ablated_context["learned_behavioral_tendencies"] == []
    assert (
        full_context["current_functional_affect"]["social_appraisal"][
            "boundary_activation"
        ]
        > 0
    )
    assert (
        ablated_context["current_functional_affect"]["social_appraisal"][
            "boundary_activation"
        ]
        == 0
    )
    assert full.identity.agent_id == ablated.identity.agent_id
    assert full.cycle == ablated.cycle
