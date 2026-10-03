"""Persistent Computer Use backed by the open-source browser-harness project.

Agent Runtime owns the capability contract, authority checks, policy, evidence,
and stable session binding. Browser Harness owns CDP connection lifecycle, tab
attachment, and real browser interaction primitives.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from typing import Any
from urllib.parse import urlsplit

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)
from .browser import (
    BROWSER_CLICK,
    BROWSER_INSPECT,
    BROWSER_NAVIGATE,
    BROWSER_PRESS,
    BROWSER_SCREENSHOT,
    BROWSER_TYPE,
    BrowserPolicy,
)


_CAPABILITIES = (
    BROWSER_NAVIGATE,
    BROWSER_INSPECT,
    BROWSER_CLICK,
    BROWSER_TYPE,
    BROWSER_PRESS,
    BROWSER_SCREENSHOT,
)
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class BrowserHarnessRuntime:
    executable: str = "browser-harness"
    runtime_dir: Path | None = None
    state_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "AGENT_RUNTIME_BROWSER_STATE",
                "~/.local/state/agent-runtime/browser-harness",
            )
        ).expanduser()
    )
    cdp_url: str | None = None
    cdp_ws: str | None = None

    def __post_init__(self) -> None:
        if self.cdp_url and self.cdp_ws:
            raise ValueError("configure only one of cdp_url or cdp_ws")
        if self.cdp_url:
            parsed = urlsplit(self.cdp_url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.port is None
            ):
                raise ValueError(
                    "cdp_url must be a loopback HTTP DevTools endpoint"
                )
        if self.cdp_ws:
            parsed = urlsplit(self.cdp_ws)
            if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
                raise ValueError(
                    "cdp_ws must be an absolute WebSocket endpoint"
                )


class BrowserHarnessProvider:
    """Persistent real-browser provider using Browser Harness daemons.

    Browser/daemon lifetime is deliberately outside this provider. Closing or
    restarting Agent Runtime therefore does not destroy authenticated browser
    state. A Runtime session ID is durably bound to an exact CDP target ID.
    """

    provider_name = "browser-harness"
    contract_version = "runtime-browser:v2"
    _RESULT_PREFIX = "__AGENT_RUNTIME_BROWSER__"

    def __init__(
        self,
        *,
        policy: BrowserPolicy | None = None,
        runtime: BrowserHarnessRuntime | None = None,
        runner: Callable[..., Any] | None = None,
        dns_resolver: Callable[..., Any] = socket.getaddrinfo,
    ) -> None:
        self.policy = policy or BrowserPolicy()
        self.runtime = runtime or BrowserHarnessRuntime(
            executable=os.environ.get(
                "AGENT_RUNTIME_BROWSER_HARNESS",
                "browser-harness",
            )
        )
        self._runner = runner or subprocess.run
        self._dns_resolver = dns_resolver
        self._sessions: set[str] = set()
        self._closed = False

    def manifest(self) -> dict[str, Any]:
        rows = []
        for capability_id in _CAPABILITIES:
            interactive = capability_id in {BROWSER_CLICK, BROWSER_TYPE, BROWSER_PRESS}
            rows.append(
                {
                    "capability_id": capability_id,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": (
                        "EXTERNAL_SIDE_EFFECT"
                        if interactive
                        else "READ_ONLY"
                    ),
                    "required_permissions": (
                        ["browser.interact"] if interactive else []
                    ),
                }
            )
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": rows,
        }

    def readiness(self) -> dict[str, Any]:
        executable = self._resolve_executable()
        return {
            "provider": self.provider_name,
            "status": "READY" if executable else "NOT_INSTALLED",
            "executable": executable or self.runtime.executable,
            "restart_semantics": (
                "browser_and_daemon_outlive_runtime_process"
            ),
        }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("browser provider is closed")
        request = dict(payload or {})
        if capability_id == BROWSER_NAVIGATE:
            return self._navigate(request)
        if capability_id == BROWSER_INSPECT:
            return self._inspect(request)
        if capability_id == BROWSER_CLICK:
            return self._click(request)
        if capability_id == BROWSER_TYPE:
            return self._type(request)
        if capability_id == BROWSER_PRESS:
            return self._press(request)
        if capability_id == BROWSER_SCREENSHOT:
            return self._screenshot(request)
        raise ValueError(
            f"unsupported browser capability: {capability_id}"
        )

    def close(self) -> None:
        # Do not stop Browser Harness daemons or Chrome here. Runtime shutdown
        # must not destroy logged-in browser state.
        self._closed = True

    def evaluate(
        self,
        session_id: str,
        expression: str,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Evaluate bounded page JavaScript on an existing durable session.

        This is an internal provider primitive for higher-level Runtime
        integrations (for example a web-backed LLM provider). It deliberately
        is not registered as a generic agent capability: Browser Harness keeps
        ownership of CDP mechanics while integrations can inspect the DOM they
        specifically understand.
        """
        if self._closed:
            raise RuntimeError("browser provider is closed")
        sid = _session_id(session_id)
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("browser expression must be non-empty")
        if len(expression) > 65536:
            raise ValueError("browser expression exceeds 64 KiB")
        body = self._run(
            sid,
            self._attach_target_script(sid)
            + f"value = js({json.dumps(expression)})\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps({'value': value}, ensure_ascii=False))\n"
            ),
            timeout_seconds=timeout_seconds,
        )
        return body.get("value")

    def adopt_existing_tab(
        self,
        session_id: str,
        url_prefix: str,
        *,
        open_if_missing: bool = False,
    ) -> dict[str, Any]:
        """Bind a Runtime session to an already-open browser tab.

        Prefer an exact URL match, then the first tab under the requested
        prefix. Existing tabs are adopted without navigation. When explicitly
        requested, a missing tab is opened in the same Runtime-owned browser.
        """
        if self._closed:
            raise RuntimeError("browser provider is closed")
        sid = _session_id(session_id)
        prefix = _validate_url(
            url_prefix,
            self.policy,
            self._dns_resolver,
        )
        persisted_target = self._load_target_id(sid)
        body = self._run(
            sid,
            f"prefix = {json.dumps(prefix)}\n"
            + f"persisted_target = {persisted_target!r}\n"
            + "tabs = list_tabs(include_chrome=False)\n"
            + "by_id = {str(row.get('targetId') or row.get('target_id') or ''): row for row in tabs}\n"
            + "exact = [row for row in tabs if row.get('url') == prefix]\n"
            + "matches = [row for row in tabs if str(row.get('url') or '').startswith(prefix)]\n"
            + "bound = by_id.get(persisted_target)\n"
            + "candidates = ([bound] if bound and str(bound.get('url') or '').startswith(prefix) else []) or exact or matches\n"
            + (
                "if not candidates:\n"
            + "    new_target = new_tab()\n"
            + "    switch_tab(new_target)\n"
            + "    import time\n"
            + "    try:\n"
            + "        cdp('Page.navigate', url=prefix, _response_timeout=20.0)\n"
            + "    except TimeoutError:\n"
            + "        pass\n"
            + "    deadline = time.monotonic() + 20.0\n"
            + "    while time.monotonic() < deadline:\n"
            + "        info = js('({url:location.href,title:document.title})')\n"
            + "        if str(info.get('url') or '').startswith(prefix):\n"
            + "            break\n"
            + "        time.sleep(0.15)\n"
            + "    else:\n"
            + "        raise RuntimeError('new Runtime tab did not reach requested URL')\n"
            + "    candidates = [{'targetId':new_target,'url':prefix}]\n"
                if open_if_missing else ""
            )
            + "assert candidates, 'no existing browser tab matches requested URL'\n"
            + "target = candidates[0]\n"
            + "target_id = target.get('targetId') or target.get('target_id')\n"
            + "assert target_id, 'browser tab has no target id'\n"
            + "switch_tab(target_id)\n"
            + "info = js('({url:location.href,title:document.title})')\n"
            + "info['target_id'] = target_id\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(info, ensure_ascii=False))\n"
            ),
        )
        target_id = str(body.get("target_id") or "")
        if not target_id:
            raise RuntimeError("browser-harness did not return a target id")
        self._save_target_id(sid, target_id)
        return {
            "ok": True,
            "provider": self.provider_name,
            "session_id": sid,
            "url": _validate_url(
                str(body.get("url") or prefix),
                self.policy,
                self._dns_resolver,
            ),
            "title": _bounded_text(body.get("title"), 1000),
            "target_id": target_id,
        }

    def run_session_script(
        self,
        session_id: str,
        script: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Run one bounded integration transaction on a durable browser tab.

        Higher-level Runtime integrations use this to keep a multi-step web
        operation on one Browser Harness/CDP attachment. The script surface is
        intentionally internal and is never registered as an agent capability.
        """
        if self._closed:
            raise RuntimeError("browser provider is closed")
        sid = _session_id(session_id)
        if not isinstance(script, str) or not script.strip():
            raise ValueError("browser transaction script must be non-empty")
        if len(script) > 131072:
            raise ValueError("browser transaction script exceeds 128 KiB")
        timeout = float(timeout_seconds)
        if not 1 <= timeout <= 900:
            raise ValueError("browser transaction timeout must be in [1, 900]")
        return self._run(
            sid,
            self._attach_target_script(sid)
            + script
            + "\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(result, ensure_ascii=False))\n"
            ),
            timeout_seconds=timeout,
        )

    def run_jev_session_script(
        self,
        session_id: str,
        script: str,
        *,
        timeout_seconds: float,
        package_root: Path | None = None,
    ) -> dict[str, Any]:
        """Execute Jev observed actions on the existing durable Runtime tab.

        Jev owns observation and node-bound actions. Runtime owns the browser,
        profile, target binding and subprocess lifetime; Jev must not create or
        close a Chrome/tab here. package_root supports a caller-installed Jev
        package when Browser Harness uses a separate Python environment.
        """
        prefix = "import sys\n"
        if package_root is not None:
            root = Path(package_root).resolve(strict=True)
            if not (root / "jev_ultrafast" / "browser.py").is_file():
                raise ValueError("Jev package root does not contain browser.py")
            prefix += f"sys.path.insert(0, {str(root)!r})\n"
        prefix += (
            "from jev_ultrafast.browser import Browser\n"
            "def _jev_cdp(method, **params):\n"
            "    if method == 'Runtime.evaluate':\n"
            "        params.setdefault('_response_timeout', 20.0)\n"
            "    return cdp(method, **params)\n"
            "sys.modules['jev_ultrafast.browser'].cdp = _jev_cdp\n"
            "browser = Browser.__new__(Browser)\n"
            "browser.target = target_id\n"
            "browser.session = cdp('Target.attachToTarget', "
            "targetId=target_id, flatten=True)['sessionId']\n"
            "browser.after_input = None\n"
            "cdp('Emulation.setFocusEmulationEnabled', session_id=browser.session, enabled=True)\n"
            "cdp('Page.bringToFront', session_id=browser.session)\n"
        )
        return self.run_session_script(
            session_id, prefix + script, timeout_seconds=timeout_seconds
        )

    def _navigate(self, request: dict[str, Any]) -> dict[str, Any]:
        reuse_current = request.get("reuse_current", False)
        if not isinstance(reuse_current, bool):
            raise ValueError("reuse_current must be boolean")
        session_id = _session_id(
            request.get("session_id", "default")
        )
        url = _validate_url(
            request.get("url"),
            self.policy,
            self._dns_resolver,
        )
        target_id = self._load_target_id(session_id)
        if target_id:
            attach = (
                f"target_id = {json.dumps(target_id)}\n"
                "targets = {row['targetId'] for row in list_tabs(include_chrome=False)}\n"
                "target_rebound = target_id not in targets\n"
                "if target_rebound:\n"
                "    target_id = new_tab()\n"
                "else:\n"
                "    switch_tab(target_id)\n"
            )
        else:
            attach = (
                "target_id = new_tab()\n"
                "target_rebound = False\n"
            )
        body = self._run(
            session_id,
            attach
            + f"url = {json.dumps(url)}\n"
            + f"reuse_current = {reuse_current!r}\n"
            + "def navigation_info():\n"
            + "    evaluated = cdp('Runtime.evaluate', expression=\"JSON.stringify({url:location.href,title:document.title,dom_ready:!!document.documentElement})\", returnByValue=True, awaitPromise=False)\n"
            + "    if evaluated.get('exceptionDetails'):\n"
            + "        raise RuntimeError('browser navigation state evaluation failed: ' + str(evaluated['exceptionDetails']))\n"
            + "    return json.loads(evaluated['result']['value'])\n"
            + "if not reuse_current or target_rebound or navigation_info().get('url') != url:\n"
            + "    goto_url(url)\n"
            + "    if reuse_current:\n"
            + "        import time\n"
            + f"        deadline = time.monotonic() + {float(self.policy.timeout_seconds)!r}\n"
            + "        while time.monotonic() < deadline:\n"
            + "            info = navigation_info()\n"
            + "            if info.get('url') == url and info.get('dom_ready'):\n"
            + "                break\n"
            + "            time.sleep(0.15)\n"
            + "        else:\n"
            + "            raise RuntimeError('browser did not restore the requested URL')\n"
            + "    else:\n"
            + f"        loaded = wait_for_load(timeout={float(self.policy.timeout_seconds)!r})\n"
            + "        assert loaded, 'browser navigation did not finish loading'\n"
            + "info = navigation_info() if reuse_current else page_info()\n"
            + "info['target_id'] = target_id\n"
            + "info['target_rebound'] = target_rebound\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(info, ensure_ascii=False))\n"
            ),
        )
        returned_target = str(body.get("target_id") or "")
        if not returned_target:
            raise RuntimeError(
                "browser-harness did not return a target id"
            )
        self._save_target_id(
            session_id,
            returned_target,
        )
        final_url = _validate_url(
            str(body.get("url") or url),
            self.policy,
            self._dns_resolver,
        )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_NAVIGATE,
            "session_id": session_id,
            "restart_semantics": (
                "browser_and_daemon_outlive_runtime_process"
            ),
            "url": final_url,
            "title": _bounded_text(
                body.get("title"),
                1000,
            ),
            "status_code": None,
            "target_rebound": bool(body.get("target_rebound")),
        }

    def _inspect(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(
            request.get("session_id", "default")
        )
        max_chars = _bounded_int(
            request.get(
                "max_chars",
                self.policy.max_text_chars,
            ),
            1,
            self.policy.max_text_chars,
            "max_chars",
        )
        max_elements = _bounded_int(
            request.get(
                "max_elements",
                self.policy.max_elements,
            ),
            1,
            self.policy.max_elements,
            "max_elements",
        )
        expression = (
            "(()=>{const body=(document.body?.innerText||'');"
            "const nodes=[...document.querySelectorAll("
            "'a,button,input,textarea,select,[contenteditable=true],"
            "[role=button],[role=link]')];"
            f"return {{text:body.slice(0,{max_chars}),"
            "textOriginalChars:body.length,"
            f"elements:nodes.slice(0,{max_elements}).map("
            "(el,index)=>({index,"
            "tag:el.tagName.toLowerCase(),"
            "role:el.getAttribute('role')||'',"
            "name:el.getAttribute('aria-label')||"
            "el.getAttribute('placeholder')||"
            "el.getAttribute('name')||"
            "(el.innerText||'').trim().slice(0,500),"
            "href:el.getAttribute('href')||'',"
            "type:el.getAttribute('type')||'',"
            "value:('value' in el?String(el.value??''):"
            "String(el.innerText??el.textContent??'')).slice(0,1000)})),"
            f"elementsTruncated:nodes.length>{max_elements}}};}})()"
        )
        body = self._run(
            session_id,
            self._attach_target_script(session_id)
            + f"data = js({json.dumps(expression)})\n"
            + "info = page_info()\n"
            + "data['url'] = info.get('url', '')\n"
            + "data['title'] = info.get('title', '')\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(data, ensure_ascii=False))\n"
            ),
        )
        original_chars = int(
            body.get("textOriginalChars") or 0
        )
        text = str(body.get("text") or "")
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_INSPECT,
            "session_id": session_id,
            "url": _validate_url(
                str(body.get("url") or ""),
                self.policy,
                self._dns_resolver,
            ),
            "title": _bounded_text(
                body.get("title"),
                1000,
            ),
            "text": text,
            "text_truncated": len(text) < original_chars,
            "text_original_chars": original_chars,
            "elements": list(
                body.get("elements") or ()
            ),
            "elements_truncated": bool(
                body.get("elementsTruncated")
            ),
        }

    def _click(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(
            request.get("session_id", "default")
        )
        selector = _selector(
            request.get("selector")
        )
        expression = (
            "(()=>{const el=document.querySelector("
            + json.dumps(selector)
            + ");"
            "if(!el)return null;"
            "el.scrollIntoView({block:'center',"
            "inline:'center'});"
            "const r=el.getBoundingClientRect();"
            "return {x:r.left+r.width/2,"
            "y:r.top+r.height/2};})()"
        )
        body = self._run(
            session_id,
            self._attach_target_script(session_id)
            + f"point = js({json.dumps(expression)})\n"
            + (
                "assert point is not None, "
                + json.dumps(
                    "browser selector not found: "
                    + selector
                )
                + "\n"
            )
            + "click_at_xy(point['x'], point['y'])\n"
            + "info = page_info()\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(info, ensure_ascii=False))\n"
            ),
        )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_CLICK,
            "session_id": session_id,
            "url": _validate_url(
                str(body.get("url") or ""),
                self.policy,
                self._dns_resolver,
            ),
            "selector": selector,
        }

    def _type(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(
            request.get("session_id", "default")
        )
        selector = _selector(
            request.get("selector")
        )
        value = request.get("text")
        if (
            not isinstance(value, str)
            or len(value) > self.policy.max_input_chars
        ):
            raise ValueError(
                "browser input text exceeds the configured bound"
            )
        selector_json = json.dumps(selector)
        value_json = json.dumps(value)
        mode_expression = (
            "(()=>{const e=document.querySelector("
            + selector_json
            + ");if(!e)return 'missing';"
            "return e.isContentEditable?'contenteditable':'input';})()"
        )
        focus_expression = (
            "(()=>{const e=document.querySelector("
            + selector_json
            + ");if(!e)return false;"
            "e.focus();document.execCommand('selectAll',false,null);"
            "return true;})()"
        )
        body = self._run(
            session_id,
            self._attach_target_script(session_id)
            + f"input_mode = js({json.dumps(mode_expression)})\n"
            + "assert input_mode != 'missing', "
            + json.dumps("browser selector not found: " + selector)
            + "\n"
            + "if input_mode == 'contenteditable':\n"
            + f"    focused = js({json.dumps(focus_expression)})\n"
            + "    assert focused, "
            + json.dumps("browser selector not found: " + selector)
            + "\n"
            + f"    type_text({value_json})\n"
            + "else:\n"
            + (
                f"    fill_input({selector_json}, "
                f"{value_json}, "
                f"timeout={float(self.policy.timeout_seconds)!r})\n"
            )
            + "info = page_info()\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(info, ensure_ascii=False))\n"
            ),
        )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_TYPE,
            "session_id": session_id,
            "url": _validate_url(
                str(body.get("url") or ""),
                self.policy,
                self._dns_resolver,
            ),
            "selector": selector,
            "chars_written": len(value),
        }

    def _press(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(
            request.get("session_id", "default")
        )
        key = request.get("key")
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 80
        ):
            raise ValueError(
                "browser key must be a bounded non-empty string"
            )
        selector_value = request.get("selector")
        selector = (
            None
            if selector_value is None
            else _selector(selector_value)
        )
        focus = ""
        if selector is not None:
            expression = (
                "(()=>{const el=document.querySelector("
                + json.dumps(selector)
                + ");if(!el)return false;"
                "el.focus();return true;})()"
            )
            focus = (
                f"focused = js({json.dumps(expression)})\n"
                + "assert focused, "
                + json.dumps(
                    "browser selector not found: " + selector
                )
                + "\n"
            )
        body = self._run(
            session_id,
            self._attach_target_script(session_id)
            + focus
            + f"press_key({json.dumps(key)})\n"
            + "info = page_info()\n"
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(info, ensure_ascii=False))\n"
            ),
        )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_PRESS,
            "session_id": session_id,
            "url": _validate_url(
                str(body.get("url") or ""),
                self.policy,
                self._dns_resolver,
            ),
            "selector": selector,
            "key": key,
        }

    def _screenshot(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = _session_id(
            request.get("session_id", "default")
        )
        full_page = request.get(
            "full_page",
            False,
        )
        if not isinstance(full_page, bool):
            raise ValueError(
                "full_page must be boolean"
            )
        body = self._run(
            session_id,
            self._attach_target_script(session_id)
            + (
                "shot = cdp('Page.captureScreenshot', "
                "format='png', "
                f"captureBeyondViewport={full_page!r})"
                "['data']\n"
            )
            + "info = page_info()\n"
            + (
                "data = {'url': info.get('url', ''), "
                "'content': shot}\n"
            )
            + (
                f"print({json.dumps(self._RESULT_PREFIX)} + "
                "json.dumps(data))\n"
            ),
            timeout_seconds=max(
                float(self.policy.timeout_seconds),
                60.0,
            ),
        )
        content = str(
            body.get("content") or ""
        )
        try:
            raw = base64.b64decode(
                content,
                validate=True,
            )
        except Exception as exc:
            raise RuntimeError(
                "browser-harness returned invalid "
                "screenshot data"
            ) from exc
        if (
            len(raw)
            > self.policy.max_screenshot_bytes
        ):
            raise ValueError(
                "browser screenshot exceeds configured "
                "byte limit"
            )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_SCREENSHOT,
            "session_id": session_id,
            "url": _validate_url(
                str(body.get("url") or ""),
                self.policy,
                self._dns_resolver,
            ),
            "encoding": "base64",
            "media_type": "image/png",
            "bytes": len(raw),
            "content": content,
        }

    def _run(
        self,
        session_id: str,
        script: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if session_id not in self._sessions:
            if (
                len(self._sessions)
                >= self.policy.max_sessions
            ):
                raise RuntimeError(
                    "browser session limit exceeded"
                )
            self._sessions.add(session_id)

        executable = self._resolve_executable()
        if not executable:
            raise RuntimeError(
                "browser-harness executable is unavailable; "
                "set AGENT_RUNTIME_BROWSER_HARNESS or "
                "BrowserHarnessRuntime.executable"
            )

        env = dict(os.environ)
        env["BU_NAME"] = (
            f"agent-runtime-{session_id}"
        )
        env["BH_TAB_MARKER"] = "0"
        env["BH_DOMAIN_SKILLS"] = "0"
        env["ANONYMIZED_TELEMETRY"] = "false"
        if self.runtime.cdp_url:
            env["BU_CDP_URL"] = (
                self.runtime.cdp_url
            )
            env.pop(
                "BU_CDP_WS",
                None,
            )
        elif self.runtime.cdp_ws:
            env["BU_CDP_WS"] = (
                self.runtime.cdp_ws
            )
            env.pop(
                "BU_CDP_URL",
                None,
            )
        if self.runtime.runtime_dir is not None:
            runtime_dir = Path(
                self.runtime.runtime_dir
            ).expanduser()
            runtime_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
            env["BH_RUNTIME_DIR"] = str(
                runtime_dir
            )
            env["BH_RUNTIME_DIR_SHARED"] = "1"

        completed = self._runner(
            [executable],
            input="import json\n" + script,
            text=True,
            capture_output=True,
            timeout=float(
                timeout_seconds
                or max(
                    self.policy.timeout_seconds + 10,
                    30,
                )
            ),
            env=env,
            check=False,
        )
        if int(completed.returncode) != 0:
            detail = str(
                completed.stderr
                or completed.stdout
                or ""
            ).strip()
            raise RuntimeError(
                "browser-harness command failed"
                + (
                    f": {detail.splitlines()[-1]}\n{detail[-1000:]}"
                    if detail
                    else ""
                )
            )

        for line in reversed(
            str(completed.stdout or "").splitlines()
        ):
            if not line.startswith(
                self._RESULT_PREFIX
            ):
                continue
            value = json.loads(
                line[len(self._RESULT_PREFIX) :]
            )
            if not isinstance(value, dict):
                raise RuntimeError(
                    "browser-harness returned a "
                    "non-object result"
                )
            return value
        raise RuntimeError(
            "browser-harness command returned no "
            "structured result"
        )

    def _attach_target_script(
        self,
        session_id: str,
    ) -> str:
        target_id = self._load_target_id(
            session_id
        )
        if not target_id:
            raise LookupError(
                "browser session does not exist: "
                f"{session_id}"
            )
        return (
            f"target_id = {json.dumps(target_id)}\n"
            "switch_tab(target_id)\n"
        )

    def _session_state_path(
        self,
        session_id: str,
    ) -> Path:
        return (
            Path(self.runtime.state_dir)
            / f"{session_id}.json"
        )

    def _load_target_id(
        self,
        session_id: str,
    ) -> str | None:
        path = self._session_state_path(
            session_id
        )
        try:
            body = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid browser session state: {path}"
            ) from exc
        if not isinstance(body, dict):
            raise RuntimeError(
                f"invalid browser session state: {path}"
            )
        expected_binding = (
            self.runtime.cdp_url
            or self.runtime.cdp_ws
            or "local"
        )
        if (
            body.get("browser_binding")
            != expected_binding
        ):
            raise RuntimeError(
                f"browser session {session_id} is "
                "bound to a different browser"
            )
        target_id = body.get("target_id")
        if (
            not isinstance(target_id, str)
            or not target_id
        ):
            raise RuntimeError(
                f"browser session {session_id} "
                "has no target id"
            )
        return target_id

    def _save_target_id(
        self,
        session_id: str,
        target_id: str,
    ) -> None:
        root = Path(self.runtime.state_dir)
        root.mkdir(
            parents=True,
            exist_ok=True,
        )
        path = self._session_state_path(
            session_id
        )
        tmp = path.with_suffix(".tmp")
        body = {
            "schema_version": 1,
            "session_id": session_id,
            "target_id": target_id,
            "browser_binding": (
                self.runtime.cdp_url
                or self.runtime.cdp_ws
                or "local"
            ),
        }
        tmp.write_text(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(
            tmp,
            0o600,
        )
        os.replace(
            tmp,
            path,
        )

    def _resolve_executable(self) -> str | None:
        candidate = Path(
            self.runtime.executable
        ).expanduser()
        if candidate.is_absolute():
            return (
                str(candidate)
                if candidate.is_file()
                else None
            )
        return shutil.which(
            self.runtime.executable
        )


def register_browser_harness_provider(
    provider: BrowserHarnessProvider,
    inventory: CapabilityInventory,
) -> tuple[str, ...]:
    registered: list[str] = []
    for row in provider.manifest()["capabilities"]:
        capability_id = str(
            row["capability_id"]
        )
        if (
            inventory.get(capability_id)
            is not None
        ):
            raise ValueError(
                "browser capability already registered: "
                f"{capability_id}"
            )
        level = SideEffectLevel[
            str(row["side_effect_level"])
        ]
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider.provider_name,
                candidate_id=(
                    f"{provider.provider_name}:"
                    f"{capability_id}"
                ),
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.contract_version,
                side_effects=level,
                entry_point={
                    "adapter": (
                        "BrowserHarnessProvider"
                    ),
                    "required_permissions": list(
                        row.get(
                            "required_permissions"
                        )
                        or ()
                    ),
                },
                evidence_refs=[
                    "contract:runtime-browser:v2"
                ],
            )
        )
        registered.append(
            capability_id
        )
    return tuple(registered)


def _session_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _SESSION_RE.fullmatch(value)
    ):
        raise ValueError(
            "session_id must match "
            "[A-Za-z0-9._-]{1,64}"
        )
    return value


def _selector(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 1024
    ):
        raise ValueError(
            "selector must be a bounded "
            "non-empty string"
        )
    return value.strip()


def _bounded_int(
    value: Any,
    low: int,
    high: int,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise ValueError(
            f"{field} must be in "
            f"[{low}, {high}]"
        )
    return value


def _bounded_text(
    value: Any,
    max_chars: int,
) -> str:
    if value is None:
        return ""
    text = (
        value
        if isinstance(value, str)
        else str(value)
    )
    return text[:max_chars]


def _normalize_host(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        raise ValueError(
            "browser host policy entries "
            "must be non-empty strings"
        )
    host = (
        value.strip()
        .rstrip(".")
        .lower()
    )
    if "://" in host or "/" in host:
        raise ValueError(
            "browser host policy entries "
            "must be hostnames"
        )
    return host


def _host_matches(
    host: str,
    pattern: str,
) -> bool:
    return (
        host == pattern
        or host.endswith(
            "." + pattern
        )
    )


def _validate_url(
    value: Any,
    policy: BrowserPolicy,
    dns_resolver: Callable[..., Any],
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 4096
    ):
        raise ValueError(
            "browser url must be a bounded "
            "non-empty string"
        )
    url = value.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower()
        not in {"http", "https"}
        or not parsed.hostname
    ):
        raise ValueError(
            "browser supports only http/https URLs"
        )
    if (
        parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "credential-bearing browser URLs "
            "are not allowed"
        )

    host = (
        parsed.hostname
        .rstrip(".")
        .lower()
    )
    if (
        host
        in {
            "localhost",
            "localhost.localdomain",
        }
        or host.endswith(
            (".local", ".internal")
        )
    ):
        raise ValueError(
            "local/private browser hosts "
            "are not allowed"
        )
    blocked = frozenset(
        _normalize_host(x)
        for x in policy.blocked_hosts
    )
    if any(
        _host_matches(
            host,
            item,
        )
        for item in blocked
    ):
        raise ValueError(
            "browser host is blocked by policy"
        )
    if policy.allowed_hosts is not None:
        allowed = frozenset(
            _normalize_host(x)
            for x in policy.allowed_hosts
        )
        if not any(
            _host_matches(
                host,
                item,
            )
            for item in allowed
        ):
            raise ValueError(
                "browser host is outside the "
                "configured allowlist"
            )

    try:
        literal = ipaddress.ip_address(
            host
        )
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError(
                "non-global browser IP targets "
                "are not allowed"
            )
        return url

    try:
        addresses = dns_resolver(
            host,
            parsed.port
            or (
                443
                if parsed.scheme == "https"
                else 80
            ),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError(
            "browser host could not be resolved"
        ) from exc
    if not addresses:
        raise ValueError(
            "browser host could not be resolved"
        )
    for row in addresses:
        try:
            address = ipaddress.ip_address(
                row[4][0]
            )
        except (
            IndexError,
            TypeError,
            ValueError,
        ):
            raise ValueError(
                "browser DNS result is invalid"
            )
        if not address.is_global:
            raise ValueError(
                "browser DNS resolved to a "
                "non-global address"
            )
    return url


__all__ = [
    "BrowserHarnessProvider",
    "BrowserHarnessRuntime",
    "register_browser_harness_provider",
]
