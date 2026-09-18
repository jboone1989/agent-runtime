from agent_runtime.llm.reputation import (
    ModelReputation,
    ModelReputationBook,
    canonical_model_name,
)


def test_canonical_model_name_matches_provider_variants():
    assert canonical_model_name("deepseek-ai/deepseek-v4-flash-0731:free") == "deepseek-v4-flash-0731"
    assert canonical_model_name("z-ai/glm-5.3-20260816") == "glm-5.3"
    assert canonical_model_name("moonshotai/kimi-k3:free") == "kimi-k3"


def test_reputation_rank_is_provider_neutral():
    book = ModelReputationBook([
        ModelReputation(
            canonical_name="gpt-oss-120b",
            overall=0.82,
            coding=0.91,
            agentic=0.86,
            human_preference=0.78,
            popularity=0.75,
            confidence=1.0,
            source_refs=("artificial-analysis:today",),
        ),
        ModelReputation(
            canonical_name="llama3.1-8b",
            overall=0.45,
            coding=0.41,
            agentic=0.38,
            human_preference=0.50,
            popularity=0.65,
            confidence=1.0,
            source_refs=("artificial-analysis:today",),
        ),
    ])
    nvidia = book.rank(
        ["openai/gpt-oss-120b", "meta/llama3.1-8b"],
        task_class="code_audit",
    )
    cerebras = book.rank(
        ["gpt-oss-120b", "llama3.1-8b"],
        task_class="code_audit",
    )
    assert nvidia[0] == "openai/gpt-oss-120b"
    assert cerebras[0] == "gpt-oss-120b"


def test_task_specific_reputation_can_change_order():
    book = ModelReputationBook([
        ModelReputation(
            canonical_name="model-a",
            overall=0.90,
            coding=0.55,
            agentic=0.65,
            human_preference=0.90,
            popularity=0.80,
            confidence=1.0,
        ),
        ModelReputation(
            canonical_name="model-b",
            overall=0.78,
            coding=0.96,
            agentic=0.86,
            human_preference=0.70,
            popularity=0.70,
            confidence=1.0,
        ),
    ])
    assert book.rank(["model-a", "model-b"], task_class="general_text")[0] == "model-a"
    assert book.rank(["model-a", "model-b"], task_class="code_audit")[0] == "model-b"


def test_unknown_model_stays_neutral_instead_of_getting_name_bonus():
    book = ModelReputationBook([])
    assert book.score("some-vendor/ultra-super-max-999b", task_class="self_upgrade") == 0.5
