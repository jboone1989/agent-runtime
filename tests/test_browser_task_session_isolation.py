"""Interleaved clients must never depend on a daemon-wide current page."""
from pathlib import Path
import sys
from types import ModuleType


class Daemon:
    def __init__(self):
        self.session = "external-x-session"
        self.counter = 0
        self.sessions = {"external-x-session":"x"}
        self.detached = []
        self.requests = []
    def send(self, req, **kwargs):
        self.requests.append(req)
        method, params = req.get("method"), req.get("params", {})
        if req.get("meta") == "session": return {"session_id":self.session}
        if req.get("meta") == "set_session":
            self.session = req["session_id"]
            return {}
        if method == "Target.getBrowserContexts":
            return {"result":{"defaultBrowserContextId":"default"}}
        if method == "Target.getTargetInfo":
            target = params["targetId"]
            return {"result":{"targetInfo":{"targetId":target,"browserContextId":"default","url":"https://"+target}}}
        if method == "Target.attachToTarget":
            self.counter += 1
            session = "client-"+str(self.counter)
            self.sessions[session] = params["targetId"]
            return {"result":{"sessionId":session}}
        if method == "Target.detachFromTarget":
            self.detached.append(params["sessionId"])
            return {"result":{}}
        if method == "Runtime.evaluate":
            target = self.sessions[req.get("session_id") or self.session]
            return {"result":{"result":{"value":target}}}
        return {"result":{}}


def client(monkeypatch, daemon):
    # Each Browser Harness client is a separate process with its own helpers.
    helpers = ModuleType("browser_harness.helpers")
    helpers._send = daemon.send
    monkeypatch.setitem(sys.modules, "browser_harness.helpers", helpers)
    package = ModuleType("browser_harness")
    package.helpers = helpers
    monkeypatch.setitem(sys.modules, "browser_harness", package)
    def cdp(method, session_id=None, **params):
        return helpers._send({"method":method,"params":params,"session_id":session_id}).get("result",{})
    env = {"cdp":cdp,"switch_tab":lambda *a:None,"account_id":"default",
           "current_tab":lambda: {"targetId":"daemon-global-page"}}
    source = Path(__file__).parents[1]/"src/agent_runtime/integrations/browser_account_script.py"
    exec(compile(source.read_text(),str(source),"exec"),env)
    return env, helpers


def test_interleaved_clients_pin_page_reads_and_release_only_their_attachments(monkeypatch):
    daemon = Daemon()
    chat, chat_helpers = client(monkeypatch, daemon)
    x, x_helpers = client(monkeypatch, daemon)
    chat_session = chat["switch_tab"]("chatgpt")
    x_session = x["switch_tab"]("x")
    assert chat_session != x_session
    assert chat["current_session_id"]() == chat_session
    assert x["current_session_id"]() == x_session
    for helpers, target in ((chat_helpers,"chatgpt"),(x_helpers,"x"),(chat_helpers,"chatgpt")):
        response = helpers._send({"method":"Runtime.evaluate","params":{},"session_id":None})
        assert response["result"]["result"]["value"] == target
    assert daemon.session == "external-x-session"
    assert daemon.detached == []
    chat["release_account_lock"]()
    assert daemon.detached == [chat_session]
    assert x_helpers._send({"method":"Runtime.evaluate","params":{}})["result"]["result"]["value"] == "x"
    x["release_account_lock"]()
    assert daemon.detached == [chat_session,x_session]

def test_detach_ipc_timeout_does_not_mask_completed_transaction(monkeypatch, capsys):
    daemon = Daemon()
    task, _ = client(monkeypatch, daemon)
    session = task["switch_tab"]("chatgpt")
    old_cdp = task["cdp"]

    def stalled_detach(method, **params):
        if method == "Target.detachFromTarget":
            raise RuntimeError(
                "Target.detachFromTarget timed out after 5s waiting for the daemon"
            )
        return old_cdp(method, **params)

    task["cdp"] = stalled_detach
    task["release_account_lock"]()
    warning = capsys.readouterr().err
    assert "BROWSER_ACCOUNT_CLEANUP_TIMEOUT" in warning
    assert session in daemon.sessions


def test_unexpected_detach_error_is_still_raised(monkeypatch):
    import pytest
    daemon = Daemon()
    task, _ = client(monkeypatch, daemon)
    task["switch_tab"]("chatgpt")
    old_cdp = task["cdp"]

    def broken_detach(method, **params):
        if method == "Target.detachFromTarget":
            raise RuntimeError("Unexpected session ownership failure")
        return old_cdp(method, **params)

    task["cdp"] = broken_detach
    with pytest.raises(RuntimeError, match="Unexpected session ownership failure"):
        task["release_account_lock"]()
    assert "external-x-session" not in daemon.detached
