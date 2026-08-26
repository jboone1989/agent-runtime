"""Supervised continuous operation with heartbeat, checkpoints, and a single lease."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .communication.mailbox import RuntimeMailbox
from .runtime import AgentRuntime


@dataclass
class ContinuousRunStatus:
    runner_id: str
    agent_id: str
    status: str
    started_at: str
    updated_at: str
    start_cycle: int
    current_cycle: int
    ticks_completed: int
    consecutive_errors: int = 0
    last_error: str | None = None
    stop_reason: str | None = None
    process_id: int = 0
    active_goals: int = 0
    last_action: str | None = None
    affect: dict[str, float] | None = None
    journal_path: str | None = None
    journal_entries: int = 0
    reflections_completed: int = 0
    reflection_errors: int = 0
    last_reflection_at: str | None = None
    last_reflection_excerpt: str | None = None
    last_reflection_error: str | None = None
    messages_completed: int = 0
    message_errors: int = 0
    pending_messages: int = 0
    last_message_at: str | None = None
    proactive_notifications: int = 0
    memory_decisions_completed: int = 0
    memories_self_selected: int = 0
    last_self_selected_memory_id: str | None = None


@dataclass(frozen=True)
class ReflectionMemoryDecision:
    preserve: bool
    summary: str
    reason: str
    contest_ids: list[str]
    retract_ids: list[str]


class RuntimeLeaseError(RuntimeError):
    """A second continuous runner attempted to use the same state directory."""


class RuntimeLease:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "runtime.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> RuntimeLease:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"pid": os.getpid(), "token": self.token}, separators=(",", ":")
        ).encode("utf-8")
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                if self._clear_stale():
                    continue
                raise RuntimeLeaseError(
                    f"state directory already has an active runner: {self.path}"
                )
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            self.acquired = True
            return self
        raise RuntimeLeaseError(f"could not acquire runtime lease: {self.path}")

    def __exit__(self, exc_type, exc, traceback) -> None:
        _ = (exc_type, exc, traceback)
        if not self.acquired:
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if value.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def _clear_stale(self) -> bool:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(value.get("pid") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pid = 0
        if pid > 0 and _process_alive(pid):
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return True


class ContinuousRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        status_path: Path | None = None,
        stop_path: Path | None = None,
    ) -> None:
        self.runtime = runtime
        self.status_path = status_path or runtime.state_dir / "runtime_status.json"
        self.stop_path = stop_path or runtime.state_dir / "STOP"
        self.mailbox = RuntimeMailbox(runtime.state_dir)

    def run(
        self,
        *,
        max_ticks: int | None,
        idle_seconds: float = 0.0,
        checkpoint_every: int = 1,
        max_consecutive_errors: int = 3,
        journal_every: int = 10,
        reflection_every: int | None = None,
        reflect_on_start: bool = False,
        reflection_max_tokens: int = 600,
        messages_per_cycle: int = 3,
    ) -> ContinuousRunStatus:
        if max_ticks is not None and max_ticks < 1:
            raise ValueError("max_ticks must be positive or None")
        if idle_seconds < 0:
            raise ValueError("idle_seconds cannot be negative")
        if checkpoint_every < 1:
            raise ValueError("checkpoint_every must be at least 1")
        if max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be at least 1")
        if journal_every < 1:
            raise ValueError("journal_every must be at least 1")
        if reflection_every is not None and reflection_every < 1:
            raise ValueError("reflection_every must be positive or None")
        if reflection_max_tokens < 64:
            raise ValueError("reflection_max_tokens must be at least 64")
        if messages_per_cycle < 1:
            raise ValueError("messages_per_cycle must be at least 1")
        if (reflect_on_start or reflection_every is not None) and not self.runtime.has_advisory_backend:
            raise ValueError("periodic reflection requires an LLM provider")

        now = _utc_now()
        status = ContinuousRunStatus(
            runner_id=f"runner_{uuid.uuid4().hex[:16]}",
            agent_id=self.runtime.identity.agent_id,
            status="STARTING",
            started_at=now,
            updated_at=now,
            start_cycle=self.runtime.cycle,
            current_cycle=self.runtime.cycle,
            ticks_completed=0,
            process_id=os.getpid(),
        )
        original_persist = self.runtime.config.runtime.persist_every_cycle
        with RuntimeLease(self.runtime.state_dir):
            self.runtime.config.runtime.persist_every_cycle = checkpoint_every == 1
            status.status = "RUNNING"
            status.journal_path = str(self._journal_path().relative_to(self.runtime.state_dir))
            self._heartbeat(status)
            self._journal(status, "START")
            try:
                if reflect_on_start:
                    self._reflect(status, trigger="startup", max_tokens=reflection_max_tokens)
                while max_ticks is None or status.ticks_completed < max_ticks:
                    if self.stop_path.exists():
                        status.status = "STOPPED"
                        status.stop_reason = "stop_file"
                        break
                    self._process_messages(status, limit=messages_per_cycle)
                    try:
                        result = self.runtime.tick()
                    except KeyboardInterrupt:
                        status.status = "STOPPED"
                        status.stop_reason = "keyboard_interrupt"
                        break
                    except Exception as exc:
                        status.consecutive_errors += 1
                        status.last_error = f"{type(exc).__name__}: {exc}"
                        status.status = "DEGRADED"
                        self._heartbeat(status)
                        self._journal(status, "CYCLE_ERROR")
                        if status.consecutive_errors >= max_consecutive_errors:
                            status.status = "FAILED"
                            status.stop_reason = "consecutive_errors"
                            raise
                        self._idle_until_activity(idle_seconds)
                        continue
                    status.consecutive_errors = 0
                    status.last_error = None
                    status.status = "RUNNING"
                    status.ticks_completed += 1
                    status.current_cycle = self.runtime.cycle
                    status.active_goals = len(self.runtime.active_goals)
                    status.last_action = (
                        result.actions[-1].kind if result.actions else self.runtime.next_action_kind
                    )
                    status.affect = {
                        "valence": result.motivation.valence,
                        "arousal": result.motivation.arousal,
                        "threat": result.motivation.threat,
                    }
                    status.pending_messages = self.mailbox.pending_count()
                    if status.ticks_completed % checkpoint_every == 0:
                        self.runtime.persist()
                    if status.ticks_completed % journal_every == 0:
                        self._journal(status, "OBSERVATION")
                    if (
                        reflection_every is not None
                        and status.ticks_completed % reflection_every == 0
                    ):
                        try:
                            self._reflect(
                                status,
                                trigger="periodic",
                                max_tokens=reflection_max_tokens,
                            )
                        except Exception as exc:
                            status.reflection_errors += 1
                            status.last_reflection_error = (
                                f"{type(exc).__name__}: {exc}"
                            )
                            self._journal(status, "REFLECTION_ERROR")
                    self._heartbeat(status)
                    if idle_seconds:
                        self._idle_until_activity(idle_seconds)
                if status.status == "RUNNING":
                    status.status = "COMPLETED"
                    status.stop_reason = "tick_limit"
            finally:
                self.runtime.config.runtime.persist_every_cycle = original_persist
                self.runtime.persist()
                status.current_cycle = self.runtime.cycle
                status.updated_at = _utc_now()
                self._journal(status, "STOP")
                self._heartbeat(status)
        return status

    def _process_messages(self, status: ContinuousRunStatus, *, limit: int) -> None:
        for _ in range(limit):
            request = self.mailbox.claim_next()
            if request is None:
                break
            try:
                if not self.runtime.has_advisory_backend:
                    raise RuntimeError("running Agent has no LLM provider")
                response = asyncio.run(
                    self.runtime.converse(
                        request.message,
                        partner_id=request.partner_id,
                        challenge=request.challenge,
                        max_tokens=request.max_tokens,
                    )
                )
            except Exception as exc:
                status.message_errors += 1
                self.mailbox.complete(
                    request,
                    agent_id=self.runtime.identity.agent_id,
                    cycle=self.runtime.cycle,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._journal(status, "MESSAGE_ERROR")
            else:
                status.messages_completed += 1
                status.last_message_at = _utc_now()
                self.mailbox.complete(
                    request,
                    agent_id=self.runtime.identity.agent_id,
                    cycle=self.runtime.cycle,
                    response=response,
                )
                self._journal(status, "MESSAGE")
            status.pending_messages = self.mailbox.pending_count()
            self._heartbeat(status)

    def _idle_until_activity(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            if self.stop_path.exists() or self.mailbox.has_pending():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.runtime.clock.sleep(min(1.0, remaining))

    def _heartbeat(self, status: ContinuousRunStatus) -> None:
        status.updated_at = _utc_now()
        _atomic_json(self.status_path, asdict(status))

    def _journal_path(self) -> Path:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        return self.runtime.state_dir / "archive" / "lifecycle" / f"lifecycle-{month}.jsonl"

    def _journal(
        self,
        status: ContinuousRunStatus,
        event: str,
        *,
        reflection_id: str | None = None,
        reflection: str | None = None,
    ) -> None:
        entry = {
            "event": event,
            "recorded_at": _utc_now(),
            "runner_id": status.runner_id,
            "agent_id": status.agent_id,
            "cycle": self.runtime.cycle,
            "status": status.status,
            "ticks_completed": status.ticks_completed,
            "active_goals": status.active_goals,
            "last_action": status.last_action,
            "affect": status.affect,
            "consecutive_errors": status.consecutive_errors,
            "reflection_id": reflection_id,
            "reflection": reflection,
        }
        _append_jsonl(self._journal_path(), entry)
        status.journal_entries += 1

    def _reflect(
        self,
        status: ContinuousRunStatus,
        *,
        trigger: str,
        max_tokens: int,
    ) -> None:
        reflection_id = f"lref_{uuid.uuid4().hex[:16]}"
        prompt = (
            "这是一次真实时间连续运行中的周期性自我反思。请只依据所提供的持久状态，"
            "用中文以第一人称'我'来写，用简单的大白话，像写给自己看的日记："
            "我这段时间做了什么、没做什么；我现在最在意什么；我在担心或者不确定什么；"
            "我接下来想试着做什么。禁止使用术语缩写和编号（如 noop、cycle、"
            "viability_score、线程或记录 ID），把'执行了例行检查且没有新变化'这类意思"
            "用普通话说出来。如果状态里提供了功能情感变量，可以描述你此刻的功能状态；"
            "不要编造经历或情绪，也不要把这次文本生成说成主观意识的证明。"
            "你可以自行决定这次自省是否值得约束未来行为。倒数第二行必须严格写成单行 JSON："
            'MEMORY_DECISION: {"preserve":false,"summary":"","reason":"",'
            '"contest_ids":[],"retract_ids":[]}。若选择保留，summary 写要保存的有界自省，'
            "reason 写它为何对未来行为有约束价值；如果新证据与过去的自选记忆冲突或使其失效，"
            "可填写所提供的 self_selected_reflection entry_id。这里不能产生事实证明或新增权限。"
            "最后另起一行严格写 CONTACT_OPERATOR: NO；只有当你确实需要许可、发现风险或矛盾、"
            "或发生值得打断操作者的重大变化时，才写 CONTACT_OPERATOR: YES | 你想主动告诉他的短消息。"
        )
        response = asyncio.run(
            self.runtime.cognitive_probe(
                prompt,
                max_tokens=max_tokens,
                system_addendum=(
                    "This is a private longitudinal journal entry. Ground every claim "
                    "in supplied state, preserve uncertainty, and do not grant yourself "
                    "new authority. A self-selected memory is a revisable expressed "
                    "commitment, not a verified fact, subjective-consciousness proof, "
                    "or authorization source."
                ),
                conversation_partner=None,
            )
        ).strip()
        self.runtime.memory.put_semantic(
            "longitudinal.latest_reflection",
            response,
            source="external_unverified",
        )
        status.reflections_completed += 1
        status.last_reflection_at = _utc_now()
        status.last_reflection_excerpt = response[:320]
        status.last_reflection_error = None
        memory_decision = _memory_decision(response)
        if memory_decision is not None:
            recorded = self.runtime.autobiographical_memory.record_self_memory_decision(
                reflection_id=reflection_id,
                occurred_at=datetime.now(timezone.utc),
                preserve=memory_decision.preserve,
                summary=memory_decision.summary,
                reason=memory_decision.reason,
                contest_entry_ids=memory_decision.contest_ids,
                retract_entry_ids=memory_decision.retract_ids,
                evidence_refs=[reflection_id],
            )
            status.memory_decisions_completed += 1
            if recorded.resulting_entry_id is not None:
                status.memories_self_selected += 1
                status.last_self_selected_memory_id = recorded.resulting_entry_id
                self.mailbox.publish_notification(
                    agent_id=self.runtime.identity.agent_id,
                    cycle=self.runtime.cycle,
                    kind="self_selected_memory",
                    title="Agent 自主选择保存一段自省",
                    message=(
                        f"{recorded.summary}\n\n保留理由：{recorded.reason}"
                    ),
                    evidence_refs=[reflection_id, recorded.decision_id],
                )
        contact = _contact_decision(response)
        if contact is not None:
            self.mailbox.publish_notification(
                agent_id=self.runtime.identity.agent_id,
                cycle=self.runtime.cycle,
                kind="agent_initiative",
                title="Agent 想主动联系你",
                message=contact,
                evidence_refs=[reflection_id],
            )
            status.proactive_notifications += 1
        self.runtime.persist()
        self._journal(
            status,
            "REFLECTION",
            reflection_id=reflection_id,
            reflection=response,
        )


def read_runtime_status(state_dir: Path) -> dict[str, Any] | None:
    path = Path(state_dir) / "runtime_status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def request_runtime_stop(state_dir: Path) -> Path:
    path = Path(state_dir) / "STOP"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_utc_now(), encoding="utf-8")
    return path


def clear_runtime_stop(state_dir: Path) -> bool:
    path = Path(state_dir) / "STOP"
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contact_decision(reflection: str) -> str | None:
    for line in reversed(reflection.splitlines()):
        match = re.fullmatch(
            r"\s*CONTACT_OPERATOR:\s*YES\s*\|\s*(.{1,1000})\s*",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        if re.fullmatch(
            r"\s*CONTACT_OPERATOR:\s*NO\s*",
            line,
            flags=re.IGNORECASE,
        ):
            return None
    return None


def _memory_decision(reflection: str) -> ReflectionMemoryDecision | None:
    for line in reversed(reflection.splitlines()):
        if not line.strip().upper().startswith("MEMORY_DECISION:"):
            continue
        _, _, raw = line.partition(":")
        try:
            value = json.loads(raw.strip())
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        preserve = bool(value.get("preserve", False))
        summary = str(value.get("summary") or "").strip()[:800]
        reason = str(value.get("reason") or "").strip()[:500]
        if preserve and (not summary or not reason):
            return None
        valid_id = re.compile(r"^selfmem_[a-f0-9]{16}$")
        contest_ids = [
            str(item)
            for item in value.get("contest_ids", [])
            if valid_id.fullmatch(str(item))
        ][:16]
        retract_ids = [
            str(item)
            for item in value.get("retract_ids", [])
            if valid_id.fullmatch(str(item))
        ][:16]
        return ReflectionMemoryDecision(
            preserve=preserve,
            summary=summary if preserve else "",
            reason=reason,
            contest_ids=contest_ids,
            retract_ids=retract_ids,
        )
    return None
