from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from aegisrun.core.domain import PolicySnapshot
from aegisrun.core.errors import ApprovalRequiredError, PolicyDeniedError
from aegisrun.harness.events import EventSource, EventStore
from aegisrun.tools.registry import RegisteredTool, ToolRegistry
from aegisrun.tools.spec import ToolResult


class ToolPolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    action: ToolPolicyAction
    reason: str


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    call_id: str
    agent_id: str
    task_id: str | None
    tool: RegisteredTool
    arguments: dict[str, Any]
    policy: PolicySnapshot


ToolPolicyHook = Callable[[ToolExecutionContext], Awaitable[ToolPolicyDecision]]
ApprovalHook = Callable[[ToolExecutionContext, str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True, slots=True)
class ToolInvocationOutcome:
    call_id: str
    result: ToolResult | None = None
    error: str | None = None


class ToolPipeline:
    """Guarded, event-paired execution for model-visible tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        events: EventStore | None = None,
        policy_hooks: tuple[ToolPolicyHook, ...] = (),
        approval_hook: ApprovalHook | None = None,
    ) -> None:
        self.registry = registry
        self.events = events
        self.policy_hooks = policy_hooks
        self.approval_hook = approval_hook

    async def _record_policy(
        self,
        context: ToolExecutionContext,
        action: ToolPolicyAction,
        reason: str,
    ) -> None:
        if self.events is None:
            return
        await self.events.append(
            "policy/decision",
            {
                "surface": "tool",
                "call_id": context.call_id,
                "tool": context.tool.spec.name,
                "decision": action.value,
                "reason": reason,
            },
            source=EventSource("runtime", actor_id=context.agent_id),
            task_id=context.task_id,
        )

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        policy: PolicySnapshot,
        *,
        agent_id: str,
        task_id: str | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        context = await self._prepare(
            name,
            arguments,
            policy,
            agent_id=agent_id,
            task_id=task_id,
            call_id=call_id,
        )
        try:
            result = await self._invoke(context)
        except BaseException as error:
            await self._record_result(context, error=error)
            raise
        await self._record_result(context, result=result)
        return result

    async def _prepare(
        self,
        name: str,
        arguments: dict[str, Any],
        policy: PolicySnapshot,
        *,
        agent_id: str,
        task_id: str | None,
        call_id: str | None,
    ) -> ToolExecutionContext:
        registered = self.registry.validate(name, arguments, policy)
        context = ToolExecutionContext(
            call_id=call_id or str(uuid4()),
            agent_id=agent_id,
            task_id=task_id,
            tool=registered,
            arguments=arguments,
            policy=policy,
        )
        ask_reason: str | None = None
        for hook in self.policy_hooks:
            decision = await hook(context)
            if decision.action is ToolPolicyAction.DENY:
                await self._record_policy(context, decision.action, decision.reason)
                raise PolicyDeniedError(decision.reason)
            if decision.action is ToolPolicyAction.ASK:
                ask_reason = decision.reason
        if name in policy.approval_required and ask_reason is None:
            ask_reason = "tool requires explicit approval"
        if ask_reason is not None:
            if self.approval_hook is None:
                await self._record_policy(context, ToolPolicyAction.ASK, ask_reason)
                raise ApprovalRequiredError(ask_reason)
            approved = await self.approval_hook(context, ask_reason)
            if not approved:
                await self._record_policy(
                    context, ToolPolicyAction.DENY, "tool approval was rejected"
                )
                raise PolicyDeniedError("tool approval was rejected")
            await self._record_policy(context, ToolPolicyAction.ALLOW, f"approved: {ask_reason}")
        else:
            await self._record_policy(
                context, ToolPolicyAction.ALLOW, "all tool policy checks passed"
            )
        if self.events is not None:
            await self.events.append(
                "tool/call",
                {
                    "call_id": context.call_id,
                    "tool": name,
                    "version": registered.spec.version,
                    "arguments": arguments,
                    "side_effect": registered.spec.side_effect,
                    "risk": registered.spec.risk.value,
                },
                source=EventSource("agent", actor_id=agent_id),
                task_id=task_id,
            )
        return context

    async def _invoke(self, context: ToolExecutionContext) -> ToolResult:
        async with asyncio.timeout(context.tool.spec.timeout_seconds):
            return await context.tool.handler(context.arguments)

    @staticmethod
    def _error_text(context: ToolExecutionContext, error: BaseException) -> str:
        if isinstance(error, TimeoutError):
            return f"tool timed out after {context.tool.spec.timeout_seconds}s"
        return f"{type(error).__name__}: {error}"[:2_000]

    async def _record_result(
        self,
        context: ToolExecutionContext,
        *,
        result: ToolResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self.events is None:
            return
        payload: dict[str, Any] = {
            "call_id": context.call_id,
            "tool": context.tool.spec.name,
            "is_error": error is not None,
        }
        if error is not None:
            payload["error"] = self._error_text(context, error)
        elif result is not None:
            payload.update(
                {
                    "summary": result.summary,
                    "data": result.data,
                    "artifact_id": result.artifact_id,
                    "external_reference": result.external_reference,
                }
            )
        await self.events.append(
            "tool/result",
            payload,
            source=EventSource("runtime", actor_id=context.agent_id),
            task_id=context.task_id,
        )

    async def execute_batch(
        self,
        calls: Sequence[ToolInvocation],
        policy: PolicySnapshot,
        *,
        agent_id: str,
        task_id: str | None = None,
    ) -> tuple[ToolInvocationOutcome, ...]:
        """Run only consecutive safe calls concurrently, preserving returned order."""
        outcomes: list[ToolInvocationOutcome] = []
        safe_group: list[ToolInvocation] = []

        async def flush_safe_group() -> None:
            if not safe_group:
                return
            outcomes.extend(await self._execute_safe_group(safe_group, policy, agent_id, task_id))
            safe_group.clear()

        for call in calls:
            try:
                spec = self.registry.get(call.name).spec
                concurrency_safe = (
                    spec.concurrency_safe
                    and not spec.side_effect
                    and call.name not in policy.approval_required
                )
            except Exception:
                concurrency_safe = False
            if concurrency_safe:
                safe_group.append(call)
                continue
            await flush_safe_group()
            outcomes.append(await self._execute_outcome(call, policy, agent_id, task_id))
        await flush_safe_group()
        return tuple(outcomes)

    async def _execute_safe_group(
        self,
        calls: Sequence[ToolInvocation],
        policy: PolicySnapshot,
        agent_id: str,
        task_id: str | None,
    ) -> tuple[ToolInvocationOutcome, ...]:
        prepared: list[tuple[ToolInvocation, ToolExecutionContext | None, Exception | None]] = []
        for call in calls:
            try:
                context = await self._prepare(
                    call.name,
                    call.arguments,
                    policy,
                    agent_id=agent_id,
                    task_id=task_id,
                    call_id=call.call_id,
                )
            except Exception as error:
                prepared.append((call, None, error))
            else:
                prepared.append((call, context, None))

        async def capture(context: ToolExecutionContext) -> ToolResult | BaseException:
            try:
                return await self._invoke(context)
            except BaseException as error:
                return error

        contexts = [context for _, context, _ in prepared if context is not None]
        results = iter(await asyncio.gather(*(capture(context) for context in contexts)))
        outcomes: list[ToolInvocationOutcome] = []
        for call, prepared_context, preparation_error in prepared:
            if prepared_context is None:
                assert preparation_error is not None
                outcomes.append(
                    ToolInvocationOutcome(
                        call.call_id,
                        error=f"{type(preparation_error).__name__}: {preparation_error}"[:2_000],
                    )
                )
                continue
            captured = next(results)
            if isinstance(captured, BaseException):
                await self._record_result(prepared_context, error=captured)
                outcomes.append(
                    ToolInvocationOutcome(
                        call.call_id, error=self._error_text(prepared_context, captured)
                    )
                )
                continue
            await self._record_result(prepared_context, result=captured)
            outcomes.append(ToolInvocationOutcome(call.call_id, result=captured))
        return tuple(outcomes)

    async def _execute_outcome(
        self,
        call: ToolInvocation,
        policy: PolicySnapshot,
        agent_id: str,
        task_id: str | None,
    ) -> ToolInvocationOutcome:
        try:
            result = await self.execute(
                call.name,
                call.arguments,
                policy,
                agent_id=agent_id,
                task_id=task_id,
                call_id=call.call_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return ToolInvocationOutcome(
                call.call_id, error=f"{type(error).__name__}: {error}"[:2_000]
            )
        return ToolInvocationOutcome(call.call_id, result=result)
