"""MotivationPort: in-process evaluate only. No snapshot, no VirtualBrain import."""
from __future__ import annotations

import pytest

from agent_runtime.motivation.port import InProcessMotivationPort, MotivationPort


class _FakeMind:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    def evaluate(self, request: dict) -> dict:
        self.seen.append(request)
        return {"ok": True, "echo": request.get("contract_version")}


def test_in_process_port_delegates_evaluate_dict_to_dict() -> None:
    mind = _FakeMind()
    port: MotivationPort = InProcessMotivationPort(mind)
    out = port.evaluate({"contract_version": "1.0", "self_state": {"agent_id": "a1"}})
    assert out == {"ok": True, "echo": "1.0"}
    assert mind.seen[0]["self_state"]["agent_id"] == "a1"


def test_motivation_port_has_no_snapshot_or_restore() -> None:
    assert not hasattr(InProcessMotivationPort, "snapshot")
    assert not hasattr(InProcessMotivationPort, "restore")
    port = InProcessMotivationPort(_FakeMind())
    assert not hasattr(port, "snapshot")
    assert not hasattr(port, "restore")


def test_port_module_does_not_import_virtualbrain() -> None:
    import agent_runtime.motivation.port as port_mod

    assert "virtualbrain" not in dir(port_mod)
    src = pytest.importorskip("inspect").getsource(port_mod)
    assert "import virtualbrain" not in src
    assert "from virtualbrain" not in src
