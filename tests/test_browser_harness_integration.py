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
        if "Page.captureScreenshot" in script:
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
        ),
    )
    provider = BrowserHarnessProvider(
        policy=kwargs.pop("policy", BrowserPolicy()),
        runtime=runtime,
        runner=runner,
        dns_resolver=_resolver,
        **kwargs,
    )
    return provider, runner


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
    assert runner1.calls[0][1]["env"]["BU_NAME"] == "agent-runtime-chatgpt"
    assert runner2.calls[0][1]["env"]["BU_NAME"] == "agent-runtime-chatgpt"
    assert "target_id = new_tab()" in runner1.calls[0][1]["input"]
    assert 'target_id = "target-1"' in runner2.calls[0][1]["input"]
    state = json.loads(
        (tmp_path / "browser-state" / "chatgpt.json").read_text()
    )
    assert state["target_id"] == "target-1"


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
