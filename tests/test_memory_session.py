"""Recovery and failure semantics at the reusable Runtime boundary."""
import asyncio
import json

from pydantic import BaseModel
import pytest

from agent_runtime.memory.session import (
    BudgetedRuntime, MemoryAction, MemorySession, RetrievalBudget,
    MemoryBudgetExhausted, MemoryContinuationRequired, MemoryRequestBudgetExceeded,
    memory_decision,
)


class Decision(BaseModel):
    answer: str


class Provider:
    def __init__(self):
        self.rows = {"original": {"node_id": "original", "version": "v1",
                     "status": "ACTIVE", "text": "original receipt"}}
        self.reads = 0
        self.contexts = []
        self.failure = None

    def snapshot(self, node_id):
        return dict(self.rows[node_id])

    def search(self, query, **kwargs):
        return list(self.rows.values())

    def read(self, node_id, **kwargs):
        self.reads += 1
        if self.failure:
            raise self.failure
        return self.snapshot(node_id)

    async def contextualize(self, runtime, node_ids):
        self.contexts.append(node_ids)
        if self.failure:
            raise self.failure


def command(action="READ_EVIDENCE", **kwargs):
    return MemoryAction(action=action, ref="M1", working_summary="receipt needed",
                        missing_information="exact evidence", **kwargs)


def make_session(tmp_path, provider=None, budget=None):
    session = MemorySession(tmp_path, "test", provider or Provider(), budget=budget)
    session.seed("receipt")
    return session


def run(session, values, *, max_turns=None, classifier=None, mutate=None, payload=None):
    async def runner(runtime, task, contract, validate=None):
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        validate(value)
        if mutate:
            mutate()
        return value
    return asyncio.run(memory_decision(
        runtime=object(), session=session, instruction="TEST:",
        payload_builder=payload or (lambda: {}), contract_builder=lambda: Decision,
        decision_runner=runner,
        prompt_builder=lambda task, contract: task + json.dumps(contract.model_json_schema()),
        max_turns=max_turns, outcome_classifier=classifier,
    ))


def test_slice_resume_keeps_ref_observation_and_total_budget(tmp_path):
    session = make_session(tmp_path)
    with pytest.raises(MemoryContinuationRequired):
        run(session, [command()], max_turns=1)
    resumed = MemorySession(tmp_path, "test", session.service)
    assert resumed.remaining_rounds == 3
    assert resumed.state["refs"] == {"M1": "original"}
    assert resumed.packet()["tool_result"]["text"] == "original receipt"
    result = run(resumed, [Decision(answer="read")])
    assert run(MemorySession(tmp_path, "test", session.service), []) == result
    assert resumed.state["termination"] == "EVIDENCE_SUFFICIENT"


def test_total_budget_survives_restarts(tmp_path):
    session = make_session(tmp_path, budget=RetrievalBudget(max_decision_rounds=2))
    with pytest.raises(MemoryContinuationRequired):
        run(session, [command()], max_turns=1)
    session = MemorySession(tmp_path, "test", session.service)
    with pytest.raises(MemoryBudgetExhausted):
        run(session, [command()], max_turns=1)
    assert session.state["decision_rounds_used"] == 2
    with pytest.raises(MemoryBudgetExhausted):
        run(MemorySession(tmp_path, "test", session.service), [])
    with pytest.raises(ValueError, match="silently reset"):
        MemorySession(tmp_path, "test", session.service, budget=RetrievalBudget())


def test_pending_tool_is_finished_before_next_model_round(tmp_path):
    session = make_session(tmp_path)
    session.state["decision_rounds_used"] = 1
    session.start_action(command())
    action_id = session.state["pending_action"]["action_id"]
    resumed = MemorySession(tmp_path, "test", session.service)
    assert resumed.state["pending_action"]["action_id"] == action_id
    run(resumed, [Decision(answer="done")])
    assert resumed.service.reads == 1
    assert resumed.state["decision_rounds_used"] == 2
    assert "pending_action" not in resumed.state


def test_failed_context_resumes_without_repeating_read(tmp_path):
    provider = Provider()
    session = make_session(tmp_path, provider)
    session.execute(command())
    provider.failure = RuntimeError("context backend failed")
    with pytest.raises(RuntimeError, match="context backend failed"):
        run(session, [])
    assert session.state["pending_context"] == ["original"]
    assert session.state["termination"] == "PROVIDER_ERROR"
    provider.failure = None
    run(MemorySession(tmp_path, "test", provider), [Decision(answer="done")])
    assert provider.reads == 1


@pytest.mark.parametrize("error", [RuntimeError("offline"), ValueError("bad provider data"), KeyError("db column")])
def test_provider_failure_propagates_and_preserves_pending_intent(tmp_path, error):
    session = make_session(tmp_path)
    session.service.failure = error
    with pytest.raises(type(error)):
        run(session, [command()])
    assert session.state["status"] == "PROVIDER_ERROR"
    assert session.state["pending_action"]["command"]["action"] == "READ_EVIDENCE"
    assert session.state["tool_result"] is None


def test_cached_read_and_decision_invalidated_by_original_revision(tmp_path):
    session = make_session(tmp_path)
    run(session, [command(), Decision(answer="old")])
    session.service.rows["original"].update(version="v2", text="revised receipt")
    resumed = MemorySession(tmp_path, "test", session.service)
    assert resumed.packet()["tool_result"]["needs_refresh"]
    assert "original receipt" not in json.dumps(resumed.packet())
    assert run(resumed, [Decision(answer="new")]).answer == "new"
    assert resumed.state["decision_rounds_used"] == 3


def test_change_during_reasoning_consumes_round_and_requires_reread(tmp_path):
    session = make_session(tmp_path)
    def mutate():
        session.service.rows["original"]["version"] += "x"
    with pytest.raises(MemoryBudgetExhausted):
        run(session, [Decision(answer="obsolete")] * 4, mutate=mutate)
    assert "decision" not in session.state
    assert session.state["decision_rounds_used"] == 4


def test_incomplete_is_explicit_terminal_decision(tmp_path):
    session = make_session(tmp_path)
    run(session, [Decision(answer="unknown")], classifier=lambda value: "EVIDENCE_INCOMPLETE")
    assert session.state["status"] == "DECIDED"
    assert session.state["termination"] == "EVIDENCE_INCOMPLETE"


def test_legacy_checkpoint_keeps_domain_fields_and_discloses_new_allowance(tmp_path):
    state = {"schema": "ferro-memory-session.v1", "phase": "test", "status": "NEEDS_CONTINUATION",
             "refs": {"M1": "original"}, "cards": [], "turns": 12,
             "working_summary": "saved progress", "missing_information": "next fact",
             "tool_result": {"ref": "M1", "text": "historical original"},
             "decision_bindings": {"sources": ["source-1"]},
             "external_searches": [{"receipt": "search.json"}]}
    (tmp_path / "memory_session_test.json").write_text(json.dumps(state))
    session = MemorySession(tmp_path, "test", Provider())
    assert session.state["refs"] == state["refs"]
    assert session.state["decision_bindings"] == state["decision_bindings"]
    assert session.state["external_searches"] == state["external_searches"]
    assert session.state["tool_result"] == state["tool_result"]
    assert session.packet()["tool_result"]["needs_refresh"]
    assert session.state["legacy_tool_turns"] == 12
    assert session.state["budget_origin"] == "LEGACY_CONTINUATION"
    assert session.remaining_rounds == 4


def test_memory_selection_budget_fails_before_model_dispatch(tmp_path):
    session = make_session(tmp_path, budget=RetrievalBudget(memory_context_tokens=20))
    with pytest.raises(MemoryBudgetExhausted, match="MINIMUM_SELECTION"):
        run(session, [])
    assert session.state["decision_rounds_used"] == 0


def test_schema_budget_checked_before_backend_dispatch(tmp_path):
    class Backend:
        async def cognitive_probe(self, *args, **kwargs):
            pytest.fail("oversized schema dispatched")
    with pytest.raises(MemoryRequestBudgetExceeded):
        asyncio.run(BudgetedRuntime(Backend(), tmp_path).cognitive_probe(
            "small", max_tokens=2400, output_schema={"description": "x" * 40000}))
