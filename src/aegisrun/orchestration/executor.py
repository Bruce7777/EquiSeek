from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aegisrun.orchestration.models import ExecutionPlan, PlanStatus, TaskRecord, TaskStatus
from aegisrun.sandbox.base import SandboxPolicy, SandboxProvider, SandboxResult
from aegisrun.workspace.manager import TaskWorkspace, WorkspaceManager


@dataclass(frozen=True, slots=True)
class SubtaskContext:
    plan: ExecutionPlan
    task: TaskRecord
    workspace: TaskWorkspace
    dependency_results: dict[str, dict[str, Any]]
    sandbox: SandboxProvider | None

    async def exec(self, argv: list[str], *, read_only_workspace: bool = False) -> SandboxResult:
        if self.sandbox is None:
            raise RuntimeError("no sandbox provider configured for this executor")
        return await self.sandbox.exec(
            self.workspace.root,
            argv,
            self.task.spec.timeout_seconds,
            SandboxPolicy(
                network_allowed=self.task.spec.network_allowed,
                require_isolation=self.task.spec.require_isolation,
                read_only_workspace=read_only_workspace,
            ),
        )


@dataclass(frozen=True, slots=True)
class SubtaskOutcome:
    summary: str
    data: dict[str, Any]


SubtaskHandler = Callable[[SubtaskContext], Awaitable[SubtaskOutcome]]
EventSink = Callable[[str, dict[str, Any], str | None], Awaitable[Any]]


class SubtaskExecutor:
    def __init__(
        self,
        workspaces: WorkspaceManager,
        *,
        max_concurrency: int = 4,
        sandbox: SandboxProvider | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.workspaces = workspaces
        self.max_concurrency = max_concurrency
        self.sandbox = sandbox
        self.event_sink = event_sink

    async def _emit(
        self, event_type: str, payload: dict[str, Any], task_id: str | None = None
    ) -> None:
        if self.event_sink is not None:
            await self.event_sink(event_type, payload, task_id)

    def approve(self, plan: ExecutionPlan, task_id: str) -> None:
        plan.approve_task(task_id)
        self.workspaces.plan_store(plan.id).save(plan)

    async def execute(
        self,
        plan: ExecutionPlan,
        handlers: dict[str, SubtaskHandler],
        *,
        task_handlers: dict[str, SubtaskHandler] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExecutionPlan:
        store = self.workspaces.plan_store(plan.id)
        plan.recover_interrupted()
        store.save(plan)
        while not plan.status.terminal and plan.status is not PlanStatus.WAITING_APPROVAL:
            ready = plan.ready_task_ids()
            if not ready:
                break
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def run_one(task_id: str, limiter: asyncio.Semaphore = semaphore) -> None:
                async with limiter:
                    await self._execute_one(
                        plan,
                        task_id,
                        handlers,
                        store,
                        task_handlers=task_handlers,
                        on_progress=on_progress,
                    )

            await asyncio.gather(*(run_one(task_id) for task_id in ready))
        store.save(plan)
        self._notify(plan, on_progress)
        return plan

    @staticmethod
    def _notify(
        plan: ExecutionPlan,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(plan.to_dict())
        except Exception:
            # Observability callbacks cannot affect task execution.
            return

    async def _execute_one(
        self,
        plan: ExecutionPlan,
        task_id: str,
        handlers: dict[str, SubtaskHandler],
        store: Any,
        *,
        task_handlers: dict[str, SubtaskHandler] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        record = plan.tasks[task_id]
        if record.spec.approval_required and not record.approved:
            plan.request_approval(task_id)
            store.save(plan)
            await self._emit(
                "task/approval-requested",
                {"task_id": task_id, "status": record.status.value},
                task_id,
            )
            self._notify(plan, on_progress)
            return
        handler = (
            task_handlers.get(task_id) if task_handlers is not None else None
        ) or handlers.get(record.spec.handler)
        if handler is None:
            plan.start_task(task_id)
            plan.fail_task(task_id, f"no handler registered: {record.spec.handler}")
            store.save(plan)
            await self._emit(
                "task/ended",
                {
                    "task_id": task_id,
                    "status": record.status.value,
                    "error": record.error,
                },
                task_id,
            )
            self._notify(plan, on_progress)
            return
        while record.status is TaskStatus.PENDING:
            plan.start_task(task_id)
            store.save(plan)
            await self._emit(
                "task/started",
                {
                    "task_id": task_id,
                    "status": record.status.value,
                    "attempt": record.attempts,
                    "agent": record.spec.agent,
                    "handler": record.spec.handler,
                },
                task_id,
            )
            self._notify(plan, on_progress)
            workspace = self.workspaces.create_task(plan.id, task_id)
            context = SubtaskContext(
                plan=plan,
                task=record,
                workspace=workspace,
                dependency_results={
                    dependency: plan.tasks[dependency].result
                    for dependency in record.spec.depends_on
                },
                sandbox=self.sandbox,
            )
            try:
                outcome = await asyncio.wait_for(
                    handler(context), timeout=record.spec.timeout_seconds
                )
                self.workspaces.ensure_within_quota(plan.id)
                self.workspaces.write_json(
                    workspace.output / "result.json",
                    {"summary": outcome.summary, "data": outcome.data},
                )
                plan.succeed_task(task_id, outcome.data, outcome.summary)
                await self._emit(
                    "task/ended",
                    {
                        "task_id": task_id,
                        "status": record.status.value,
                        "attempt": record.attempts,
                        "summary": outcome.summary,
                    },
                    task_id,
                )
            except asyncio.CancelledError:
                await self._emit(
                    "task/ended",
                    {
                        "task_id": task_id,
                        "status": "cancelled",
                        "attempt": record.attempts,
                        "error": "task execution was cancelled",
                    },
                    task_id,
                )
                raise
            except Exception as error:
                retry = plan.fail_task(task_id, f"{type(error).__name__}: {error}")
                self.workspaces.write_control_json(
                    workspace.logs / f"attempt-{record.attempts}.json",
                    {"error_type": type(error).__name__, "message": str(error)[:2_000]},
                )
                await self._emit(
                    "task/ended",
                    {
                        "task_id": task_id,
                        "status": "retrying" if retry else record.status.value,
                        "attempt": record.attempts,
                        "error": record.error,
                    },
                    task_id,
                )
                if not retry:
                    store.save(plan)
                    self._notify(plan, on_progress)
                    return
            store.save(plan)
            self._notify(plan, on_progress)
