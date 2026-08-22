from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from aegisrun.agents.profiles import AgentProfileSnapshot
from aegisrun.agents.subagents import (
    SubagentResult,
    SubagentStopReason,
    SubagentWorkResult,
)
from aegisrun.core.security import canonical_hash
from aegisrun.harness.events import SAFE_ID, AgentEvent, EventSource, EventStore

ContinuableReport = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ContinuableSubagentRequest:
    child_id: str
    label: str
    parent_session_id: str
    task_id: str
    depth: int
    max_depth: int
    profile: AgentProfileSnapshot
    tool_filter: frozenset[str] = frozenset()
    sandbox_override: str | None = None
    approval_mode: str = "never"

    def __post_init__(self) -> None:
        for value, label in (
            (self.child_id, "child id"),
            (self.parent_session_id, "parent session id"),
            (self.task_id, "task id"),
        ):
            if not SAFE_ID.fullmatch(value):
                raise ValueError(f"continuable {label} is invalid")
        if not self.label.strip():
            raise ValueError("continuable label must be non-empty")
        if self.depth < 1 or self.depth > self.max_depth:
            raise ValueError("continuable subagent depth exceeds the absolute limit")
        if not self.tool_filter <= self.profile.allowed_tools:
            raise ValueError("continuable tool filter cannot expand profile tools")
        if self.approval_mode != "never":
            raise ValueError("continuable subagents require frozen approval mode 'never'")


@dataclass(frozen=True, slots=True)
class ContinuableSubagentDescriptor:
    child_id: str
    label: str
    parent_session_id: str
    task_id: str
    depth: int
    max_depth: int
    profile: AgentProfileSnapshot
    tool_filter: frozenset[str]
    sandbox_override: str | None
    approval_mode: str
    provider: str

    def __post_init__(self) -> None:
        ContinuableSubagentRequest(
            child_id=self.child_id,
            label=self.label,
            parent_session_id=self.parent_session_id,
            task_id=self.task_id,
            depth=self.depth,
            max_depth=self.max_depth,
            profile=self.profile,
            tool_filter=self.tool_filter,
            sandbox_override=self.sandbox_override,
            approval_mode=self.approval_mode,
        )
        if not SAFE_ID.fullmatch(self.provider):
            raise ValueError("continuable provider is invalid")

    @classmethod
    def from_request(
        cls, request: ContinuableSubagentRequest, provider: str
    ) -> ContinuableSubagentDescriptor:
        return cls(
            child_id=request.child_id,
            label=request.label,
            parent_session_id=request.parent_session_id,
            task_id=request.task_id,
            depth=request.depth,
            max_depth=request.max_depth,
            profile=request.profile,
            tool_filter=request.tool_filter,
            sandbox_override=request.sandbox_override,
            approval_mode=request.approval_mode,
            provider=provider,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ContinuableSubagentDescriptor:
        profile = payload.get("profile")
        if not isinstance(profile, Mapping):
            raise ValueError("continuable descriptor is missing its profile")
        restored_profile = AgentProfileSnapshot.from_dict(dict(profile))
        generation = payload.get("profile_generation")
        if generation is not None and generation != restored_profile.generation:
            raise ValueError("continuable descriptor profile generation is inconsistent")
        return cls(
            child_id=str(payload["child_id"]),
            label=str(payload["label"]),
            parent_session_id=str(payload["parent_session_id"]),
            task_id=str(payload["task_id"]),
            depth=int(payload["depth"]),
            max_depth=int(payload["max_depth"]),
            profile=restored_profile,
            tool_filter=frozenset(str(item) for item in payload.get("tool_filter", ())),
            sandbox_override=(
                str(payload["sandbox_override"])
                if payload.get("sandbox_override") is not None
                else None
            ),
            approval_mode=str(payload["approval_mode"]),
            provider=str(payload["provider"]),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "label": self.label,
            "parent_session_id": self.parent_session_id,
            "task_id": self.task_id,
            "depth": self.depth,
            "max_depth": self.max_depth,
            "profile": self.profile.to_dict(),
            "profile_generation": self.profile.generation,
            "tool_filter": sorted(self.tool_filter),
            "sandbox_override": self.sandbox_override,
            "approval_mode": self.approval_mode,
            "provider": self.provider,
        }


class ContinuableSubagentWorker(Protocol):
    async def run_turn(self, message: str, report: ContinuableReport) -> SubagentWorkResult: ...

    async def close(self) -> None: ...


class ContinuableSubagentProvider(Protocol):
    name: str

    async def prepare(
        self, descriptor: ContinuableSubagentDescriptor
    ) -> ContinuableSubagentWorker: ...


ContinuableTurnWork = Callable[
    [ContinuableSubagentDescriptor, str, ContinuableReport], Awaitable[SubagentWorkResult]
]


class _CallableWorker:
    def __init__(
        self, descriptor: ContinuableSubagentDescriptor, work: ContinuableTurnWork
    ) -> None:
        self.descriptor = descriptor
        self.work = work
        self.closed = False

    async def run_turn(self, message: str, report: ContinuableReport) -> SubagentWorkResult:
        if self.closed:
            raise RuntimeError("continuable activation is closed")
        return await self.work(self.descriptor, message, report)

    async def close(self) -> None:
        self.closed = True


class InProcessContinuableProvider:
    name = "in-process-continuable"

    def __init__(self, work: ContinuableTurnWork) -> None:
        self.work = work

    async def prepare(self, descriptor: ContinuableSubagentDescriptor) -> ContinuableSubagentWorker:
        return _CallableWorker(descriptor, self.work)


@dataclass(slots=True)
class _ContinuableState:
    descriptor: ContinuableSubagentDescriptor
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    activation: ContinuableSubagentWorker | None = None
    settled: bool = False
    followup_count: int = 0
    turn_count: int = 0
    report_count: int = 0
    last_summary: str | None = None


class ContinuableSubagentManager:
    """Owns durable child identity while providers own replaceable activations."""

    def __init__(
        self,
        events: EventStore,
        providers: tuple[ContinuableSubagentProvider, ...],
    ) -> None:
        self.events = events
        self._providers = {provider.name: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("duplicate continuable provider")
        self._children: dict[str, _ContinuableState] = {}

    async def create(
        self,
        request: ContinuableSubagentRequest,
        *,
        provider: str = "in-process-continuable",
    ) -> ContinuableSubagentDescriptor:
        if request.child_id in self._children:
            raise ValueError(f"duplicate continuable child: {request.child_id}")
        if provider not in self._providers:
            raise ValueError(f"unknown continuable provider: {provider}")
        descriptor = ContinuableSubagentDescriptor.from_request(request, provider)
        await self.events.append(
            "subagent/created",
            descriptor.to_payload(),
            source=EventSource("runtime", actor_id=request.parent_session_id),
            parent_session_id=request.parent_session_id,
            task_id=request.task_id,
        )
        self._children[request.child_id] = _ContinuableState(descriptor)
        return descriptor

    async def followup(self, child_id: str, message: str) -> SubagentResult:
        if not message.strip():
            raise ValueError("continuable followup must be non-empty")
        state = self._state(child_id)
        async with state.lock:
            if state.settled:
                raise RuntimeError("continuable child is already settled")
            if state.activation is None:
                provider = self._providers.get(state.descriptor.provider)
                if provider is None:
                    raise RuntimeError(
                        f"continuable provider unavailable: {state.descriptor.provider}"
                    )
                state.activation = await provider.prepare(state.descriptor)
            state.followup_count += 1
            followup_id = (
                "followup-"
                + canonical_hash({"child_id": child_id, "number": state.followup_count})[:20]
            )
            await self._append(
                state,
                "subagent/followup",
                {"child_id": child_id, "followup_id": followup_id, "message": message},
            )
            state.turn_count += 1
            turn_id = (
                "turn-"
                + canonical_hash(
                    {"child_id": child_id, "turn": state.turn_count, "followup": followup_id}
                )[:20]
            )
            await self._append(
                state,
                "subagent/turn-started",
                {
                    "child_id": child_id,
                    "followup_id": followup_id,
                    "turn_id": turn_id,
                    "turn_number": state.turn_count,
                },
                turn_id=turn_id,
            )

            async def report(summary: str, data: dict[str, Any]) -> None:
                if not summary.strip():
                    raise ValueError("continuable report summary must be non-empty")
                state.report_count += 1
                await self._append(
                    state,
                    "subagent/report",
                    {
                        "child_id": child_id,
                        "turn_id": turn_id,
                        "report_id": f"report-{state.report_count}",
                        "summary": summary,
                        "data": data,
                    },
                    turn_id=turn_id,
                )

            try:
                work_result = await state.activation.run_turn(message, report)
            except asyncio.CancelledError as error:
                outcome = SubagentResult(
                    SubagentStopReason.CANCELLED, error=str(error) or "turn cancelled"
                )
                await self._end_turn(state, turn_id, followup_id, outcome)
                raise
            except Exception as error:
                outcome = SubagentResult(
                    SubagentStopReason.ERROR,
                    error=f"{type(error).__name__}: {error}"[:2_000],
                )
            else:
                outcome = SubagentResult(
                    SubagentStopReason.COMPLETED,
                    summary=work_result.summary,
                    data=work_result.data,
                )
                state.last_summary = work_result.summary
            await self._end_turn(state, turn_id, followup_id, outcome)
            return outcome

    async def settle(
        self,
        child_id: str,
        *,
        reason: SubagentStopReason = SubagentStopReason.COMPLETED,
        error: str | None = None,
    ) -> None:
        state = self._state(child_id)
        async with state.lock:
            if state.settled:
                return
            if state.activation is not None:
                await state.activation.close()
                state.activation = None
            await self._append(
                state,
                "subagent/settled",
                {
                    "child_id": child_id,
                    "stop_reason": reason.value,
                    "summary": state.last_summary,
                    "error": error,
                },
            )
            state.settled = True

    async def suspend(self) -> None:
        """Drop activations without settling durable children, enabling cold resume."""
        for child_id in sorted(self._children):
            state = self._children[child_id]
            async with state.lock:
                if state.activation is not None:
                    await state.activation.close()
                    state.activation = None

    @classmethod
    async def restore(
        cls,
        events: EventStore,
        providers: tuple[ContinuableSubagentProvider, ...],
    ) -> ContinuableSubagentManager:
        manager = cls(events, providers)
        open_turns: dict[str, tuple[str, str]] = {}
        for event in await events.load():
            payload = event.payload
            if event.event_type == "subagent/created":
                descriptor = ContinuableSubagentDescriptor.from_payload(payload)
                manager._children[descriptor.child_id] = _ContinuableState(descriptor)
            elif event.event_type.startswith("subagent/"):
                child_id = payload.get("child_id")
                if not isinstance(child_id, str) or child_id not in manager._children:
                    continue
                state = manager._children[child_id]
                if event.event_type == "subagent/followup":
                    state.followup_count += 1
                elif event.event_type == "subagent/turn-started":
                    state.turn_count += 1
                    open_turns[child_id] = (str(payload["turn_id"]), str(payload["followup_id"]))
                elif event.event_type == "subagent/report":
                    state.report_count += 1
                elif event.event_type == "subagent/turn-ended":
                    open_turns.pop(child_id, None)
                    summary = payload.get("summary")
                    if isinstance(summary, str):
                        state.last_summary = summary
                elif event.event_type == "subagent/settled":
                    state.settled = True
        for child_id in sorted(open_turns):
            turn_id, followup_id = open_turns[child_id]
            state = manager._children[child_id]
            await manager._end_turn(
                state,
                turn_id,
                followup_id,
                SubagentResult(
                    SubagentStopReason.ERROR,
                    error="outcome unknown: process ended before turn settlement",
                ),
                recovered=True,
            )
        return manager

    def descriptors(self) -> tuple[ContinuableSubagentDescriptor, ...]:
        return tuple(self._children[key].descriptor for key in sorted(self._children))

    def _state(self, child_id: str) -> _ContinuableState:
        try:
            return self._children[child_id]
        except KeyError as error:
            raise ValueError(f"unknown continuable child: {child_id}") from error

    async def _end_turn(
        self,
        state: _ContinuableState,
        turn_id: str,
        followup_id: str,
        outcome: SubagentResult,
        *,
        recovered: bool = False,
    ) -> AgentEvent:
        return await self._append(
            state,
            "subagent/turn-ended",
            {
                "child_id": state.descriptor.child_id,
                "followup_id": followup_id,
                "turn_id": turn_id,
                "stop_reason": outcome.stop_reason.value,
                "summary": outcome.summary,
                "error": outcome.error,
                "recovered": recovered,
            },
            turn_id=turn_id,
        )

    async def _append(
        self,
        state: _ContinuableState,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> AgentEvent:
        descriptor = state.descriptor
        return await self.events.append(
            event_type,
            payload,
            source=EventSource("runtime", actor_id=descriptor.parent_session_id),
            parent_session_id=descriptor.parent_session_id,
            turn_id=turn_id,
            task_id=descriptor.task_id,
        )
