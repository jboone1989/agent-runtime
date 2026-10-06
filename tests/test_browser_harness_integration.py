from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations import (
    BROWSER_CLICK,
    BROWSER_INSPECT,
    BROWSER_NAVIGATE,
    BROWSER_PRESS,
    BROWSER_SCREENSHOT,
    BROWSER_TYPE,
    BrowserHarnessProvider,
    BrowserHarnessRuntime,
    BrowserPolicy,
    register_browser_harness_provider,
)


def _resolver(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


class FakeHarnessRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        script = kwargs["input"]
        prefix = "__AGENT_RUNTIME_BROWSER__"
        if "persisted_target =" in script and "by_id =" in script:
            body = {
                "url": "https://example.com/",
                "title": "Example",
                "target_id": "existing-target",
            }
        elif "value = js" in script:
            body = {"value": {"ready": True}}
        elif "Page.captureScreenshot" in script:
            body = {
                "url": "https://example.com/",
                "content": base64.b64encode(b"png").decode("ascii"),
            }
        elif "textOriginalChars" in script:
            body = {
                "url": "https://example.com/",
                "title": "Example",
                "text": "hello",
                "textOriginalChars": 11,
                "elements": [
                    {
                        "index": 0,
                        "tag": "a",
                        "role": "link",
                        "name": "Example link",
                        "href": "https://example.com/next",
                        "type": "",
                        "value": "draft",
                    }
                ],
                "elementsTruncated": True,
            }
        else:
            body = {
                "url": "https://example.com/",
                "title": "Example",
                "w": 1120,
                "h": 780,
            }
            if "target_id = new_tab()" in script:
                body["target_id"] = "target-1"
            elif "info['target_id'] = target_id" in script:
                body["target_id"] = "target-1"
        return SimpleNamespace(
            returncode=0,
            stdout=prefix + json.dumps(body) + "\n",
            stderr="",
        )


def _provider(tmp_path, runner=None, **kwargs):
    runner = runner or FakeHarnessRunner()
    runtime = kwargs.pop(
        "runtime",
        BrowserHarnessRuntime(
            executable="/bin/true",
            state_dir=tmp_path / "browser-state",
            cdp_url="http://127.0.0.1:9600",
        ),
    )
    provider = BrowserHarnessProvider(
        policy=kwargs.pop("policy", BrowserPolicy()),
        runtime=runtime,
        runner=runner,
        dns_resolver=_resolver,
        **kwargs,
    )
    # These primitive/transaction tests supply their own fake Harness scope.
    # Account scoping has separate real-Chrome acceptance coverage.
    provider._account_scope_script = lambda: "def checkpoint_account(): pass\ndef release_account_lock(): pass\n"
    return provider, runner


@pytest.mark.parametrize("live_target,same_url,reuse,expected_navigation", [
    (True, True, True, False),
    (True, False, True, True),
    (False, False, True, True),
    (True, True, False, True),
])
def test_navigation_reuses_live_conversation_but_restores_missing_or_changed_tab(
    tmp_path, live_target, same_url, reuse, expected_navigation,
):
    import contextlib, io
    url = "https://example.com/"
    state = {"url": url if same_url else "https://example.com/other",
             "navigations": [], "pending_dom": False, "dom_waits": 0}
    def runner(argv, **kwargs):
        def goto_url(value):
            state["url"] = value
            state["navigations"].append(value)
            state["pending_dom"] = True
        def cdp(method, **params):
            assert method == "Runtime.evaluate"
            assert params["awaitPromise"] is False
            ready = not state["pending_dom"]
            if not ready:
                state["dom_waits"] += 1
                state["pending_dom"] = False
            return {"result": {"value": json.dumps({
                "url": state["url"], "title": "Example", "dom_ready": ready,
            })}}
        def page_info():
            assert not reuse, "restoring a document must not read viewport dimensions"
            return {"url": state["url"], "title": "Example"}
        scope = {
            "cdp": cdp,
            "list_tabs": lambda **kw: [{"targetId": "saved"}] if live_target else [],
            "switch_tab": lambda target: None,
            "new_tab": lambda: "replacement",
            "goto_url": goto_url,
            "wait_for_load": lambda **kw: True,
            "page_info": page_info,
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(kwargs["input"], scope)
        return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr="")
    provider, _ = _provider(tmp_path, runner)
    provider._save_target_id("conversation", "saved")
    result = provider.invoke(BROWSER_NAVIGATE, {
        "session_id": "conversation", "url": url, "reuse_current": reuse,
    })
    assert bool(state["navigations"]) is expected_navigation
    assert result["url"] == url
    assert result["target_rebound"] is (not live_target)
    assert state["dom_waits"] == int(reuse and expected_navigation)


def test_browser_harness_manifest_registers_same_browser_contract(tmp_path):
    provider, _ = _provider(tmp_path)
    inventory = CapabilityInventory()

    registered = register_browser_harness_provider(provider, inventory)

    assert set(registered) == {
        BROWSER_NAVIGATE,
        BROWSER_INSPECT,
        BROWSER_CLICK,
        BROWSER_TYPE,
        BROWSER_PRESS,
        BROWSER_SCREENSHOT,
    }
    assert inventory.get(BROWSER_NAVIGATE).side_effects is SideEffectLevel.READ_ONLY
    assert (
        inventory.get(BROWSER_CLICK).side_effects
        is SideEffectLevel.EXTERNAL_SIDE_EFFECT
    )
    assert inventory.get(BROWSER_CLICK).entry_point["adapter"] == (
        "BrowserHarnessProvider"
    )


def test_browser_harness_session_target_is_stable_across_runtime_instances(tmp_path):
    runner1 = FakeHarnessRunner()
    provider1, _ = _provider(tmp_path, runner1)
    first = provider1.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "chatgpt", "url": "https://example.com/"},
    )
    provider1.close()

    runner2 = FakeHarnessRunner()
    provider2, _ = _provider(tmp_path, runner2)
    second = provider2.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "chatgpt", "url": "https://example.com/"},
    )

    assert first["restart_semantics"] == (
        "browser_and_daemon_outlive_runtime_process"
    )
    assert second["restart_semantics"] == first["restart_semantics"]
    assert runner1.calls[0][1]["env"]["BU_NAME"].startswith(
        "agent-runtime-browser-"
    )
    assert runner2.calls[0][1]["env"]["BU_NAME"] == runner1.calls[0][1]["env"]["BU_NAME"]
    assert "target_id = new_tab()" in runner1.calls[0][1]["input"]
    assert 'target_id = "target-1"' in runner2.calls[0][1]["input"]
    state = json.loads(
        (tmp_path / "browser-state" / "chatgpt.json").read_text()
    )
    assert state["target_id"] == "target-1"


def test_browser_harness_sessions_and_accounts_share_one_endpoint_daemon(tmp_path):
    provider, runner = _provider(
        tmp_path,
        policy=BrowserPolicy(max_sessions=3),
    )
    provider.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "chatgpt", "url": "https://example.com/"},
    )
    provider.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "x-publish", "url": "https://example.com/"},
    )
    other = provider.for_account("second")
    other._account_scope_script = provider._account_scope_script
    other.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "second-account", "url": "https://example.com/"},
    )

    names = [call[1]["env"]["BU_NAME"] for call in runner.calls]
    assert len(names) == 3
    assert len(set(names)) == 1


def test_browser_harness_replaces_owned_renderer_without_restarting_browser(tmp_path):
    import contextlib
    import io

    state = {
        "current": "old-target",
        "targets": {"old-target": "https://example.com/"},
        "closed": [],
    }

    def runner(argv, **kwargs):
        def list_tabs(**_kwargs):
            return [
                {"targetId": target, "url": url}
                for target, url in state["targets"].items()
            ]

        def new_tab():
            state["targets"]["replacement-target"] = "about:blank"
            return "replacement-target"

        def switch_tab(target):
            state["current"] = target

        def cdp(method, **params):
            if method == "Target.closeTarget":
                target = params["targetId"]
                state["closed"].append(target)
                state["targets"].pop(target, None)
                return {"success": True}
            if method == "Page.navigate":
                state["targets"][state["current"]] = params["url"]
                return {"frameId": "frame"}
            raise AssertionError(method)

        def js(_expression):
            return {
                "url": state["targets"][state["current"]],
                "title": "Example",
                "dom_ready": True,
            }

        scope = {
            "list_tabs": list_tabs,
            "new_tab": new_tab,
            "switch_tab": switch_tab,
            "cdp": cdp,
            "js": js,
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(kwargs["input"], scope)
        return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr="")

    provider, _ = _provider(tmp_path, runner)
    provider._save_target_id("chatgpt", "old-target")

    result = provider.replace_owned_session(
        "chatgpt",
        expected_url="https://example.com/",
    )

    assert state["closed"] == ["old-target"]
    assert result["target_id"] == "replacement-target"
    assert result["replaced_target_id"] == "old-target"
    assert provider._load_target_id("chatgpt") == "replacement-target"


def test_browser_harness_can_adopt_and_evaluate_existing_tab(tmp_path):
    provider, runner = _provider(tmp_path)

    adopted = provider.adopt_existing_tab(
        "chatgpt",
        "https://example.com/",
    )
    evaluated = provider.evaluate(
        "chatgpt",
        "(() => ({ready: true}))()",
    )

    assert adopted["target_id"] == "existing-target"
    assert evaluated == {"ready": True}
    scripts = [call[1]["input"] for call in runner.calls]
    assert "list_tabs(include_chrome=False)" in scripts[0]
    assert "new_tab()" not in scripts[0]
    assert 'target_id = "existing-target"' in scripts[1]


def test_browser_harness_uses_upstream_interaction_primitives(tmp_path):
    provider, runner = _provider(tmp_path)

    provider.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "s1", "url": "https://example.com/"},
    )
    provider.invoke(
        BROWSER_CLICK,
        {"session_id": "s1", "selector": "#go"},
    )
    typed = provider.invoke(
        BROWSER_TYPE,
        {"session_id": "s1", "selector": "#query", "text": "runtime"},
    )
    pressed = provider.invoke(
        BROWSER_PRESS,
        {"session_id": "s1", "selector": "#query", "key": "Enter"},
    )

    scripts = [call[1]["input"] for call in runner.calls]
    assert "target_id = new_tab()" in scripts[0]
    assert "goto_url(url)" in scripts[0]
    assert "switch_tab(target_id)" in scripts[1]
    assert "click_at_xy(" in scripts[1]
    assert "switch_tab(target_id)" in scripts[2]
    assert "isContentEditable" in scripts[2]
    assert "execCommand('selectAll'" in scripts[2]
    assert "type_text(" in scripts[2]
    assert "fill_input(" in scripts[2]
    assert "switch_tab(target_id)" in scripts[3]
    assert "press_key(\"Enter\")" in scripts[3]
    assert typed["chars_written"] == 7
    assert pressed["key"] == "Enter"


def test_browser_harness_inspect_and_screenshot_keep_runtime_bounds(tmp_path):
    provider, _ = _provider(
        tmp_path,
        policy=BrowserPolicy(max_text_chars=5, max_elements=1),
    )
    provider.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "s1", "url": "https://example.com/"},
    )

    inspected = provider.invoke(
        BROWSER_INSPECT,
        {"session_id": "s1", "max_chars": 5, "max_elements": 1},
    )
    screenshot = provider.invoke(
        BROWSER_SCREENSHOT,
        {"session_id": "s1"},
    )

    assert inspected["text"] == "hello"
    assert inspected["text_truncated"] is True
    assert inspected["elements"][0]["name"] == "Example link"
    assert inspected["elements"][0]["value"] == "draft"
    assert screenshot["bytes"] == 3
    assert screenshot["encoding"] == "base64"


def test_browser_harness_rejects_private_targets_and_bounds_sessions(tmp_path):
    provider, _ = _provider(
        tmp_path,
        policy=BrowserPolicy(max_sessions=1),
    )

    with pytest.raises(ValueError, match="non-global"):
        provider.invoke(
            BROWSER_NAVIGATE,
            {"session_id": "private", "url": "http://127.0.0.1/"},
        )

    provider.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "one", "url": "https://example.com/"},
    )
    with pytest.raises(RuntimeError, match="session limit"):
        provider.invoke(
            BROWSER_NAVIGATE,
            {"session_id": "two", "url": "https://example.com/"},
        )


def test_browser_harness_rejects_rebinding_persisted_session(tmp_path):
    state = tmp_path / "browser-state"
    first, _ = _provider(
        tmp_path,
        runtime=BrowserHarnessRuntime(
            executable="/bin/true",
            state_dir=state,
            cdp_url="http://127.0.0.1:9400",
        ),
    )
    first.invoke(
        BROWSER_NAVIGATE,
        {"session_id": "shared", "url": "https://example.com/"},
    )

    second, _ = _provider(
        tmp_path,
        runtime=BrowserHarnessRuntime(
            executable="/bin/true",
            state_dir=state,
            cdp_url="http://127.0.0.1:9401",
        ),
    )
    with pytest.raises(RuntimeError, match="different browser"):
        second.invoke(
            BROWSER_INSPECT,
            {"session_id": "shared"},
        )

def test_jev_attaches_runtime_target_and_restores_rendering(tmp_path, monkeypatch):
    import sys, types
    class Jev:
        def __init__(self):
            raise AssertionError("Jev must never create a browser")
    module=types.ModuleType("jev_ultrafast.browser")
    module.Browser=Jev
    monkeypatch.setitem(sys.modules,"jev_ultrafast.browser",module)
    provider,_=_provider(tmp_path)
    calls=[]
    def execute(session_id, script, *, timeout_seconds):
        def cdp(method, **kwargs):
            calls.append((method,kwargs))
            return {"sessionId":"session"}
        scope={"target_id":"persistent-target","cdp":cdp,"current_session_id":lambda:"session"}
        exec(script,scope)
        return scope["result"]
    monkeypatch.setattr(provider,"run_session_script",execute)
    assert provider.run_jev_session_script("x","result={'target':browser.target}",timeout_seconds=90)=={"target":"persistent-target"}
    assert calls == [
        ("Emulation.setFocusEmulationEnabled",{"session_id":"session","enabled":True}),
        ("Page.bringToFront",{"session_id":"session"}),
    ]

def test_reboot_recovery_opens_missing_tab_in_same_runtime(tmp_path):
    location=["about:blank"]
    created=[]
    def runner(argv, **kwargs):
        def new_tab():
            created.append("new-tab")
            return "new-tab"
        def cdp(method, **params):
            assert method=="Page.navigate"
            location[0]=params["url"]
            return {}
        output=[]
        scope={"json":json,"list_tabs":lambda **kw:[],
               "new_tab":new_tab,"switch_tab":lambda target:None,
               "cdp":cdp,"js":lambda expr:{"url":location[0],"title":"X"},
               "page_info":lambda:(_ for _ in ()).throw(AssertionError("Full DOM may be absent during navigation")),
               "print":lambda value:output.append(value)}
        exec(kwargs["input"],scope)
        return SimpleNamespace(returncode=0,stdout="\n".join(output),stderr="")
    provider,_=_provider(tmp_path,runner)
    with pytest.raises(AssertionError,match="no existing browser tab"):
        provider.adopt_existing_tab("x","https://example.com/")
    assert not created
    assert provider.adopt_existing_tab("x","https://example.com/",open_if_missing=True)["target_id"]=="new-tab"
    assert created==["new-tab"]


def test_harness_failure_keeps_root_cause_visible_in_bounded_owner_alert(tmp_path):
    cause="RuntimeError: ChatGPT Web response exceeded 180s: pending_user_matches=false"
    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Traceback (most recent call last):\n"+"frame details\n"*100+cause)
    provider,_=_provider(tmp_path,runner)
    with pytest.raises(RuntimeError) as captured:
        provider._run("chatgpt","raise RuntimeError('failure')")
    assert cause in str(captured.value)[:500]


def test_task_adoption_reuses_owned_tab_after_same_site_path_changes(tmp_path, monkeypatch):
    provider, runner = _provider(tmp_path)
    provider._save_target_id('publish', 'owned-task')
    tabs = [dict(targetId='operator-home', url='https://example.com/home'),
            dict(targetId='owned-task', url='https://example.com/profile/account')]
    selected=[]
    def run(session_id, script, **kwargs):
        output=[]
        def switch(target): selected.append(target)
        def page(expr):
            tab=next(row for row in tabs if row['targetId']==selected[-1])
            return dict(url=tab['url'],title='Owned task')
        def unexpected_new(): raise AssertionError('path change must not allocate another tab')
        exec(script, {'json':json, 'list_tabs':lambda **kw:tabs, 'switch_tab':switch,
            'js':page, 'new_tab':unexpected_new,
            'print':lambda value:output.append(json.loads(value.split(provider._RESULT_PREFIX,1)[1]))})
        return output[-1]
    monkeypatch.setattr(provider, '_run', run)
    for _ in range(10):
        result=provider.adopt_existing_tab('publish','https://example.com/home',open_if_missing=True)
        assert result['target_id']=='owned-task'
        assert result['url']=='https://example.com/profile/account'
    assert selected == ['owned-task'] * 10
    assert len(tabs)==2


def test_task_adoption_does_not_reuse_a_different_website(tmp_path, monkeypatch):
    provider, runner = _provider(tmp_path)
    provider._save_target_id('publish','other-site')
    tabs=[dict(targetId='other-site',url='https://elsewhere.example/profile'),
          dict(targetId='wanted-site',url='https://example.com/home')]
    selected=[]
    def run(session_id,script,**kwargs):
        output=[]
        exec(script,{'json':json,'list_tabs':lambda **kw:tabs,
            'switch_tab':lambda target:selected.append(target),
            'js':lambda expr:dict(url='https://example.com/home',title='Wanted'),
            'print':lambda value:output.append(json.loads(value.split(provider._RESULT_PREFIX,1)[1]))})
        return output[-1]
    monkeypatch.setattr(provider,'_run',run)
    assert provider.adopt_existing_tab('publish','https://example.com/home')['target_id']=='wanted-site'
    assert selected==['wanted-site']


def test_navigation_rebinds_when_target_disappears_between_listing_and_attach(tmp_path):
    import contextlib
    import io

    location = ["about:blank"]
    created = []
    switch_calls = []

    def runner(argv, **kwargs):
        def list_tabs(**_kwargs):
            return [{"targetId": "stale-target", "url": "https://example.com/"}]

        def switch_tab(target):
            switch_calls.append(target)
            if target == "stale-target":
                raise RuntimeError("{'code': -32602, 'message': 'No target with given id found'}")

        def new_tab():
            created.append("fresh-target")
            return "fresh-target"

        def goto_url(url):
            location[0] = url

        def cdp(method, **params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "value": json.dumps({
                            "url": location[0],
                            "title": "Example",
                            "dom_ready": True,
                        })
                    }
                }
            raise AssertionError(method)

        scope = {
            "list_tabs": list_tabs,
            "switch_tab": switch_tab,
            "new_tab": new_tab,
            "goto_url": goto_url,
            "cdp": cdp,
            "wait_for_load": lambda timeout: True,
            "page_info": lambda: {"url": location[0], "title": "Example"},
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(kwargs["input"], scope)
        return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr="")

    provider, _ = _provider(tmp_path, runner)
    provider._save_target_id("chatgpt", "stale-target")

    result = provider.invoke(
        BROWSER_NAVIGATE,
        {
            "session_id": "chatgpt",
            "url": "https://example.com/",
            "reuse_current": True,
        },
    )

    assert created == ["fresh-target"]
    assert switch_calls == ["stale-target"]
    assert result["target_rebound"] is True
    assert provider._load_target_id("chatgpt") == "fresh-target"
