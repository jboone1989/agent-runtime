from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations.browser import (
    BROWSER_CLICK,
    BROWSER_INSPECT,
    BROWSER_NAVIGATE,
    BROWSER_PRESS,
    BROWSER_SCREENSHOT,
    BROWSER_TYPE,
    BrowserPolicy,
    PlaywrightBrowserProvider,
    register_browser_provider,
)


class _Locator:
    def __init__(self, page, selector, index=None):
        self.page = page
        self.selector = selector
        self.index = index

    def inner_text(self, timeout=None):
        if self.selector == "body":
            return self.page.body_text
        return "Example"

    def count(self):
        return 1

    def nth(self, index):
        return _Locator(self.page, self.selector, index=index)

    def evaluate(self, script):
        if "tagName" in script:
            return "div"
        if "'value' in el" in script:
            return "draft"
        return "a"

    def get_attribute(self, name):
        values = {
            "href": "https://example.com/next",
            "role": "link",
            "type": None,
            "aria-label": "Example link",
            "placeholder": None,
            "name": None,
        }
        return values.get(name)

    def click(self, timeout=None):
        self.page.clicked.append(self.selector)

    def fill(self, text, timeout=None):
        self.page.filled.append((self.selector, text))

    def press(self, key, timeout=None):
        self.page.pressed.append((self.selector, key))


class _Page:
    def __init__(self):
        self.url = "https://example.com/"
        self.body_text = "hello world"
        self.clicked = []
        self.filled = []
        self.pressed = []
        self.keyboard = SimpleNamespace(
            press=lambda key, timeout=None: self.pressed.append((None, key))
        )

    def goto(self, url, **kwargs):
        self.url = url
        return SimpleNamespace(status=200)

    def title(self):
        return "Example"

    def locator(self, selector):
        return _Locator(self, selector)

    def screenshot(self, **kwargs):
        return b"png"


class _Context:
    def __init__(self):
        self.page = _Page()
        self.routes = []
        self.closed = False

    def route(self, pattern, callback):
        self.routes.append((pattern, callback))

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self):
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        context = _Context()
        self.contexts.append(context)
        return context

    def close(self):
        self.closed = True


def _resolver(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def _provider(**kwargs):
    browser = _Browser()
    provider = PlaywrightBrowserProvider(
        browser_factory=lambda: browser,
        dns_resolver=_resolver,
        **kwargs,
    )
    return provider, browser


def test_manifest_and_inventory_classify_browser_side_effects() -> None:
    provider, _ = _provider()
    inventory = CapabilityInventory()
    registered = register_browser_provider(provider, inventory)

    assert set(registered) == {
        BROWSER_NAVIGATE,
        BROWSER_INSPECT,
        BROWSER_CLICK,
        BROWSER_TYPE,
        BROWSER_PRESS,
        BROWSER_SCREENSHOT,
    }
    assert inventory.get(BROWSER_NAVIGATE).side_effects is SideEffectLevel.READ_ONLY
    assert inventory.get(BROWSER_CLICK).side_effects is SideEffectLevel.EXTERNAL_SIDE_EFFECT
    assert inventory.get(BROWSER_CLICK).entry_point["required_permissions"] == ["browser.interact"]
    provider.close()


def test_navigate_inspect_and_screenshot_are_bounded() -> None:
    provider, browser = _provider(policy=BrowserPolicy(max_text_chars=5, max_elements=2))

    nav = provider.invoke(BROWSER_NAVIGATE, {"session_id": "s1", "url": "https://example.com/a"})
    inspect = provider.invoke(BROWSER_INSPECT, {"session_id": "s1", "max_chars": 5})
    screenshot = provider.invoke(BROWSER_SCREENSHOT, {"session_id": "s1"})

    assert nav["status_code"] == 200
    assert nav["restart_semantics"] == "browser_sessions_are_process_local"
    assert inspect["text"] == "hello"
    assert inspect["text_truncated"] is True
    assert inspect["elements"][0]["name"] == "Example link"
    assert inspect["elements"][0]["value"] == "draft"
    assert screenshot["encoding"] == "base64"
    assert screenshot["bytes"] == 3
    provider.close()
    assert browser.closed is True
    assert browser.contexts[0].closed is True


def test_click_and_type_use_fixed_browser_primitives() -> None:
    provider, browser = _provider()
    provider.invoke(BROWSER_NAVIGATE, {"session_id": "s1", "url": "https://example.com"})

    provider.invoke(BROWSER_CLICK, {"session_id": "s1", "selector": "#go"})
    typed = provider.invoke(
        BROWSER_TYPE,
        {"session_id": "s1", "selector": "#query", "text": "runtime"},
    )
    provider.invoke(
        BROWSER_PRESS,
        {"session_id": "s1", "selector": "#query", "key": "Enter"},
    )
    provider.invoke(
        BROWSER_PRESS,
        {"session_id": "s1", "key": "Escape"},
    )

    page = browser.contexts[0].page
    assert page.clicked == ["#go"]
    assert page.filled == [("#query", "runtime")]
    assert page.pressed == [("#query", "Enter"), (None, "Escape")]
    assert typed["chars_written"] == 7
    provider.close()


def test_application_runtime_enforces_browser_interaction_authority(tmp_path) -> None:
    provider, browser = _provider()
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
    )
    runtime.register_provider(provider)
    runtime.invoke(BROWSER_NAVIGATE, {"url": "https://example.com"})

    with pytest.raises(PermissionError, match="missing capability permissions"):
        runtime.invoke(BROWSER_CLICK, {"selector": "#go"})

    provider2, _ = _provider()
    allowed = ApplicationRuntime.bootstrap(
        tmp_path / "allowed",
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
        allowed_permissions=frozenset({"browser.interact"}),
    )
    allowed.register_provider(provider2)
    allowed.invoke(BROWSER_NAVIGATE, {"url": "https://example.com"})
    result = allowed.invoke(BROWSER_CLICK, {"selector": "#go"})
    assert result.payload["ok"] is True
    provider.close()
    provider2.close()


def test_browser_rejects_private_targets_and_bounds_sessions() -> None:
    provider, _ = _provider(policy=BrowserPolicy(max_sessions=1))
    with pytest.raises(ValueError, match="non-global"):
        provider.invoke(BROWSER_NAVIGATE, {"url": "http://127.0.0.1/"})

    provider.invoke(BROWSER_NAVIGATE, {"session_id": "one", "url": "https://example.com"})
    with pytest.raises(RuntimeError, match="session limit"):
        provider.invoke(BROWSER_NAVIGATE, {"session_id": "two", "url": "https://example.com"})
    provider.close()


def test_browser_route_aborts_request_when_dns_becomes_private() -> None:
    answers = [
        [(2, 1, 6, "", ("93.184.216.34", 443))],
        [(2, 1, 6, "", ("93.184.216.34", 443))],
        [(2, 1, 6, "", ("10.0.0.1", 443))],
    ]
    browser = _Browser()
    provider = PlaywrightBrowserProvider(
        browser_factory=lambda: browser,
        dns_resolver=lambda *args, **kwargs: answers.pop(0),
    )
    provider.invoke(BROWSER_NAVIGATE, {"url": "https://example.com"})
    route_callback = browser.contexts[0].routes[0][1]
    route = SimpleNamespace(aborted=False, continued=False)
    route.abort = lambda: setattr(route, "aborted", True)
    route.continue_ = lambda: setattr(route, "continued", True)

    route_callback(route, SimpleNamespace(url="https://example.com/asset"))

    assert route.aborted is True
    assert route.continued is False
    provider.close()
