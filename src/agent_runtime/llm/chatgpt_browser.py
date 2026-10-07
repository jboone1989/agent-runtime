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
import sys
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
        browser_session_id: str = "chatgpt-shared",
        response_timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 0.4,
        stable_seconds: float = 1.6,
        process_lock_path: Path | None = None,
        conversation_state_path: Path | None = None,
        refresh_after_turns: int = 8,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(session_id)):
            raise ValueError("session_id must be non-empty")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(browser_session_id)):
            raise ValueError("browser_session_id must be non-empty")
        if not 5 <= float(response_timeout_seconds) <= 900:
            raise ValueError("response_timeout_seconds must be in [5, 900]")
        if not 0.1 <= float(poll_interval_seconds) <= 5:
            raise ValueError("poll_interval_seconds must be in [0.1, 5]")
        if not 0.5 <= float(stable_seconds) <= 10:
            raise ValueError("stable_seconds must be in [0.5, 10]")
        if isinstance(refresh_after_turns, bool) or not isinstance(refresh_after_turns, int) or refresh_after_turns < 0:
            raise ValueError("refresh_after_turns must be a nonnegative integer")
        self.refresh_after_turns = refresh_after_turns
        self.browser = browser
        self.session_id = str(session_id)
        self.browser_session_id = str(browser_session_id)
        self.response_timeout_seconds = float(response_timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.stable_seconds = float(stable_seconds)
        self.process_lock_path = Path(process_lock_path or (Path.home() / ".local" / "state" / "agent-runtime" / "browser-harness" / "chatgpt-browser.lock"))
        self.conversation_state_path = Path(conversation_state_path or
            self.process_lock_path.with_name(f"{self.process_lock_path.stem}-{self.session_id}-conversation.json"))
        self.last_metadata: dict[str, Any] = {}
        self._request_lock = asyncio.Lock()

    @classmethod
    def from_environment(
        cls,
        *,
        session_id: str | None = None,
    ) -> "ChatGPTBrowserProvider":
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
            candidate = Path(sys.executable).with_name("browser-harness")
            if candidate.is_file():
                executable = str(candidate)
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
                account_id=str(os.environ.get("AGENT_RUNTIME_CHATGPT_ACCOUNT_ID") or "default").strip(),
            ),
        )
        resolved_session_id = str(
            session_id
            if session_id is not None
            else (
                os.environ.get("AGENT_RUNTIME_CHATGPT_SESSION_ID")
                or "chatgpt-cognition"
            )
        ).strip()
        return cls(
            browser=browser,
            session_id=resolved_session_id,
            browser_session_id=str(
                os.environ.get("AGENT_RUNTIME_CHATGPT_BROWSER_SESSION_ID")
                or "chatgpt-shared"
            ).strip(),
            response_timeout_seconds=response_timeout,
            process_lock_path=state_dir / (f"chatgpt-profile-{profile_id}.lock" if browser.runtime.account_id == "default"
                                          else f"chatgpt-profile-{profile_id}-{browser.runtime.account_id}.lock"),
            refresh_after_turns=int(os.environ.get("AGENT_RUNTIME_CHATGPT_REFRESH_AFTER_TURNS", "8")),
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
                completed = journal.get("last_completed") or {}
                if (not pending and completed.get("payload_sha256")
                        == hashlib.sha256(outbound.encode()).hexdigest()):
                    return self._record_result({**completed, "recovered": True})
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

    def _archive_server_missing_pending(
        self,
        journal: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        pending = dict(journal.get("pending") or {})
        request_id = str(pending.get("request_id") or "")
        if (
            not request_id
            or pending.get("submission_acknowledged") is not True
            or not pending.get("payload_sha256")
        ):
            raise RuntimeError("Only an acknowledged pending request may be archived as server-missing")
        archive = self.conversation_state_path.with_name(
            self.conversation_state_path.stem
            + "-server-missing-"
            + request_id
            + ".json"
        )
        archived = {
            **pending,
            "status": "SERVER_MISSING_ACCEPTED_TURN",
            "conversation_url": journal.get("conversation_url"),
        }
        if archive.exists():
            existing = json.loads(archive.read_text(encoding="utf-8"))
            if existing.get("payload_sha256") != pending["payload_sha256"]:
                raise RuntimeError("Server-missing pending archive changed")
        else:
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(archived, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
        settled = dict(journal)
        settled["last_failed_request"] = {
            "request_id": request_id,
            "payload_sha256": pending["payload_sha256"],
            "reason": "SERVER_MISSING_ACCEPTED_TURN",
            "failure_cause": (
                "same-conversation reload contained a different last user turn; "
                "the locally acknowledged send was not durably present on the server"
            ),
            "archive": str(archive),
        }
        settled["pending"] = None
        self.conversation_state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.conversation_state_path.parent,
            prefix=self.conversation_state_path.name + ".",
            delete=False,
        ) as handle:
            tmp = Path(handle.name)
            os.chmod(tmp, 0o600)
            json.dump(settled, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.conversation_state_path)
        return settled, request_id

    def _shared_browser_reserved_by_other_session(self) -> bool:
        pattern = "chatgpt-profile-*-conversation.json"
        for path in self.conversation_state_path.parent.glob(pattern):
            if path == self.conversation_state_path:
                continue
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except json.JSONDecodeError:
                continue
            if not isinstance(body, dict):
                continue
            pending = body.get("pending")
            if not isinstance(pending, dict) or not pending:
                continue
            if str(pending.get("browser_session_id") or "") == self.browser_session_id:
                return True
        return False

    def _physical_browser_session_id(self, journal: Mapping[str, Any]) -> str:
        pending = journal.get("pending")
        if isinstance(pending, Mapping) and pending:
            pinned = str(pending.get("browser_session_id") or "").strip()
            return pinned or self.session_id
        if self._shared_browser_reserved_by_other_session():
            return self.session_id
        return self.browser_session_id

    def _pending_browser_target_ids(self) -> set[str]:
        protected: set[str] = set()
        load_target = getattr(self.browser, "_load_target_id", None)
        if not callable(load_target):
            return protected
        for path in self.conversation_state_path.parent.glob(
            "chatgpt-profile-*-conversation.json"
        ):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if not isinstance(body, dict):
                continue
            pending = body.get("pending")
            if not isinstance(pending, dict) or not pending:
                continue
            logical = str(body.get("session_id") or "").strip()
            browser_session = str(
                pending.get("browser_session_id") or logical
            ).strip()
            if not browser_session:
                continue
            target_id = load_target(browser_session)
            if target_id:
                protected.add(target_id)
        return protected

    def _prune_idle_chatgpt_tabs(self, physical_session_id: str) -> int:
        if physical_session_id != self.browser_session_id:
            return 0
        load_target = getattr(self.browser, "_load_target_id", None)
        if not callable(load_target):
            return 0
        keep_target = load_target(physical_session_id)
        if not keep_target:
            return 0
        protected = self._pending_browser_target_ids()
        protected.add(keep_target)
        script = (
            f"protected = {sorted(protected)!r}\n"
            "closed = []\n"
            "for row in list_tabs(include_chrome=False):\n"
            "    candidate = str(row.get('targetId') or row.get('target_id') or '')\n"
            "    url = str(row.get('url') or '')\n"
            "    if candidate and candidate not in protected and url.startswith('https://chatgpt.com/'):\n"
            "        answer = cdp('Target.closeTarget', targetId=candidate)\n"
            "        if not isinstance(answer, dict) or answer.get('success') is not False:\n"
            "            closed.append(candidate)\n"
            "result = {'closed_target_ids': closed, 'closed_count': len(closed)}\n"
        )
        body = self.browser.run_session_script(
            physical_session_id,
            script,
            timeout_seconds=15.0,
        )
        return int(body.get("closed_count") or 0)

    def _run_turn(
        self,
        outbound: str,
        timeout: float,
        journal: dict[str, Any],
        *,
        physical_session_id: str | None = None,
    ) -> dict[str, Any]:
        if physical_session_id is None:
            physical_session_id = self._physical_browser_session_id(journal)
        try:
            return self._run_turn_once(outbound, timeout, journal)
        except RuntimeError as exc:
            detail = str(exc)
            if "No target with given id found" in detail:
                recovered = self._load_conversation()
                pending = recovered.get("pending")
                url = recovered.get("conversation_url")
                if not url or (pending and pending.get("outbound") != outbound):
                    raise
                self.browser.invoke(BROWSER_NAVIGATE, {
                    "session_id": physical_session_id,
                    "url": url,
                    "reuse_current": True,
                })
                evaluate = getattr(self.browser, "evaluate", None)
                if (
                    isinstance(pending, Mapping)
                    and pending.get("submission_acknowledged") is True
                    and callable(evaluate)
                ):
                    state = evaluate(physical_session_id, _CHATGPT_STATE_EXPRESSION)
                    request_marker = re.search(
                        r"Runtime request fingerprint: ([0-9a-f]{64})",
                        outbound,
                    )
                    observed_user = " ".join(
                        str(state.get("last_user_text") or "").split()
                    )
                    current_user_id = state.get("last_user_id")
                    submitted_user_id = pending.get("submitted_user_id")
                    server_missing = bool(
                        request_marker
                        and state.get("url") == url
                        and state.get("generating") is False
                        and observed_user
                        and request_marker.group(0) not in observed_user
                        and current_user_id
                        and current_user_id != submitted_user_id
                    )
                    if server_missing:
                        settled, missing_request_id = (
                            self._archive_server_missing_pending(recovered)
                        )
                        body = self._run_turn_once(outbound, timeout, settled)
                        return {
                            **body,
                            "target_rebound": True,
                            "server_missing_resubmission_of_request_id": (
                                missing_request_id
                            ),
                        }
                body = self._run_turn_once(outbound, timeout, recovered)
                return {**body, "target_rebound": True}
            if "CHATGPT_WEB_USAGE_LIMIT" in detail:
                # A native quota alert can be stale DOM left on an otherwise
                # usable owned conversation. Confirm it once by reloading the
                # exact same server conversation before classifying the model
                # channel unavailable. Never send or adopt a different tab
                # during this confirmation.
                recovered = self._load_conversation()
                pending = recovered.get("pending") or {}
                limit = recovered.get("last_usage_limit") or {}
                url = recovered.get("conversation_url")
                if (not url or (limit.get("url") and limit.get("url") != url)
                        or (pending and pending.get("outbound") != outbound)):
                    raise
                self.browser.reload_owned_session(
                    physical_session_id,
                    expected_url=url,
                )
                usage_limit_probed_at = None
                if (pending.get("submission_acknowledged") is True
                        and limit.get("status") == "CHATGPT_WEB_USAGE_LIMIT"
                        and limit.get("request_id") == pending.get("request_id")
                        and limit.get("url") == url):
                    usage_limit_probed_at = time.time()
                try:
                    body = self._run_turn_once(
                        outbound,
                        timeout,
                        recovered,
                        usage_limit_probed_at=usage_limit_probed_at,
                    )
                except RuntimeError as retry_exc:
                    retry_detail = str(retry_exc)
                    if "CHATGPT_WEB_USAGE_LIMIT" in retry_detail:
                        raise RuntimeError(
                            'CHATGPT_WEB_USAGE_LIMIT: native page still says '
                            '"You have hit your limit; please try again later" '
                            'after same-conversation reload confirmation; '
                            'original pending receipt preserved'
                        ) from retry_exc
                    if ("exact restored payload archived without resubmitting" in retry_detail
                            or "owned failed payload archived without resubmitting" in retry_detail):
                        settled = self._load_conversation()
                        failure = settled.get("last_failed_request") or {}
                        expected_hash = hashlib.sha256(outbound.encode()).hexdigest()
                        if (settled.get("pending")
                                or not (failure.get("composer_restored")
                                        or failure.get("generation_failure_confirmed"))
                                or failure.get("payload_sha256") != expected_hash):
                            raise
                        archive = json.loads(
                            Path(failure["archive"]).read_text(encoding="utf-8")
                        )
                        if (archive.get("outbound") != outbound
                                or archive.get("status") != "GENERATION_FAILED"):
                            raise
                        body = self._retry_reloaded_turn(
                            outbound,
                            timeout,
                            settled,
                        )
                        return {
                            **body,
                            "usage_limit_rechecked": True,
                            "generation_recovery_of_request_id": failure["request_id"],
                        }
                    raise
                return {**body, "usage_limit_rechecked": True}
            response_stalled = False
            if "ChatGPT Web response exceeded" in detail:
                diagnostic_start = detail.find("{", detail.find("ChatGPT Web response exceeded"))
                if diagnostic_start >= 0:
                    try:
                        diagnostics, _ = json.JSONDecoder().raw_decode(detail[diagnostic_start:])
                    except ValueError:
                        diagnostics = {}
                    response_stalled = (
                        diagnostics.get("generating") is False
                        or int(diagnostics.get("poll_timeout_count") or 0) > 0
                    )
            transport_stalled = (
                "Page.enable timed out" in detail
                or "ChatGPT live composer state could not be read within 30s" in detail
                or response_stalled
            )
            if transport_stalled:
                recovered = self._load_conversation()
                pending = recovered.get("pending")
                url = recovered.get("conversation_url")
                if not url and (not pending or (
                        pending.get("submission_acknowledged") is True
                        and pending.get("outbound") == outbound)):
                    observed_url = self.browser.owned_session_url(physical_session_id)
                    if re.fullmatch(r"https://chatgpt\.com/c/[A-Za-z0-9_-]+", observed_url):
                        url = observed_url
                    elif not pending and observed_url == self.chat_url:
                        url = observed_url
                # Restore the same server conversation, never a fresh chat.
                # A pending accepted turn is reconciled with its original
                # payload; the recovery transaction cannot send another turn.
                if not url or (pending and (
                        pending.get("submission_acknowledged") is not True
                        or pending.get("outbound") != outbound)):
                    raise
                if "ChatGPT Web response exceeded" in detail and not pending:
                    raise
                self.browser.replace_owned_session(physical_session_id, expected_url=url)
                body = self._run_turn_once(outbound, timeout, recovered)
                return {**body, "renderer_replaced": True}
            if "CONVERSATION_CAPACITY_REACHED_BEFORE_SUBMISSION" in detail:
                # The unsent turn has no receipt to replay. The transaction
                # confirms the explicit page limit and durably changes carriers.
                return self._run_turn_once(outbound, timeout, self._load_conversation())
            if ("exact restored payload archived without resubmitting" in detail
                    or "owned failed payload archived without resubmitting" in detail):
                # The transaction has durably settled an explicit generation
                # failure. Reload the same conversation and retry this payload
                # once; ambiguous or still-pending submissions never enter here.
                recovered = self._load_conversation()
                failure = recovered.get("last_failed_request") or {}
                expected_hash = hashlib.sha256(outbound.encode()).hexdigest()
                if (recovered.get("pending") or not (failure.get("composer_restored") or failure.get("generation_failure_confirmed"))
                        or failure.get("payload_sha256") != expected_hash):
                    raise
                archive = json.loads(Path(failure["archive"]).read_text(encoding="utf-8"))
                if archive.get("outbound") != outbound or archive.get("status") != "GENERATION_FAILED":
                    raise
                url = recovered.get("conversation_url")
                if not url:
                    raise
                self.browser.reload_owned_session(physical_session_id, expected_url=url)
                body = self._retry_reloaded_turn(outbound, timeout, recovered)
                return {**body, "renderer_reloaded": True,
                        "generation_recovery_of_request_id": failure["request_id"]}
            if "Runtime.evaluate" not in detail or "timed out" not in detail:
                # Composer readiness failures occur before pending is created.
                # Reloading a pending submission here would lose its ownership.
                unsent_failure = any(marker in detail for marker in (
                    "ChatGPT live composer did not become ready",
                    "ChatGPT live composer state could not be read",
                    "ChatGPT conversation failed to load after Retry",
                    "ChatGPT Send control did not become enabled",
                    "ChatGPT composer changed while awaiting Send readiness",
                ))
                recovered = self._load_conversation()
                url = recovered.get("conversation_url")
                if not unsent_failure or recovered.get("pending") or not url:
                    raise
                self.browser.reload_owned_session(physical_session_id, expected_url=url)
                body = self._retry_reloaded_turn(outbound, timeout, recovered)
                return {**body, "renderer_reloaded": True}
            # The profile lock is still held. Restore only this owned renderer,
            # then resume the durable receipt, preserving submission identity.
            recovered = self._load_conversation()
            url = recovered.get("conversation_url")
            if not url:
                raise
            self.browser.reload_owned_session(physical_session_id, expected_url=url)
            body = self._retry_reloaded_turn(outbound, timeout, recovered)
            return {**body, "renderer_reloaded": True}

    def _retry_reloaded_turn(
        self,
        outbound: str,
        timeout: float,
        journal: dict[str, Any],
        *,
        physical_session_id: str | None = None,
    ) -> dict[str, Any]:
        if physical_session_id is None:
            physical_session_id = self._physical_browser_session_id(journal)
        try:
            return self._run_turn_once(outbound, timeout, journal)
        except RuntimeError as exc:
            detail = str(exc)
            if not any(marker in detail for marker in (
                "exact restored payload archived without resubmitting",
                "owned failed payload archived without resubmitting",
                "ChatGPT live composer did not become ready",
                "ChatGPT live composer state could not be read",
                "ChatGPT conversation failed to load after Retry",
                "Runtime.evaluate timed out",
            )):
                raise
            settled = self._load_conversation()
            failure = settled.get("last_failed_request") or {}
            if (settled.get("pending") or not settled.get("conversation_url")
                    or failure.get("reason") != "GENERATION_FAILED"
                    or not (failure.get("composer_restored") or failure.get("generation_failure_confirmed"))
                    or not failure.get("archive")):
                raise
            archive = json.loads(Path(failure["archive"]).read_text(encoding="utf-8"))
            archived_outbound = str(archive.get("outbound") or "")
            if (archive.get("status") != "GENERATION_FAILED" or not archived_outbound
                    or hashlib.sha256(archived_outbound.encode()).hexdigest() != failure.get("payload_sha256")):
                raise
            # Only a durably failed carrier may be replaced, after reload failed.
            # Pending and ambiguous requests remain on the reconciliation path.
            carrier_archive = self.conversation_state_path.with_name(
                self.conversation_state_path.stem + "-failed-carrier-" + failure["request_id"] + ".json")
            if not carrier_archive.exists():
                with carrier_archive.open("x", encoding="utf-8") as handle:
                    os.chmod(carrier_archive, 0o600)
                    json.dump({"reason": "GENERATION_FAILED_AFTER_RELOAD", "conversation": settled},
                              handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
            settled["conversation_transition"] = {
                "stage": "NEW_CHAT_REQUESTED", "old_url": settled["conversation_url"],
                "archive": str(carrier_archive), "requested_at": time.time(),
                "reason": "GENERATION_FAILED_AFTER_RELOAD",
            }
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=self.conversation_state_path.parent, delete=False) as handle:
                os.chmod(handle.name, 0o600)
                json.dump(settled, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.conversation_state_path)
            return self._run_turn_once(outbound, timeout, settled)

    def _run_turn_once(
        self,
        outbound: str,
        timeout: float,
        journal: dict[str, Any],
        *,
        physical_session_id: str | None = None,
        usage_limit_probed_at: float | None = None,
    ) -> dict[str, Any]:
        if physical_session_id is None:
            physical_session_id = self._physical_browser_session_id(journal)
        hard_response_timeout = min(900.0, max(timeout, timeout * 3.0))
        url = journal.get("conversation_url")
        page_refreshed = False
        renderer_replaced = False
        completed = journal.get("page_turns_since_refresh")
        if completed is not None and (isinstance(completed, bool) or not isinstance(completed, int) or completed < 0):
            raise ChatGPTBrowserProviderError("Invalid completed page-turn counter")
        transition = journal.get("conversation_transition")
        if (transition and transition.get("reason") == "GENERATION_FAILED_AFTER_RELOAD"
                and not journal.get("pending")):
            # Restore the fresh carrier in the same Runtime-owned tab/profile.
            self.browser.invoke(BROWSER_NAVIGATE, {
                "session_id": physical_session_id, "url": self.chat_url, "reuse_current": False,
            })
        if url and not transition:
            # Restore the logical conversation before inspecting/recycling the
            # shared renderer, which may still show another session's URL.
            self.browser.invoke(BROWSER_NAVIGATE, {
                "session_id": physical_session_id, "url": url, "reuse_current": True,
            })
        elif not transition and not journal.get("pending"):
            self.browser.invoke(BROWSER_NAVIGATE, {
                "session_id": physical_session_id,
                "url": self.chat_url,
                "reuse_current": True,
            })
        pending = journal.get("pending") or {}
        if (
            url
            and pending.get("outbound") == outbound
            and pending.get("submission_acknowledged") is True
        ):
            state = self.browser.evaluate(
                physical_session_id,
                _CHATGPT_STATE_EXPRESSION,
            )
            request_marker = re.search(
                r"Runtime request fingerprint: ([0-9a-f]{64})",
                outbound,
            )
            observed_user = " ".join(
                str(state.get("last_user_text") or "").split()
            )
            current_user_id = state.get("last_user_id")
            submitted_user_id = pending.get("submitted_user_id")
            server_missing = bool(
                request_marker
                and state.get("url") == url
                and state.get("generating") is False
                and observed_user
                and request_marker.group(0) not in observed_user
                and current_user_id
                and submitted_user_id
                and current_user_id != submitted_user_id
            )
            if server_missing:
                settled, missing_request_id = (
                    self._archive_server_missing_pending(journal)
                )
                body = self._run_turn_once(
                    outbound,
                    timeout,
                    settled,
                    physical_session_id=physical_session_id,
                )
                return {
                    **body,
                    "server_missing_resubmission_of_request_id": (
                        missing_request_id
                    ),
                }
        if url and not transition and not journal.get("pending") and self.refresh_after_turns:
            page_refreshed = (
                completed is None and bool(journal.get("last_completed"))
                or completed is not None and completed >= self.refresh_after_turns
            )
            if page_refreshed:
                state = self.browser.evaluate(physical_session_id, _CHATGPT_STATE_EXPRESSION)
                if state.get("usage_limited"):
                    raise ChatGPTBrowserProviderError("CHATGPT_WEB_USAGE_LIMIT: native page says usage limit reached; page refresh deferred")
                if state.get("generating"):
                    raise ChatGPTBrowserProviderError("Cannot refresh a ChatGPT page with an unowned generation")
                self.browser.replace_owned_session(
                    physical_session_id,
                    expected_url=url,
                )
                renderer_replaced = True
        limit = journal.get("last_usage_limit") or {}
        prior_probe = journal.get("usage_limit_probe") or {}
        quota_seen_at = (
            prior_probe.get("refreshed_at") if prior_probe.get("request_id") == pending.get("request_id")
            else limit.get("observed_at")
        )
        if (url and pending.get("outbound") == outbound
                and pending.get("submission_acknowledged") is True
                and limit.get("status") == "CHATGPT_WEB_USAGE_LIMIT"
                and limit.get("request_id") == pending.get("request_id")
                and limit.get("url") == url
                and quota_seen_at is not None
                and time.time() - float(quota_seen_at) >= 300):
            state = self.browser.evaluate(physical_session_id, _CHATGPT_STATE_EXPRESSION)
            if state.get("url") == url and not state.get("generating"):
                # A quota toast can survive indefinitely in this page. Recheck
                # once per cooldown on the same carrier; never infer a fresh
                # account limit from a cached alert or interrupt generation.
                self.browser.reload_owned_session(physical_session_id, expected_url=url)
                usage_limit_probed_at = time.time()
        # An unbound in-flight turn must use its persisted target; adopting a
        # different tab would invent provenance for an ambiguous submission.
        with tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", dir=self.process_lock_path.parent,
            prefix="chatgpt-outbound-", suffix=".txt",
        ) as payload:
            payload.write(outbound)
            payload.flush()
            body = self.browser.run_session_script(
                physical_session_id,
                self._transaction_script(
                    Path(payload.name),
                    timeout,
                    physical_session_id=physical_session_id,
                    hard_response_timeout=hard_response_timeout,
                    page_refreshed=page_refreshed,
                    usage_limit_probed_at=usage_limit_probed_at,
                ),
                timeout_seconds=min(900.0, hard_response_timeout + 45.0),
            )
            body = {
                **body,
                "pruned_chatgpt_tabs": self._prune_idle_chatgpt_tabs(physical_session_id),
            }
            if renderer_replaced:
                body = {**body, "renderer_replaced": True}
            return body

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
            "page_refreshed": body.get("page_refreshed"),
            "page_refresh_count": body.get("page_refresh_count"),
            "page_garbage_collected": bool(body.get("page_garbage_collected")),
            "renderer_reloaded": bool(body.get("renderer_reloaded")),
            "renderer_replaced": bool(body.get("renderer_replaced")),
            "target_rebound": bool(body.get("target_rebound")),
            "pruned_chatgpt_tabs": int(body.get("pruned_chatgpt_tabs") or 0),
            "generation_recovery_of_request_id": body.get("generation_recovery_of_request_id"),
            "receipt_ownership": body.get("receipt_ownership"),
            "conversation_rolled_over": bool(body.get("conversation_rolled_over")),
            "conversation_lineage_depth": int(body.get("conversation_lineage_depth") or 0),
        }
        return response

    def _transaction_script(
        self,
        outbound_path: Path,
        timeout: float,
        *,
        physical_session_id: str | None = None,
        hard_response_timeout: float | None = None,
        page_refreshed: bool = False,
        usage_limit_probed_at: float | None = None,
    ) -> str:
        if physical_session_id is None:
            physical_session_id = self.browser_session_id
        if hard_response_timeout is None:
            hard_response_timeout = min(900.0, max(timeout, timeout * 3.0))
        return (
            "import json\nimport time\nfrom pathlib import Path\n"
            f"state_expr = {json.dumps(_CHATGPT_STATE_EXPRESSION)}\n"
            f"new_chat_expr = {json.dumps(_NEW_CHAT_EXPRESSION)}\n"
            f"focus_expr = {json.dumps(_FOCUS_COMPOSER_EXPRESSION)}\n"
            f"send_expr = {json.dumps(_SEND_EXPRESSION)}\n"
            f"retry_generation_expr = {json.dumps(_RETRY_GENERATION_EXPRESSION)}\n"
            f"retry_conversation_load_expr = {json.dumps(_RETRY_CONVERSATION_LOAD_EXPRESSION)}\n"
            f"outbound = Path({json.dumps(str(outbound_path))}).read_text(encoding='utf-8')\n"
            f"journal_path = Path({json.dumps(str(self.conversation_state_path))})\n"
            f"session_id = {json.dumps(self.session_id)}\n"
            f"browser_session_id = {json.dumps(physical_session_id)}\n"
            f"response_timeout = {float(timeout)!r}\n"
            f"hard_response_timeout = {float(hard_response_timeout)!r}\n"
            f"usage_limit_probed_at = {usage_limit_probed_at!r}\n"
            f"page_refreshed = {page_refreshed!r}\n"
            f"page_refresh_requested_at = {time.time()!r}\n"
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
        body = "\n".join(parts)
        fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return body + "\n\nRuntime request fingerprint: " + fingerprint


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
conversation_rolled_over = False
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
def archive_failed_request(reason, *, composer_restored, failure_confirmed):
    archive = journal_path.with_name(journal_path.stem + '-generation-failed-' + pending['request_id'] + '.json')
    if archive.exists():
        assert json.loads(archive.read_text())['payload_sha256'] == pending['payload_sha256'], 'Failed request archive changed'
    else:
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump({**pending, 'status': 'GENERATION_FAILED', 'reason': reason,
                'composer_restored': composer_restored,
                'generation_failure_confirmed': failure_confirmed}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
    journal['last_failed_request'] = {'request_id': pending['request_id'],
        'payload_sha256': pending['payload_sha256'], 'reason': 'GENERATION_FAILED',
        'failure_cause': reason, 'composer_restored': composer_restored,
        'generation_failure_confirmed': failure_confirmed, 'archive': str(archive)}
    journal['pending'] = None
    save_journal()

if usage_limit_probed_at is not None:
    assert pending and pending.get('submission_acknowledged') is True, 'Quota probe requires an accepted request'
    limit = journal.get('last_usage_limit') or {}
    assert limit.get('request_id') == pending['request_id'], 'Quota probe request changed'
    journal['usage_limit_probe'] = {
        'request_id': pending['request_id'], 'payload_sha256': pending['payload_sha256'],
        'url': journal.get('conversation_url'), 'refreshed_at': usage_limit_probed_at,
    }
    save_journal()

def read_state():
    state = js(state_expr)
    if state.get('usage_limited'):
        expected = journal.get('conversation_url')
        assert not expected or state.get('url') == expected, 'Usage-limit page is not the owned conversation'
        # The native notice establishes an account/model limit, not a renderer
        # failure or a completed answer. Preserve accepted and ambiguous turns.
        journal['last_usage_limit'] = {
            'status': 'CHATGPT_WEB_USAGE_LIMIT',
            'observed_at': time.time(),
            'url': state.get('url'),
            'request_id': (journal.get('pending') or {}).get('request_id'),
            'pending_preserved': bool(journal.get('pending')),
            'notice': "You've hit your limit. Please try again later.",
        }
        save_journal()
        raise RuntimeError('CHATGPT_WEB_USAGE_LIMIT: native page says "You have hit your limit; please try again later"; original pending receipt preserved; no request submitted or renderer replaced')
    return state

def collect_page_garbage():
    # Only called before a verifiably unsent turn or after a durable answer.
    # This releases unreachable editor/render objects; no page/storage changes.
    collect = globals().get('cdp')
    if not callable(collect):
        return False
    try:
        collect('HeapProfiler.collectGarbage', _response_timeout=10.0)
        return True
    except Exception:
        # Cleanup must not turn an already-persisted answer into a lost receipt.
        return False

def is_fresh_conversation(state):
    from urllib.parse import urlsplit
    parsed = urlsplit(str(state.get('url') or ''))
    return (parsed.scheme == 'https' and parsed.netloc == 'chatgpt.com'
            and parsed.path in ('', '/') and state.get('composer_selector')
            and not state.get('user_count') and not state.get('assistant_count')
            and not state.get('generating') and not state.get('conversation_exhausted'))
def bind_conversation(url):
    if not re.fullmatch(r'https://chatgpt\.com/c/[A-Za-z0-9_-]+', str(url or '')):
        return
    expected = journal.get('conversation_url')
    assert not expected or expected == url, 'ChatGPT conversation binding changed unexpectedly'
    if not expected:
        journal['conversation_url'] = url
        lineage = journal.get('conversation_lineage') or []
        if lineage and lineage[-1].get('new_url') is None:
            lineage[-1]['new_url'] = url
        save_journal()
conversation_load_retry_count = 0
accepted_pending = bool(
    pending and pending.get('submission_acknowledged') is True
)
ready_deadline = time.monotonic() + 30.0
while True:
    try:
        state = read_state()
    except RuntimeError as exc:
        if 'Runtime.evaluate timed out' not in str(exc):
            raise
        if time.monotonic() >= ready_deadline:
            raise RuntimeError('ChatGPT live composer state could not be read within 30s') from exc
        time.sleep(poll_interval)
        continue
    assert not state.get('login_required'), 'ChatGPT browser profile is not authenticated'
    if state.get('conversation_load_failed'):
        if conversation_load_retry_count >= 1:
            raise RuntimeError('ChatGPT conversation failed to load after Retry')
        retried = js(retry_conversation_load_expr)
        assert retried is True, 'ChatGPT conversation load failure has no unique usable Retry control'
        conversation_load_retry_count += 1
        ready_deadline = time.monotonic() + 30.0
        time.sleep(poll_interval)
        continue
    if accepted_pending or state.get('composer_selector'):
        break
    if time.monotonic() >= ready_deadline:
        raise RuntimeError('ChatGPT live composer did not become ready within 30s')
    time.sleep(poll_interval)
transition = journal.get('conversation_transition')
if transition or (state.get('conversation_exhausted') and not pending):
    assert not pending, 'An in-flight request must be reconciled before conversation rollover'
    assert not state.get('generating'), 'Cannot replace a conversation with an unowned generation'
    if not transition:
        old_url = journal.get('conversation_url')
        assert old_url and state.get('url') == old_url, 'Exhausted page is not the owned conversation'
        archive = journal_path.with_name(journal_path.stem + '-exhausted-' + old_url.rsplit('/', 1)[-1] + '.json')
        snapshot = {'schema_version': 1, 'reason': 'CONVERSATION_MAXIMUM_LENGTH',
                    'conversation': journal, 'unsent_composer': state.get('composer_text') or '',
                    'continuing_outbound': outbound}
        if archive.exists():
            saved = json.loads(archive.read_text())
            assert saved['conversation']['conversation_url'] == old_url, 'Conversation archive changed'
        else:
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(snapshot, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            directory_fd = os.open(archive.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        transition = {'stage': 'NEW_CHAT_REQUESTED', 'old_url': old_url,
                      'archive': str(archive), 'requested_at': time.time()}
        journal['conversation_transition'] = transition
        save_journal()
    assert transition['stage'] == 'NEW_CHAT_REQUESTED', 'Unknown conversation transition phase'
    assert journal.get('conversation_url') == transition['old_url'], 'Conversation transition binding changed'
    if state.get('url') == transition['old_url']:
        assert state.get('conversation_exhausted'), 'Owned page no longer exposes its explicit length limit'
        assert js(new_chat_expr) is True, 'Exhausted conversation New chat control is unavailable'
        fresh_deadline = time.monotonic() + 30.0
        while time.monotonic() < fresh_deadline:
            state = read_state()
            assert not state.get('login_required'), 'Authentication was lost during conversation rollover'
            if is_fresh_conversation(state):
                break
            time.sleep(poll_interval)
        else:
            raise RuntimeError('Exhausted conversation transition did not reach a fresh chat')
    assert is_fresh_conversation(state), 'Conversation transition has an unexpected live page'
    journal.setdefault('conversation_lineage', []).append({**transition, 'new_url': None,
                                                        'completed_at': time.time()})
    journal['conversation_url'] = None
    journal['conversation_transition'] = None
    journal['page_turns_since_refresh'] = 0
    save_journal()
    page_refreshed = False
    conversation_rolled_over = True
if journal.get('conversation_url'):
    assert state.get('url') == journal['conversation_url'], 'ChatGPT failed to restore its bound conversation'
needs_fresh = not journal.get('conversation_url') and not pending and bool(state.get('user_count') or state.get('assistant_count') or '/c/' in str(state.get('url') or ''))
if needs_fresh:
    clicked = js(new_chat_expr)
    assert clicked is True, 'ChatGPT new-chat control is unavailable'
    fresh_deadline = time.monotonic() + 15.0
    while time.monotonic() < fresh_deadline:
        state = read_state()
        assert not state.get('login_required'), 'ChatGPT authentication was lost while starting a new chat'
        if state.get('composer_selector') and '/c/' not in str(state.get('url') or '') and not state.get('user_count') and not state.get('assistant_count'):
            break
        time.sleep(poll_interval)
    else:
        raise RuntimeError('ChatGPT did not enter a fresh chat within 15s')
bind_conversation(state.get('url'))
assert not state.get('generating') or pending, 'ChatGPT has an unowned generation in progress'
if page_refreshed:
    assert not pending, 'A pending ChatGPT request must never be refreshed'
    journal['page_turns_since_refresh'] = 0
    journal['page_refresh_count'] = int(journal.get('page_refresh_count') or 0) + 1
    journal['last_page_refresh'] = {
        'requested_at': page_refresh_requested_at,
        'conversation_url': journal['conversation_url'],
        'after_request_id': (journal.get('last_completed') or {}).get('request_id'),
    }
    save_journal()
# Refill only a verifiably unsent request, preserving its request identity.
# Accepted and ambiguous submissions remain on the response-recovery path.
marker = re.search(r'Runtime request fingerprint: ([0-9a-f]{64})', outbound)
observed_user = ' '.join(str(state.get('last_user_text') or '').split())
refill_pending = bool(
    pending and pending.get('submission_acknowledged') is False
    and not state.get('generating')
    and state.get('user_count') == pending['baseline_user_count']
    and state.get('assistant_count') == pending['baseline_assistant_count']
    and state.get('last_assistant_id') == pending.get('baseline_assistant_id')
    and 'last_user_text' in state
    and observed_user != ' '.join(outbound.split())
    and not (marker and marker.group(0) in observed_user)
)
if refill_pending:
    pending['stage'] = 'RESTORING_UNSENT'
    pending['resubmission_count'] = int(pending.get('resubmission_count') or 0) + 1
    save_journal()
if pending and not refill_pending:
    baseline_user_count = int(pending['baseline_user_count'])
    baseline_user_id = pending.get('baseline_user_id')
    baseline_assistant_count = int(pending['baseline_assistant_count'])
    baseline_assistant_id = pending.get('baseline_assistant_id')
    sent = pending.get('submission_acknowledged')
else:
    collect_page_garbage()
    focused = js(focus_expr)
    assert focused is True, 'ChatGPT composer could not be focused'
    # Keep each Runtime.evaluate request well below Browser Harness' framed IPC
    # boundary even when the prompt contains multibyte text.
    input_chunk_chars = 4096
    for start in range(0, len(outbound), input_chunk_chars):
        end = min(len(outbound), start + input_chunk_chars)
        chunk = outbound[start:end]
        insert_expr = ("(() => {const editor=()=>[...document.querySelectorAll('main [contenteditable],textarea[placeholder]')].find(el=>(el.isContentEditable || el.tagName.toLowerCase()==='textarea') && el.getClientRects().length);"
                       "if(!editor()) return false;"
                       "setTimeout(() => {const el=editor(); if(!el) return; el.focus(); document.execCommand('insertText', false, "
                       + json.dumps(chunk, ensure_ascii=False)
                       + " );}, 0); return true;})()")
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
                typed = read_state()
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
    typed = read_state()
    expected_text = ' '.join(outbound.split())
    actual_text = ' '.join(str(typed.get('composer_text') or '').split())
    assert actual_text == expected_text, f'ChatGPT composer fill verification failed: expected_chars={len(expected_text)} actual_chars={len(actual_text)}'
    send_deadline = time.monotonic() + 30.0
    while not typed.get('send_ready'):
        if typed.get('conversation_exhausted'):
            raise RuntimeError('CONVERSATION_CAPACITY_REACHED_BEFORE_SUBMISSION: owned conversation reached its maximum length')
        if time.monotonic() >= send_deadline:
            raise RuntimeError('ChatGPT Send control did not become enabled within 30s')
        time.sleep(poll_interval)
        typed = read_state()
        assert not typed.get('login_required'), 'ChatGPT authentication was lost before submission'
        actual_text = ' '.join(str(typed.get('composer_text') or '').split())
        assert actual_text == expected_text, 'ChatGPT composer changed while awaiting Send readiness'
    baseline_user_count = int(typed.get('user_count') or 0)
    baseline_user_id = typed.get('last_user_id')
    baseline_assistant_count = int(typed.get('assistant_count') or 0)
    baseline_assistant_id = typed.get('last_assistant_id')
    pending = {'request_id': pending['request_id'] if refill_pending else uuid.uuid4().hex,
        'resubmission_count': pending['resubmission_count'] if refill_pending else 0,
        'outbound': outbound,
        'payload_sha256': hashlib.sha256(outbound.encode()).hexdigest(),
        'browser_session_id': pending.get('browser_session_id', browser_session_id)
            if refill_pending else browser_session_id,
        'baseline_user_count': baseline_user_count,
        'baseline_user_id': baseline_user_id,
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
response_started = time.monotonic()
deadline = response_started + response_timeout
hard_deadline = response_started + hard_response_timeout
last_text = ''
stable_since = None
rejection_since = None
failed_generation_since = None
while time.monotonic() < min(deadline, hard_deadline):
    try:
        state = read_state()
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
    observed_user = ' '.join(str(state.get('last_user_text') or '').split())
    current_user_id = state.get('last_user_id')
    request_marker = re.search(r'Runtime request fingerprint: ([0-9a-f]{64})', outbound)
    text_owned_user = (
        'last_user_text' not in state
        or (bool(request_marker) and request_marker.group(0) in observed_user)
        or observed_user == ' '.join(outbound.split())
    )
    submitted_user_id = pending.get('submitted_user_id')
    if not submitted_user_id and text_owned_user and current_user_id:
        pending['submitted_user_id'] = current_user_id
        submitted_user_id = current_user_id
        save_journal()
    # Fallback turn indexes can change after reload or virtualization. The
    # exact request fingerprint remains authoritative; a recycled wrapper index
    # alone cannot prove ownership when visible user text contradicts it.
    stable_submitted_user = bool(
        submitted_user_id
        and not str(submitted_user_id).startswith('fallback-turn-')
        and current_user_id == submitted_user_id
    )
    bound_fallback_user = bool(
        submitted_user_id
        and str(submitted_user_id).startswith('fallback-turn-')
        and baseline_user_id
        and current_user_id == submitted_user_id
        and submitted_user_id != baseline_user_id
        and pending.get('submission_acknowledged') is True
        and state.get('url') == journal.get('conversation_url')
    )
    matched_user = text_owned_user or stable_submitted_user or bound_fallback_user
    current_id = state.get('last_assistant_id')
    now = time.monotonic()
    if state.get('generating'):
        deadline = min(hard_deadline, max(deadline, now + response_timeout))
    new_answer = (
        (current_id != baseline_assistant_id)
        if current_id and baseline_assistant_id
        else assistant_count > baseline_assistant_count
    )
    # ChatGPT virtualizes long conversations, so visible message counts may
    # shrink and wrapper IDs may be recycled. If the exact owned user turn is
    # visible and the last assistant node is after it in DOM order, that is a
    # stronger response receipt than a monotonic visible-count assumption.
    if (
        matched_user
        and state.get('assistant_after_last_user') is True
        and text
    ):
        new_answer = True
    # The observed fallback renderer can show only assistant units. Exact
    # verified Send acknowledgement plus a new server message UUID is then the
    # receipt authority; ambiguous sends and old answers never qualify.
    server_id = bool(current_id and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', current_id))
    baseline_server_id = bool(baseline_assistant_id and re.fullmatch(
        r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', baseline_assistant_id))
    first_owned_answer = (baseline_assistant_count == 0 and not baseline_assistant_id
                          and (journal.get('last_completed') or {}).get('url') != state.get('url'))
    assistant_only_receipt = bool(
        state.get('assistant_only_rendering') and not observed_user and not user_count
        and pending.get('submission_acknowledged') is True and server_id and new_answer
        and (baseline_server_id or first_owned_answer)
        and not state.get('composer_text') and state.get('url') == journal.get('conversation_url'))
    matched_user = matched_user or assistant_only_receipt
    limit = journal.get('last_usage_limit') or {}
    probe = journal.get('usage_limit_probe') or {}
    quota_rejected_owned = bool(
        request_marker and request_marker.group(0) in observed_user
        and pending.get('submission_acknowledged') is True
        and limit.get('status') == 'CHATGPT_WEB_USAGE_LIMIT'
        and limit.get('request_id') == pending['request_id']
        and limit.get('url') == state.get('url') == journal.get('conversation_url')
        and probe.get('request_id') == pending['request_id']
        and probe.get('payload_sha256') == pending['payload_sha256']
        and probe.get('url') == state.get('url')
        and float(probe.get('refreshed_at') or 0) >= float(limit.get('observed_at') or 0)
        and not state.get('usage_limited') and not state.get('generating')
        and not new_answer
    )
    if quota_rejected_owned:
        # The exact submitted turn was rejected by a native quota notice. A
        # refreshed carrier now has neither that notice nor an owned answer.
        # Archive this rejected turn so the existing bounded recovery can
        # retry its exact payload once. A live or answered turn is untouched.
        archive_failed_request('NATIVE_USAGE_LIMIT_REJECTION',
            composer_restored=False, failure_confirmed=True)
        raise RuntimeError('ChatGPT quota-rejected owned failed payload archived without resubmitting')
    failed_owned = bool(request_marker and request_marker.group(0) in observed_user
                        and state.get('generation_error') and not state.get('generating')
                        and not new_answer)
    if failed_owned:
        if int(pending.get('generation_retry_count') or 0) >= 1 or pending.get('unknown_error_retry'):
            if failed_generation_since is None:
                failed_generation_since = time.monotonic()
            elif time.monotonic() - failed_generation_since >= 3.0:
                composer_restored = ' '.join(str(state.get('composer_text') or '').split()) == ' '.join(outbound.split())
                failure_confirmed = state.get('generation_error_kind') == 'REQUEST_FAILED'
                if not composer_restored and not failure_confirmed:
                    raise RuntimeError('ChatGPT generation failed after same-request Retry; exact composer restoration is unverified, pending receipt preserved')
                archive_failed_request('Generation error after same-request Retry',
                    composer_restored=composer_restored, failure_confirmed=failure_confirmed)
                if composer_restored:
                    raise RuntimeError('ChatGPT generation failed with Unknown error after same-request Retry; exact restored payload archived without resubmitting')
                raise RuntimeError('ChatGPT generation failed after same-request Retry; owned failed payload archived without resubmitting')
            time.sleep(poll_interval)
            continue
        pending['generation_retry_count'] = 1
        pending['stage'] = 'GENERATION_RETRY_REQUESTED'
        save_journal()
        retry_call = retry_generation_expr.replace('LIVE_STATE', state_expr).replace(
            'EXPECTED_URL', json.dumps(journal['conversation_url'])).replace(
            'BASELINE_ID', json.dumps(baseline_assistant_id)).replace(
            'BASELINE_COUNT', str(baseline_assistant_count)).replace(
            'REQUEST_MARKER', json.dumps(request_marker.group(0)))
        retried = js(retry_call)
        assert retried is True, 'ChatGPT failed generation has no usable Retry control'
        pending['stage'] = 'WAITING_RESPONSE'
        save_journal()
        continue
    else:
        failed_generation_since = None
    rejected_owned = bool(request_marker and request_marker.group(0) in observed_user
                          and state.get('message_too_long') and not state.get('generating')
                          and not new_answer)
    if rejected_owned:
        if rejection_since is None:
            rejection_since = time.monotonic()
        elif time.monotonic() - rejection_since >= 3.0:
            archive = journal_path.with_name(journal_path.stem + '-rejected-' + pending['request_id'] + '.json')
            if archive.exists():
                assert json.loads(archive.read_text())['payload_sha256'] == pending['payload_sha256'], 'Rejected request archive changed'
            else:
                fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as handle:
                    json.dump(pending, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
            journal['last_rejected_request'] = {'request_id': pending['request_id'],
                'payload_sha256': pending['payload_sha256'], 'reason': 'MESSAGE_TOO_LONG',
                'archive': str(archive)}
            journal['pending'] = None
            save_journal()
            raise RuntimeError('ChatGPT rejected the owned request as MESSAGE_TOO_LONG; archived without resubmitting')
    else:
        rejection_since = None
    if new_answer and text and matched_user:
        if text != last_text:
            last_text = text
            stable_since = now
            deadline = min(hard_deadline, max(deadline, now + response_timeout))
        elif stable_since is None:
            stable_since = now
        if not state.get('generating') and stable_since is not None and now - stable_since >= stable_seconds:
            assert journal.get('conversation_url'), 'ChatGPT did not expose a durable conversation URL'
            result = {'response': text, 'url': journal['conversation_url'],
                      'assistant_message_count': assistant_count,
                      'request_id': pending['request_id'], 'recovered': recovering,
                      'payload_sha256': pending['payload_sha256'],
                      'receipt_ownership': (
                          'BOUND_FALLBACK_USER_ID'
                          if bound_fallback_user
                          else 'SUBMITTED_USER_ID'
                          if submitted_user_id and current_user_id == submitted_user_id
                          else 'ACKNOWLEDGED_SEND_AND_NEW_ASSISTANT_UUID'
                          if assistant_only_receipt
                          else 'EXACT_USER_MARKER'
                      )}
            journal['page_turns_since_refresh'] = int(journal.get('page_turns_since_refresh') or 0) + 1
            result['conversation_rolled_over'] = conversation_rolled_over
            result['conversation_lineage_depth'] = len(journal.get('conversation_lineage') or [])
            result['page_refreshed'] = page_refreshed
            result['page_refresh_count'] = int(journal.get('page_refresh_count') or 0)
            journal['last_completed'] = result
            journal['pending'] = None
            save_journal()
            result['page_garbage_collected'] = collect_page_garbage()
            break
    time.sleep(poll_interval)
else:
    elapsed = time.monotonic() - response_started
    diagnostics = {'url': state.get('url'), 'baseline_user_count': baseline_user_count, 'user_count': state.get('user_count'), 'baseline_user_id': baseline_user_id, 'last_user_id': state.get('last_user_id'), 'submitted_user_id': pending.get('submitted_user_id'), 'baseline_assistant_count': baseline_assistant_count, 'assistant_count': state.get('assistant_count'), 'baseline_assistant_id': baseline_assistant_id, 'last_assistant_id': state.get('last_assistant_id'), 'assistant_after_last_user': state.get('assistant_after_last_user'), 'generating': state.get('generating'), 'response_chars': len(str(state.get('last_assistant_text') or '')), 'composer_chars': len(str(state.get('composer_text') or '')), 'poll_timeout_count': poll_timeout_count, 'submission_acknowledged': sent is True, 'pending_user_matches': matched_user, 'stable_for_seconds': (time.monotonic() - stable_since) if stable_since is not None else None, 'idle_timeout_seconds': response_timeout, 'hard_timeout_seconds': hard_response_timeout}
    raise RuntimeError(f'ChatGPT Web response exceeded {elapsed:.0f}s: {json.dumps(diagnostics, sort_keys=True)}')
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
    '[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]',
    'main [contenteditable="true"][role="textbox"]',
    'main [contenteditable="true"]',
  ];
  let composer = null;
  let composerSelector = '';
  for (const selector of composerSelectors) {
    const candidate = [...document.querySelectorAll(selector)].find((el) =>
      visible(el) &&
      (el.isContentEditable ||
        el.tagName.toLowerCase() === 'textarea') &&
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
  const lastUser = users.length ? users[users.length - 1] : null;
  const assistantAfterLastUser = Boolean(
    lastUser &&
    last &&
    typeof lastUser.compareDocumentPosition === 'function' &&
    (lastUser.compareDocumentPosition(last) & 4)
  );
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
    conversation_load_failed: [...document.querySelectorAll('main,[role="main"],body')].some(el =>
      visible(el) && /could not load this chatgpt conversation/i.test(
        (el.innerText || el.textContent || '').trim())),
    conversation_exhausted: [...document.querySelectorAll('[role="alert"]')].some(el =>
      visible(el) && /reached the maximum length for this conversation/i.test(el.innerText || el.textContent || '')),
    message_too_long: [...document.querySelectorAll('[role="alert"]')].some((el) =>
      /message you submitted was too long/i.test(el.innerText || el.textContent || '')),
    usage_limited: [...document.querySelectorAll('[role="alert"]')].some(el =>
      visible(el) && /^(?:you['’]ve hit your limit\. please try again later\.?|you have hit your limit\. please try again later\.?)$/i.test((el.innerText || el.textContent || '').trim())),
    generation_error: [...document.querySelectorAll('[role="alert"]')].some(el =>
      visible(el) && /(?:unknown error|something went wrong\. please try again)/i.test(el.textContent || '')),
    generation_error_kind: [...document.querySelectorAll('[role="alert"]')].some(el =>
      visible(el) && /something went wrong\. please try again/i.test(el.textContent || ''))
      ? 'REQUEST_FAILED' : null,
    login_required: loginRequired,
    composer_selector: composerSelector,
    composer_text: composerText,
    user_count: users.length,
    assistant_count: assistants.length,
    assistant_after_last_user: assistantAfterLastUser,
    last_assistant_text: lastAssistantText,
    last_user_text: users.length ? (users[users.length - 1].innerText || users[users.length - 1].textContent || '').trim() : '',
    last_user_id: lastUser && (
      lastUser.getAttribute('data-message-id') ||
      lastUser.querySelector('[data-chatgpt-selection-message-id]')?.getAttribute('data-chatgpt-selection-message-id') ||
      (lastUser.getAttribute('data-chatgpt-search-message-ids') || '').trim().split(/\s+/).filter(Boolean)[0] ||
      lastUser.getAttribute('data-content-search-unit-key') ||
      lastUser.getAttribute('data-chatgpt-search-unit-key')
    ),
    last_assistant_id: last && (last.getAttribute('data-message-id') ||
      last.querySelector('[data-chatgpt-selection-message-id]')?.getAttribute('data-chatgpt-selection-message-id') ||
      (last.getAttribute('data-chatgpt-search-message-ids') || '').trim().split(/\s+/).filter(Boolean)[0] ||
      last.getAttribute('data-content-search-unit-key') ||
      last.getAttribute('data-chatgpt-search-unit-key')),
    assistant_only_rendering: !users.length && Boolean(last &&
      (last.getAttribute('data-content-search-unit-key') || last.getAttribute('data-chatgpt-search-unit-key') || '').startsWith('fallback-turn-') &&
      last.getAttribute('data-chatgpt-search-message-ids')),
    send_ready: controls.some((el) => visible(el) &&
      el.tagName.toLowerCase() === 'button' && !el.disabled &&
      el.getAttribute('aria-disabled') !== 'true' &&
      (el.getAttribute('aria-label') || el.innerText || el.textContent || '').trim().toLowerCase() === 'send'),
    generating,
  };
})()"""


_RETRY_CONVERSATION_LOAD_EXPRESSION = r"""(() => {
  const visible = (el) => Boolean(
    el && el.getClientRects && el.getClientRects().length
  );
  const surfaces = [...document.querySelectorAll('main,[role="main"]')].filter(
    (el) => visible(el) && /could not load this chatgpt conversation/i.test(
      el.innerText || el.textContent || '')
  );
  if (surfaces.length !== 1) return false;
  const retries = [...surfaces[0].querySelectorAll('button,[role="button"]')].filter(
    (el) => visible(el) && !el.disabled &&
      el.getAttribute('aria-disabled') !== 'true' &&
      (el.innerText || el.textContent || '').trim().toLowerCase() === 'retry'
  );
  if (retries.length !== 1) return false;
  retries[0].click();
  return true;
})()"""


_RETRY_GENERATION_EXPRESSION = r"""((marker, state, expectedURL, baselineID, baselineCount) => {
  if (state.url !== expectedURL || state.generating ||
      !marker || !String(state.last_user_text || '').includes(marker)) return false;
  if (state.last_assistant_id && baselineID
      ? state.last_assistant_id !== baselineID
      : state.assistant_count > baselineCount) return false;
  const visible = el => !!(el && el.getClientRects().length);
  const users = [...document.querySelectorAll(
    '[data-message-author-role="user"],[data-content-search-unit-key$=":user"],[data-chatgpt-search-unit-key$=":user"]'
  )].filter(visible);
  if (!users.length || !(users[users.length-1].textContent || '').includes(marker)) return false;
  const alerts = [...document.querySelectorAll('[role="alert"]')].filter(el =>
    visible(el) && /^(?:unknown error|something went wrong\. please try again)\s+retry$/i.test((el.innerText || el.textContent || '').trim()));
  const targets = alerts.flatMap(alert => [...alert.querySelectorAll('button')].filter(el =>
    visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
    (el.textContent || '').trim().toLowerCase() === 'retry'));
  if (targets.length !== 1) return false;
  targets[0].click();
  return true;
})(REQUEST_MARKER, LIVE_STATE, EXPECTED_URL, BASELINE_ID, BASELINE_COUNT)"""


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
    '[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]',
    'main [contenteditable="true"][role="textbox"]',
    'main [contenteditable="true"]',
  ];
  let el = null;
  for (const selector of selectors) {
    el = [...document.querySelectorAll(selector)].find((candidate) =>
      visible(candidate) &&
      (candidate.isContentEditable ||
        candidate.tagName.toLowerCase() === 'textarea') &&
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
