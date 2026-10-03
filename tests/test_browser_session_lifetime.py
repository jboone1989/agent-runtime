from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import pytest

SOURCE = Path(__file__).parents[1] / "src/agent_runtime/integrations/browser_account_script.py"


def scope(monkeypatch, tmp_path, account="default"):
    state = {"target": "one", "session": "page-0", "next": 0, "live": {"page-0"}, "calls": []}
    def cdp(method, **params):
        state["calls"].append((method, params))
        if method == "Target.attachToBrowserTarget":
            state["next"] += 1
            sid = "browser-" + str(state["next"])
            state["live"].add(sid)
            return {"sessionId": sid}
        if method == "Target.getBrowserContexts":
            return {"defaultBrowserContextId": "main", "browserContextIds": ["isolated"]}
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"browserContextId": "main" if account == "default" else "isolated"}}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "one", "url": "https://example.test/", "type": "page"}]}
        if method == "Target.detachFromTarget":
            state["live"].remove(params["sessionId"])
            return {}
        if method == "Target.activateTarget":
            return {}
        raise AssertionError(method)
    def switch(target, activate=False):
        state["next"] += 1
        sid = "page-" + str(state["next"])
        state["live"].add(sid)
        state.update(target=target, session=sid)
        return sid
    helpers = ModuleType("browser_harness.helpers")
    helpers._send = lambda req: {"session_id": state["session"]}
    package = ModuleType("browser_harness")
    monkeypatch.setitem(sys.modules, "browser_harness", package)
    monkeypatch.setitem(sys.modules, "browser_harness.helpers", helpers)
    path = tmp_path / "alice.json"
    if account != "default":
        path.write_text('{"browser_binding":"endpoint","context_id":"isolated"}')
    ns = {"cdp": cdp, "switch_tab": switch,
          "current_tab": lambda: {"targetId": state["target"], "url": "https://example.test/"},
          "account_id": account, "account_path": str(path), "browser_binding": "endpoint",
          "storage_source": ""}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)
    return ns, state


def test_repeated_transactions_keep_one_page_session(monkeypatch, tmp_path):
    ns, state = scope(monkeypatch, tmp_path)
    for _ in range(100):
        assert ns["switch_tab"]("one") == "page-0"
    assert state["live"] == {"page-0"}
    assert not any(method == "Target.attachToBrowserTarget" for method, _ in state["calls"])


def test_switch_releases_previous_session_and_checks_account(monkeypatch, tmp_path):
    ns, state = scope(monkeypatch, tmp_path)
    for target in ["two", "one"] * 25:
        ns["switch_tab"](target)
        assert len(state["live"]) == 1
    original = ns["cdp"]
    ns["cdp"] = lambda method, **params: {"targetInfo": {"browserContextId": "other"}} if method == "Target.getTargetInfo" else original(method, **params)
    with pytest.raises(RuntimeError, match="another account"):
        ns["switch_tab"]("foreign")
    assert len(state["live"]) == 1


def test_storage_session_cleanup_even_if_tab_reclaim_fails(monkeypatch, tmp_path):
    ns, state = scope(monkeypatch, tmp_path, account="alice")
    assert len(state["live"]) == 2
    original = ns["cdp"]
    def fail(method, **params):
        if method == "Target.getTargets":
            raise RuntimeError("disconnected")
        return original(method, **params)
    ns["cdp"] = fail
    with pytest.raises(RuntimeError, match="disconnected"):
        ns["release_account_lock"]()
    assert len(state["live"]) == 1
    assert ns["_account_lock"].closed
