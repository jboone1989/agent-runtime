"""ChatGPT Web LLM provider backed by Agent Runtime's persistent browser.

This provider uses the existing authenticated browser profile rather than an API
key. Browser Harness owns CDP/tab/input mechanics; this module owns only the
ChatGPT-specific request/response contract.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import json
import os
import tempfile
import time
from pathlib import Path
import shutil
from typing import Any, AsyncIterator, Mapping

from ..integrations.browser import BrowserPolicy, BROWSER_NAVIGATE
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
        conversation_state_path: Path | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(session_id)):
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
        self.conversation_state_path = Path(conversation_state_path or
            self.process_lock_path.with_name(f"{self.process_lock_path.stem}-{self.session_id}-conversation.json"))
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
                journal = self._load_conversation()
                pending = journal.get("pending")
                # Finish the outstanding turn before submitting a later turn.
                # The stored payload survives provider/process restarts.
                if pending:
                    body = self._run_turn(str(pending["outbound"]), timeout, journal)
                    if pending["payload_sha256"] == hashlib.sha256(outbound.encode()).hexdigest():
                        return self._record_result(body)
                    journal = self._load_conversation()
                body = self._run_turn(outbound, timeout, journal)
        except (LookupError, RuntimeError, TimeoutError) as exc:
            raise ChatGPTBrowserProviderError(
                f"ChatGPT browser transaction failed: {exc}"
            ) from exc
        return self._record_result(body)

    def _load_conversation(self) -> dict[str, Any]:
        try:
            journal = json.loads(self.conversation_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "session_id": self.session_id}
        if (not isinstance(journal, dict) or journal.get("schema_version") != 1
                or journal.get("session_id") != self.session_id):
            raise ChatGPTBrowserProviderError("Invalid ChatGPT conversation binding")
        url = journal.get("conversation_url")
        if url and not re.fullmatch(r"https://chatgpt\.com/c/[A-Za-z0-9_-]+", url):
            raise ChatGPTBrowserProviderError("Invalid ChatGPT conversation URL")
        return journal

    def _run_turn(self, outbound: str, timeout: float, journal: dict[str, Any]) -> dict[str, Any]:
        url = journal.get("conversation_url")
        if url:
            # Navigation restores the conversation even after its tab/target
            # disappears. A browser target ID is never conversation identity.
            self.browser.invoke(BROWSER_NAVIGATE, {"session_id": self.session_id, "url": url})
        elif not journal.get("pending"):
            self.browser.adopt_existing_tab(self.session_id, self.chat_url)
        # An unbound in-flight turn must use its persisted target; adopting a
        # different tab would invent provenance for an ambiguous submission.
        with tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", dir=self.process_lock_path.parent,
            prefix="chatgpt-outbound-", suffix=".txt",
        ) as payload:
            payload.write(outbound)
            payload.flush()
            return self.browser.run_session_script(
                self.session_id, self._transaction_script(Path(payload.name), timeout),
                timeout_seconds=min(900.0, timeout + 45.0),
            )

    def _record_result(self, body: dict[str, Any]) -> str:
        response = str(body.get("response") or "").strip()
        if not response:
            raise ChatGPTBrowserProviderError("ChatGPT browser transaction returned an empty response")
        self.last_metadata = {
            "provider": self.provider_name, "model": self.model,
            "url": body.get("url"),
            "conversation_id": str(body.get("url") or "").rsplit("/", 1)[-1],
            "request_id": body.get("request_id"), "recovered": bool(body.get("recovered")),
            "assistant_message_count": body.get("assistant_message_count"),
        }
        return response

    def _transaction_script(self, outbound_path: Path, timeout: float) -> str:
        return (
            "import json\nimport time\nfrom pathlib import Path\n"
            f"state_expr = {json.dumps(_CHATGPT_STATE_EXPRESSION)}\n"
            f"new_chat_expr = {json.dumps(_NEW_CHAT_EXPRESSION)}\n"
            f"focus_expr = {json.dumps(_FOCUS_COMPOSER_EXPRESSION)}\n"
            f"send_expr = {json.dumps(_SEND_EXPRESSION)}\n"
            f"outbound = Path({json.dumps(str(outbound_path))}).read_text(encoding='utf-8')\n"
            f"journal_path = Path({json.dumps(str(self.conversation_state_path))})\n"
            f"session_id = {json.dumps(self.session_id)}\n"
            f"response_timeout = {float(timeout)!r}\n"
            f"poll_interval = {float(self.poll_interval_seconds)!r}\n"
            f"stable_seconds = {float(self.stable_seconds)!r}\n"
            + _CHATGPT_TRANSACTION
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


# Executed by Browser Harness with its page primitives on the bound tab.
_CHATGPT_TRANSACTION = r"""import hashlib
import os
import re
import tempfile
import uuid
try:
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
except FileNotFoundError:
    journal = {'schema_version': 1, 'session_id': session_id}
pending = journal.get('pending')
recovering = bool(pending)
if pending:
    assert pending['outbound'] == outbound, 'ChatGPT pending payload changed'
def save_journal():
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=journal_path.parent,
                                     prefix=journal_path.name + '.', delete=False) as handle:
        tmp = Path(handle.name)
        os.chmod(tmp, 0o600)
        json.dump(journal, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, journal_path)
    if os.name != 'nt':
        directory_fd = os.open(journal_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
def bind_conversation(url):
    if not re.fullmatch(r'https://chatgpt\.com/c/[A-Za-z0-9_-]+', str(url or '')):
        return
    expected = journal.get('conversation_url')
    assert not expected or expected == url, 'ChatGPT conversation binding changed unexpectedly'
    if not expected:
        journal['conversation_url'] = url
        save_journal()
state = js(state_expr)
assert not state.get('login_required'), 'ChatGPT browser profile is not authenticated'
if journal.get('conversation_url'):
    assert state.get('url') == journal['conversation_url'], 'ChatGPT failed to restore its bound conversation'
needs_fresh = not journal.get('conversation_url') and not pending and bool(state.get('user_count') or state.get('assistant_count') or '/c/' in str(state.get('url') or ''))
if needs_fresh:
    clicked = js(new_chat_expr)
    assert clicked is True, 'ChatGPT new-chat control is unavailable'
    fresh_deadline = time.monotonic() + 15.0
    while time.monotonic() < fresh_deadline:
        state = js(state_expr)
        assert not state.get('login_required'), 'ChatGPT authentication was lost while starting a new chat'
        if state.get('composer_selector') and '/c/' not in str(state.get('url') or '') and not state.get('user_count') and not state.get('assistant_count'):
            break
        time.sleep(poll_interval)
    else:
        raise RuntimeError('ChatGPT did not enter a fresh chat within 15s')
bind_conversation(state.get('url'))
assert not state.get('generating') or pending, 'ChatGPT has an unowned generation in progress'
if pending:
    baseline_user_count = int(pending['baseline_user_count'])
    baseline_assistant_count = int(pending['baseline_assistant_count'])
    baseline_assistant_id = pending.get('baseline_assistant_id')
    sent = pending.get('submission_acknowledged')
else:
    focused = js(focus_expr)
    assert focused is True, 'ChatGPT composer could not be focused'
    input_chunk_chars = 4000
    for start in range(0, len(outbound), input_chunk_chars):
        end = min(len(outbound), start + input_chunk_chars)
        chunk = outbound[start:end]
        insert_expr = ("(() => {const el=document.activeElement;"
                       "if(!el || el.getAttribute('contenteditable') !== 'true') return false;"
                       "setTimeout(() => document.execCommand('insertText', false, "
                       + json.dumps(chunk, ensure_ascii=False)
                       + " ), 0); return true;})()")
        try:
            inserted = js(insert_expr)
        except RuntimeError as exc:
            if 'Runtime.evaluate timed out' not in str(exc):
                raise
            inserted = None
        assert inserted is True or inserted is None, f'ChatGPT composer insertText was rejected at chunk {start}:{end}'
        expected_prefix = ' '.join(outbound[:end].split())
        input_deadline = time.monotonic() + 30.0
        actual_prefix = ''
        while time.monotonic() < input_deadline:
            try:
                typed = js(state_expr)
            except RuntimeError as exc:
                if 'Runtime.evaluate timed out' not in str(exc):
                    raise
                time.sleep(poll_interval)
                continue
            assert not typed.get('login_required'), 'ChatGPT authentication was lost while filling the composer'
            actual_prefix = ' '.join(str(typed.get('composer_text') or '').split())
            if actual_prefix == expected_prefix:
                break
            if actual_prefix and not expected_prefix.startswith(actual_prefix):
                break
            time.sleep(poll_interval)
        if actual_prefix != expected_prefix:
            limit = min(len(expected_prefix), len(actual_prefix))
            mismatch = next((i for i in range(limit) if expected_prefix[i] != actual_prefix[i]), limit)
            exp_codes = [ord(ch) for ch in expected_prefix[max(0, mismatch - 8): mismatch + 12]]
            act_codes = [ord(ch) for ch in actual_prefix[max(0, mismatch - 8): mismatch + 12]]
            raise RuntimeError(f'ChatGPT composer chunk verification failed: start={start} end={end} mismatch={mismatch} expected_chars={len(expected_prefix)} actual_chars={len(actual_prefix)} expected_codes={exp_codes} actual_codes={act_codes}')
    typed = js(state_expr)
    expected_text = ' '.join(outbound.split())
    actual_text = ' '.join(str(typed.get('composer_text') or '').split())
    assert actual_text == expected_text, f'ChatGPT composer fill verification failed: expected_chars={len(expected_text)} actual_chars={len(actual_text)}'
    baseline_user_count = int(typed.get('user_count') or 0)
    baseline_assistant_count = int(typed.get('assistant_count') or 0)
    baseline_assistant_id = typed.get('last_assistant_id')
    pending = {'request_id': uuid.uuid4().hex, 'outbound': outbound,
        'payload_sha256': hashlib.sha256(outbound.encode()).hexdigest(),
        'baseline_user_count': baseline_user_count,
        'baseline_assistant_count': baseline_assistant_count,
        'baseline_assistant_id': baseline_assistant_id,
        'stage': 'SUBMITTING'}
    journal['pending'] = pending
    save_journal()
    try:
        sent = js(send_expr)
    except RuntimeError as exc:
        if 'Runtime.evaluate timed out' not in str(exc):
            raise
        sent = None
    if sent is False:
        journal['pending'] = None
        save_journal()
        raise RuntimeError('ChatGPT Send button is unavailable')
    assert sent is True or sent is None, 'ChatGPT Send returned an unexpected result'
    pending['submission_acknowledged'] = sent is True
    pending['stage'] = 'WAITING_RESPONSE'
    save_journal()
poll_timeout_count = 0
deadline = time.monotonic() + response_timeout
last_text = ''
stable_since = None
while time.monotonic() < deadline:
    try:
        state = js(state_expr)
    except RuntimeError as exc:
        if 'Runtime.evaluate timed out' not in str(exc):
            raise
        poll_timeout_count += 1
        time.sleep(poll_interval)
        continue
    assert not state.get('login_required'), 'ChatGPT authentication was lost while waiting for a response'
    bind_conversation(state.get('url'))
    user_count = int(state.get('user_count') or 0)
    assistant_count = int(state.get('assistant_count') or 0)
    text = str(state.get('last_assistant_text') or '').strip()
    current_id = state.get('last_assistant_id')
    new_answer = (current_id != baseline_assistant_id if current_id and baseline_assistant_id
                  else assistant_count > baseline_assistant_count)
    if new_answer and text:
        now = time.monotonic()
        if text != last_text:
            last_text = text
            stable_since = now
        elif stable_since is None:
            stable_since = now
        if not state.get('generating') and stable_since is not None and now - stable_since >= stable_seconds:
            assert journal.get('conversation_url'), 'ChatGPT did not expose a durable conversation URL'
            result = {'response': text, 'url': journal['conversation_url'],
                      'assistant_message_count': assistant_count,
                      'request_id': pending['request_id'], 'recovered': recovering}
            journal['last_completed'] = result
            journal['pending'] = None
            save_journal()
            break
    time.sleep(poll_interval)
else:
    diagnostics = {'url': state.get('url'), 'baseline_user_count': baseline_user_count, 'user_count': state.get('user_count'), 'baseline_assistant_count': baseline_assistant_count, 'assistant_count': state.get('assistant_count'), 'generating': state.get('generating'), 'response_chars': len(str(state.get('last_assistant_text') or '')), 'composer_chars': len(str(state.get('composer_text') or '')), 'poll_timeout_count': poll_timeout_count, 'submission_acknowledged': sent is True, 'stable_for_seconds': (time.monotonic() - stable_since) if stable_since is not None else None}
    raise RuntimeError(f'ChatGPT Web response exceeded {response_timeout:.0f}s: {json.dumps(diagnostics, sort_keys=True)}')
"""

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
  const composerSelectors = [
    '#prompt-textarea',
    'textarea[placeholder]',
    '[contenteditable="true"][data-lexical-editor="true"]',
    'main [contenteditable="true"][role="textbox"]',
    'main [contenteditable="true"]',
  ];
  let composer = null;
  let composerSelector = '';
  for (const selector of composerSelectors) {
    const candidate = [...document.querySelectorAll(selector)].find((el) =>
      visible(el) &&
      !el.disabled &&
      el.getAttribute('aria-disabled') !== 'true'
    );
    if (candidate) {
      composer = candidate;
      composerSelector = selector;
      break;
    }
  }
  const composerText = composer
    ? (typeof composer.value === 'string'
      ? composer.value
      : [...composer.childNodes]
          .map((node) => node.textContent || '')
          .join('\n'))
    : '';
  const messageNodes = (role) => {
    const selectors = [
      '[data-message-author-role="' + role + '"]',
      '[data-content-search-unit-key$=":' + role + '"]',
      '[data-chatgpt-search-unit-key$=":' + role + '"]',
    ];
    for (const selector of selectors) {
      const nodes = [...document.querySelectorAll(selector)].filter(visible);
      if (nodes.length) {
        const seen = new Set();
        return nodes.filter((el) => {
          const key = el.getAttribute('data-message-id') ||
            el.getAttribute('data-content-search-unit-key') ||
            el.getAttribute('data-chatgpt-search-unit-key');
          if (!key) return true;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      }
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
    last_assistant_id: last && (last.getAttribute('data-message-id') ||
      last.getAttribute('data-content-search-unit-key') ||
      last.getAttribute('data-chatgpt-search-unit-key')),
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
  const visible = (el) => Boolean(
    el && el.getClientRects && el.getClientRects().length
  );
  const selectors = [
    '#prompt-textarea',
    'textarea[placeholder]',
    '[contenteditable="true"][data-lexical-editor="true"]',
    'main [contenteditable="true"][role="textbox"]',
    'main [contenteditable="true"]',
  ];
  let el = null;
  for (const selector of selectors) {
    el = [...document.querySelectorAll(selector)].find((candidate) =>
      visible(candidate) &&
      !candidate.disabled &&
      candidate.getAttribute('aria-disabled') !== 'true'
    ) || null;
    if (el) break;
  }
  if (!el) return false;
  el.focus();
  if (typeof el.select === 'function' && typeof el.value === 'string') {
    el.select();
  } else {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
  }
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
  setTimeout(() => target.click(), 0);
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
