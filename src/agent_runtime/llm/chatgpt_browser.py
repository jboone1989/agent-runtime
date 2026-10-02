"""ChatGPT Web LLM provider backed by Agent Runtime's persistent browser.

This provider uses the existing authenticated browser profile rather than an API
key. Browser Harness owns CDP/tab/input mechanics; this module owns only the
ChatGPT-specific request/response contract.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
import shutil
from typing import Any, AsyncIterator, Mapping

from ..integrations.browser import BrowserPolicy
from ..integrations.browser_harness import (
    BrowserHarnessProvider,
    BrowserHarnessRuntime,
)



class _BrowserProfileProcessLock:
    """Serialize all processes that operate one authenticated ChatGPT profile."""

    def __init__(self, path: Path, *, timeout_seconds: float) -> None:
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        self._handle = handle
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    handle.close()
                    self._handle = None
                    raise TimeoutError(
                        f"ChatGPT browser profile lock exceeded {self.timeout_seconds:.0f}s"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        handle = self._handle
        self._handle = None
        if handle is None:
            return False
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        return False


class ChatGPTBrowserProviderError(RuntimeError):
    pass


class ChatGPTBrowserProvider:
    provider_name = "chatgpt-browser"
    model = "chatgpt-web"
    chat_url = "https://chatgpt.com/"

    def __init__(
        self,
        *,
        browser: BrowserHarnessProvider,
        session_id: str = "chatgpt-cognition",
        response_timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 0.4,
        stable_seconds: float = 1.6,
        process_lock_path: Path | None = None,
    ) -> None:
        if not str(session_id).strip():
            raise ValueError("session_id must be non-empty")
        if not 5 <= float(response_timeout_seconds) <= 900:
            raise ValueError("response_timeout_seconds must be in [5, 900]")
        if not 0.1 <= float(poll_interval_seconds) <= 5:
            raise ValueError("poll_interval_seconds must be in [0.1, 5]")
        if not 0.5 <= float(stable_seconds) <= 10:
            raise ValueError("stable_seconds must be in [0.5, 10]")
        self.browser = browser
        self.session_id = str(session_id)
        self.response_timeout_seconds = float(response_timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.stable_seconds = float(stable_seconds)
        self.process_lock_path = Path(process_lock_path or (Path.home() / ".local" / "state" / "agent-runtime" / "browser-harness" / "chatgpt-browser.lock"))
        self.last_metadata: dict[str, Any] = {}
        self._request_lock = asyncio.Lock()

    @classmethod
    def from_environment(cls) -> "ChatGPTBrowserProvider":
        profile_id = str(
            os.environ.get("AGENT_RUNTIME_CHATGPT_PROFILE_ID") or "chatgpt"
        ).strip()
        cdp_url = str(
            os.environ.get("AGENT_RUNTIME_CHATGPT_CDP_URL") or ""
        ).strip()
        if not cdp_url:
            raw_state = str(
                os.environ.get("AGENT_RUNTIME_CHATGPT_PROFILE_STATE") or ""
            ).strip()
            state_path = (
                Path(raw_state).expanduser()
                if raw_state
                else Path.home()
                / ".local"
                / "state"
                / "agent-runtime"
                / "browser"
                / "profiles"
                / profile_id
                / "runtime.json"
            )
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ChatGPTBrowserProviderError(
                    f"ChatGPT browser profile state is unavailable: {state_path}"
                ) from exc
            except json.JSONDecodeError as exc:
                raise ChatGPTBrowserProviderError(
                    f"ChatGPT browser profile state is invalid: {state_path}"
                ) from exc
            port = state.get("cdp_port") if isinstance(state, Mapping) else None
            if (
                isinstance(port, bool)
                or not isinstance(port, int)
                or not 1 <= port <= 65535
            ):
                raise ChatGPTBrowserProviderError(
                    f"ChatGPT browser profile has no valid CDP port: {state_path}"
                )
            cdp_url = f"http://127.0.0.1:{port}"

        executable = str(
            os.environ.get("AGENT_RUNTIME_BROWSER_HARNESS") or ""
        ).strip()
        if not executable:
            executable = shutil.which("browser-harness") or ""
        if not executable:
            candidate = Path.home() / "browser-use-venv" / "bin" / "browser-harness"
            if candidate.is_file():
                executable = str(candidate)
        if not executable:
            raise ChatGPTBrowserProviderError(
                "browser-harness executable is unavailable"
            )

        raw_timeout = str(
            os.environ.get("AGENT_RUNTIME_CHATGPT_RESPONSE_TIMEOUT_SECONDS")
            or "180"
        ).strip()
        try:
            response_timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                "AGENT_RUNTIME_CHATGPT_RESPONSE_TIMEOUT_SECONDS must be numeric"
            ) from exc

        state_dir = Path(
            os.environ.get(
                "AGENT_RUNTIME_BROWSER_STATE",
                "~/.local/state/agent-runtime/browser-harness",
            )
        ).expanduser()
        browser = BrowserHarnessProvider(
            policy=BrowserPolicy(
                max_sessions=2,
                timeout_seconds=30.0,
                max_text_chars=100_000,
                max_elements=200,
                max_input_chars=100_000,
                allowed_hosts=frozenset({"chatgpt.com"}),
            ),
            runtime=BrowserHarnessRuntime(
                executable=executable,
                state_dir=state_dir,
                cdp_url=cdp_url,
            ),
        )
        session_id = str(
            os.environ.get("AGENT_RUNTIME_CHATGPT_SESSION_ID")
            or "chatgpt-cognition"
        ).strip()
        return cls(
            browser=browser,
            session_id=session_id,
            response_timeout_seconds=response_timeout,
            process_lock_path=state_dir / f"chatgpt-profile-{profile_id}.lock",
        )

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty")
        async with self._request_lock:
            return await asyncio.to_thread(
                self._complete_sync,
                prompt,
                dict(kwargs),
            )

    async def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        instruction = (
            f"{prompt}\n\nReturn only one JSON object matching this JSON schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        raw = await self.complete(instruction, **kwargs)
        try:
            value = json.loads(_json_object(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ChatGPTBrowserProviderError(
                "ChatGPT Web returned invalid structured JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ChatGPTBrowserProviderError(
                "ChatGPT Web structured response is not an object"
            )
        return value

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        yield await self.complete(prompt, **kwargs)

    async def embed(self, text: str, **kwargs: Any) -> list[float]:
        _ = (text, kwargs)
        raise ChatGPTBrowserProviderError(
            "ChatGPT Web does not expose embeddings through the browser provider"
        )

    def _complete_sync(
        self,
        prompt: str,
        kwargs: dict[str, Any],
    ) -> str:
        timeout = self._response_timeout(kwargs)
        outbound = self._request_text(prompt, kwargs)
        try:
            with _BrowserProfileProcessLock(
                self.process_lock_path,
                timeout_seconds=min(900.0, timeout + 45.0),
            ):
                self.browser.adopt_existing_tab(
                    self.session_id,
                    self.chat_url,
                )
                body = self.browser.run_session_script(
                    self.session_id,
                    self._transaction_script(outbound, timeout),
                    timeout_seconds=min(900.0, timeout + 45.0),
                )
        except (LookupError, RuntimeError, TimeoutError) as exc:
            raise ChatGPTBrowserProviderError(
                f"ChatGPT browser transaction failed: {exc}"
            ) from exc
        response = str(body.get("response") or "").strip()
        if not response:
            raise ChatGPTBrowserProviderError(
                "ChatGPT browser transaction returned an empty response"
            )
        self.last_metadata = {
            "provider": self.provider_name,
            "model": self.model,
            "url": body.get("url"),
            "assistant_message_count": body.get("assistant_message_count"),
        }
        return response

    def _transaction_script(
        self,
        outbound: str,
        timeout: float,
    ) -> str:
        return (
            "import time\n"
            f"state_expr = {json.dumps(_CHATGPT_STATE_EXPRESSION)}\n"
            f"new_chat_expr = {json.dumps(_NEW_CHAT_EXPRESSION)}\n"
            f"focus_expr = {json.dumps(_FOCUS_COMPOSER_EXPRESSION)}\n"
            f"send_expr = {json.dumps(_SEND_EXPRESSION)}\n"
            f"outbound = {json.dumps(outbound, ensure_ascii=False)}\n"
            f"response_timeout = {float(timeout)!r}\n"
            f"poll_interval = {float(self.poll_interval_seconds)!r}\n"
            f"stable_seconds = {float(self.stable_seconds)!r}\n"
            "state = js(state_expr)\n"
            "assert not state.get('login_required'), 'ChatGPT browser profile is not authenticated'\n"
            "needs_fresh = bool(state.get('user_count') or state.get('assistant_count') or '/c/' in str(state.get('url') or ''))\n"
            "if needs_fresh:\n"
            "    clicked = js(new_chat_expr)\n"
            "    assert clicked is True, 'ChatGPT new-chat control is unavailable'\n"
            "    fresh_deadline = time.monotonic() + 15.0\n"
            "    while time.monotonic() < fresh_deadline:\n"
            "        state = js(state_expr)\n"
            "        assert not state.get('login_required'), 'ChatGPT authentication was lost while starting a new chat'\n"
            "        if state.get('composer_selector') and '/c/' not in str(state.get('url') or ''):\n"
            "            break\n"
            "        time.sleep(poll_interval)\n"
            "    else:\n"
            "        raise RuntimeError('ChatGPT did not enter a fresh chat within 15s')\n"
            "focused = js(focus_expr)\n"
            "assert focused is True, 'ChatGPT composer could not be focused'\n"
            "type_text(outbound)\n"
            "typed = js(state_expr)\n"
            "assert outbound.strip() in str(typed.get('composer_text') or '').strip(), 'ChatGPT composer fill verification failed'\n"
            "baseline_user_count = int(typed.get('user_count') or 0)\n"
            "baseline_assistant_count = int(typed.get('assistant_count') or 0)\n"
            "sent = js(send_expr)\n"
            "assert sent is True, 'ChatGPT Send button is unavailable'\n"
            "deadline = time.monotonic() + response_timeout\n"
            "last_text = ''\n"
            "stable_since = None\n"
            "while time.monotonic() < deadline:\n"
            "    state = js(state_expr)\n"
            "    assert not state.get('login_required'), 'ChatGPT authentication was lost while waiting for a response'\n"
            "    user_count = int(state.get('user_count') or 0)\n"
            "    assistant_count = int(state.get('assistant_count') or 0)\n"
            "    text = str(state.get('last_assistant_text') or '').strip()\n"
            "    if user_count > baseline_user_count and assistant_count > baseline_assistant_count and text:\n"
            "        now = time.monotonic()\n"
            "        if text != last_text:\n"
            "            last_text = text\n"
            "            stable_since = now\n"
            "        elif stable_since is None:\n"
            "            stable_since = now\n"
            "        if not state.get('generating') and stable_since is not None and now - stable_since >= stable_seconds:\n"
            "            result = {'response': text, 'url': state.get('url'), 'assistant_message_count': assistant_count}\n"
            "            break\n"
            "    time.sleep(poll_interval)\n"
            "else:\n"
            "    raise RuntimeError(f'ChatGPT Web response exceeded {response_timeout:.0f}s')\n"
        )

    def _response_timeout(self, kwargs: Mapping[str, Any]) -> float:
        raw = kwargs.get("timeout_seconds")
        if raw is None:
            raw = kwargs.get("_reserve_timeout_seconds")
        if raw is None:
            raw = self.response_timeout_seconds
        try:
            timeout = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ChatGPT response timeout must be numeric"
            ) from exc
        return max(
            5.0,
            min(timeout, self.response_timeout_seconds, 900.0),
        )

    @staticmethod
    def _request_text(
        prompt: str,
        kwargs: Mapping[str, Any],
    ) -> str:
        system = kwargs.get("system")
        parts: list[str] = []
        if system is not None and str(system).strip():
            parts.extend(
                [
                    "System instructions for this request:",
                    str(system).strip(),
                    "",
                    "Task:",
                ]
            )
        parts.append(prompt.strip())
        max_tokens = kwargs.get("max_tokens")
        if (
            isinstance(max_tokens, int)
            and not isinstance(max_tokens, bool)
            and max_tokens > 0
        ):
            parts.extend(
                [
                    "",
                    "Keep the response within approximately "
                    f"{min(max_tokens, 32768)} tokens.",
                ]
            )
        return "\n".join(parts)


_CHATGPT_STATE_EXPRESSION = r"""(() => {
  const visible = (el) => Boolean(
    el && el.getClientRects && el.getClientRects().length
  );
  const controls = [...document.querySelectorAll('button,a,[role="button"]')];
  const loginRequired = controls.some((el) => {
    if (!visible(el)) return false;
    const text = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.textContent ||
      ''
    ).trim().toLowerCase();
    return text === 'log in' || text === 'login';
  });
  let composer = document.querySelector('#prompt-textarea');
  let composerSelector = composer ? '#prompt-textarea' : '';
  if (!composer) {
    composer = document.querySelector('textarea[placeholder]');
    composerSelector = composer ? 'textarea[placeholder]' : '';
  }
  if (!composer) {
    composer = document.querySelector(
      '[contenteditable="true"][data-lexical-editor="true"]'
    );
    composerSelector = composer
      ? '[contenteditable="true"][data-lexical-editor="true"]'
      : '';
  }
  if (!composer) {
    composer = document.querySelector('main [contenteditable="true"]');
    composerSelector = composer ? 'main [contenteditable="true"]' : '';
  }
  const composerText = composer
    ? (typeof composer.value === 'string'
      ? composer.value
      : (composer.innerText || composer.textContent || ''))
    : '';
  const messageNodes = (role) => {
    const selectors = [
      '[data-message-author-role="' + role + '"]',
      '[data-content-search-unit-key$=":' + role + '"]',
      '[data-chatgpt-search-unit-key$=":' + role + '"]',
    ];
    for (const selector of selectors) {
      const nodes = [...document.querySelectorAll(selector)];
      if (nodes.length) return nodes;
    }
    return [];
  };
  const users = messageNodes('user');
  const assistants = messageNodes('assistant');
  const last = assistants.length ? assistants[assistants.length - 1] : null;
  const responseBody = last && last.querySelector
    ? (
      last.querySelector('[data-chatgpt-selection-message-id]') ||
      last.querySelector('.markdown') ||
      last
    )
    : last;
  const lastAssistantText = (
    responseBody?.innerText ||
    responseBody?.textContent ||
    ''
  ).trim();
  const generating = controls.some((el) => {
    if (!visible(el)) return false;
    if (el.getAttribute('data-testid') === 'stop-button') return true;
    const text = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.textContent ||
      ''
    ).trim().toLowerCase();
    return text === 'stop' ||
      text.includes('stop generating') ||
      text.includes('stop streaming') ||
      text.includes('stop response');
  });
  return {
    url: location.href,
    login_required: loginRequired,
    composer_selector: composerSelector,
    composer_text: composerText,
    user_count: users.length,
    assistant_count: assistants.length,
    last_assistant_text: lastAssistantText,
    generating,
  };
})()"""


_NEW_CHAT_EXPRESSION = r"""(() => {
  const visible = (el) => Boolean(
    el && el.getClientRects && el.getClientRects().length
  );
  const controls = [
    ...document.querySelectorAll('button,a,[role="button"]')
  ];
  const target = controls.find((el) => {
    if (!visible(el)) return false;
    const text = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.textContent ||
      ''
    ).trim().toLowerCase();
    return text === 'new chat';
  });
  if (!target) return false;
  target.click();
  return true;
})()"""


_FOCUS_COMPOSER_EXPRESSION = r"""(() => {
  const el =
    document.querySelector('#prompt-textarea') ||
    document.querySelector('textarea[placeholder]') ||
    document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
    document.querySelector('main [contenteditable="true"]');
  if (!el) return false;
  el.focus();
  document.execCommand('selectAll', false, null);
  return document.activeElement === el || el.contains(document.activeElement);
})()"""


_SEND_EXPRESSION = r"""(() => {
  const buttons = [
    ...document.querySelectorAll('button[type="submit"],button')
  ];
  const target = buttons.find((el) => {
    if (!el.getClientRects || !el.getClientRects().length) return false;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    const label = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.textContent ||
      ''
    ).trim().toLowerCase();
    return label === 'send';
  });
  if (!target) return false;
  target.click();
  return true;
})()"""


def _json_object(text: str) -> str:
    stripped = str(text).strip()
    fence = chr(96) * 3
    if stripped.startswith(fence):
        lines = stripped.splitlines()
        if lines and lines[0].startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON object not found")
    return stripped[start : end + 1]


__all__ = [
    "ChatGPTBrowserProvider",
    "ChatGPTBrowserProviderError",
]
