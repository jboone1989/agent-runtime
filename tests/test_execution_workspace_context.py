from pathlib import Path

from agent_runtime.runtime import AgentRuntime
from agent_runtime.planning.step import PlanStep


def test_execution_context_uses_injected_workspace_root(tmp_path):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state_dir = Path("state-dir")
    runtime._execution_workspace_root = str(tmp_path / "workspace")
    runtime.goals = []
    snapshot = runtime._execution_context_snapshot(PlanStep(step_id="step", objective="inspect"), None)
    assert snapshot.sections["workspace"]["root"] == str(tmp_path / "workspace")
