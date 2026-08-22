from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from aegisrun.agents.profiles import AgentProfileSnapshot
from aegisrun.harness.events import EventSource, EventStore


class SubagentStopReason(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    BUDGET = "budget"


@dataclass(frozen=True, slots=True)
class SubagentCapabilities:
    depth_limit: bool = True
    profile: bool = True
    structured_output: bool = True


@dataclass(frozen=True, slots=True)
class SubagentWorkResult:
    summary: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SubagentResult:
    stop_reason: SubagentStopReason
    summary: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


SubagentWork = Callable[[], Awaitable[SubagentWorkResult]]


@dataclass(frozen=True, slots=True)
class SubagentStartRequest:
    child_id: str
    label: str
    parent_session_id: str
    task_id: str
    depth: int
    max_depth: int
    profile: AgentProfileSnapshot
    work: SubagentWork


class SubagentRunHandle(Protocol):
    id: str
    result: asyncio.Future[SubagentResult]

    def cancel(self, reason: str = "cancelled") -> None: ...

    async def dispose(self) -> None: ...


class SubagentProvider(Protocol):
    name: str
    capabilities: SubagentCapabilities

    async def start(
        self, request: SubagentStartRequest, events: EventStore
    ) -> SubagentRunHandle: ...


class _InProcessRun:
    def __init__(
        self,
        run_id: str,
        task: asyncio.Task[SubagentWorkResult],
        result: asyncio.Future[SubagentResult],
        monitor: asyncio.Task[None],
        *,
        shutdown_timeout: float,
    ) -> None:
        self.id = run_id
        self._task = task
        self.result = result
        self._monitor = monitor
        self._shutdown_timeout = shutdown_timeout
        self._cancel_reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        self._cancel_reason = reason.strip() or "cancelled"
        if not self._task.done():
            self._task.cancel(self._cancel_reason)

    async def dispose(self) -> None:
        if not self._monitor.done():
            self.cancel("disposed")
        try:
            await asyncio.wait_for(asyncio.shield(self._monitor), self._shutdown_timeout)
        except TimeoutError as error:
            raise RuntimeError("in-process subagent did not reach quiescence") from error


class InProcessSubagentProvider:
    name = "in-process"
    capabilities = SubagentCapabilities()

    def __init__(self, *, shutdown_timeout: float = 5.0) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("subagent shutdown timeout must be positive")
        self.shutdown_timeout = shutdown_timeout

    async def start(self, request: SubagentStartRequest, events: EventStore) -> SubagentRunHandle:
        if request.depth < 1 or request.max_depth < 0 or request.depth > request.max_depth:
            raise ValueError("subagent depth exceeds the requested absolute limit")
        gate = asyncio.Event()

        async def gated_work() -> SubagentWorkResult:
            await gate.wait()
            return await request.work()

        task = asyncio.create_task(gated_work(), name=f"subagent:{request.child_id}")
        try:
            await events.append(
                "subagent/started",
                {
                    "child_id": request.child_id,
                    "label": request.label,
                    "provider": self.name,
                    "depth": request.depth,
                    "max_depth": request.max_depth,
                    "profile_id": request.profile.id,
                    "profile_generation": request.profile.generation,
                    "profile_digest": request.profile.digest,
                },
                source=EventSource("runtime", actor_id=request.parent_session_id),
                parent_session_id=request.parent_session_id,
                task_id=request.task_id,
            )
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        gate.set()
        loop = asyncio.get_running_loop()
        result: asyncio.Future[SubagentResult] = loop.create_future()

        async def settle() -> None:
            try:
                work_result = await task
                outcome = SubagentResult(
                    SubagentStopReason.COMPLETED,
                    summary=work_result.summary,
                    data=work_result.data,
                )
            except asyncio.CancelledError as error:
                message = str(error) or "cancelled"
                outcome = SubagentResult(SubagentStopReason.CANCELLED, error=message)
            except Exception as error:
                outcome = SubagentResult(
                    SubagentStopReason.ERROR,
                    error=f"{type(error).__name__}: {error}"[:2_000],
                )
            try:
                await events.append(
                    "subagent/ended",
                    {
                        "child_id": request.child_id,
                        "provider": self.name,
                        "stop_reason": outcome.stop_reason.value,
                        "summary": outcome.summary,
                        "error": outcome.error,
                    },
                    source=EventSource("runtime", actor_id=request.parent_session_id),
                    parent_session_id=request.parent_session_id,
                    task_id=request.task_id,
                )
            except Exception as error:
                outcome = SubagentResult(
                    SubagentStopReason.ERROR,
                    error=f"event settlement failed: {type(error).__name__}: {error}"[:2_000],
                )
            if not result.done():
                result.set_result(outcome)

        monitor = asyncio.create_task(settle(), name=f"subagent-monitor:{request.child_id}")
        return _InProcessRun(
            request.child_id,
            task,
            result,
            monitor,
            shutdown_timeout=self.shutdown_timeout,
        )
