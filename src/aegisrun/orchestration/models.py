from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PlanStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"
    SUPERSEDED = "superseded"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.UNKNOWN_OUTCOME,
            self.SUPERSEDED,
            self.SKIPPED,
            self.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class TaskSpec:
    id: str
    title: str
    handler: str
    depends_on: tuple[str, ...] = ()
    description: str = ""
    agent: str = "local-worker"
    skills: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    max_attempts: int = 1
    timeout_seconds: int = 60
    network_allowed: bool = False
    require_isolation: bool = False
    approval_required: bool = False
    side_effect: bool = False
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not SAFE_ID.fullmatch(self.id) or self.id in {".", ".."}:
            raise ValueError("task id must be a non-empty safe segment")
        if not SAFE_ID.fullmatch(self.agent) or self.agent in {".", ".."}:
            raise ValueError("agent name must be a non-empty safe segment")
        if any(not SAFE_ID.fullmatch(name) or name in {".", ".."} for name in self.skills):
            raise ValueError("skill names must be safe segments")
        if any(
            not SAFE_ID.fullmatch(name) or name in {".", ".."}
            for name in self.required_capabilities
        ):
            raise ValueError("capability names must be safe segments")
        if self.max_attempts < 1 or self.timeout_seconds < 1:
            raise ValueError("task limits must be positive")
        if self.side_effect and not self.idempotency_key:
            raise ValueError("side-effect tasks require an idempotency key")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["depends_on"] = list(self.depends_on)
        value["skills"] = list(self.skills)
        value["required_capabilities"] = list(self.required_capabilities)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskSpec:
        return cls(
            id=str(value["id"]),
            title=str(value["title"]),
            handler=str(value["handler"]),
            depends_on=tuple(str(item) for item in value.get("depends_on", [])),
            description=str(value.get("description", "")),
            agent=str(value.get("agent", "local-worker")),
            skills=tuple(str(item) for item in value.get("skills", [])),
            required_capabilities=tuple(
                str(item) for item in value.get("required_capabilities", [])
            ),
            max_attempts=int(value.get("max_attempts", 1)),
            timeout_seconds=int(value.get("timeout_seconds", 60)),
            network_allowed=bool(value.get("network_allowed", False)),
            require_isolation=bool(value.get("require_isolation", False)),
            approval_required=bool(value.get("approval_required", False)),
            side_effect=bool(value.get("side_effect", False)),
            idempotency_key=value.get("idempotency_key"),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(slots=True)
class TaskRecord:
    spec: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    recoveries: int = 0
    summary: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    approved: bool = False

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def title(self) -> str:
        return self.spec.title

    @property
    def handler(self) -> str:
        return self.spec.handler

    @property
    def depends_on(self) -> tuple[str, ...]:
        return self.spec.depends_on

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.spec.to_dict(),
            "status": self.status.value,
            "attempts": self.attempts,
            "recoveries": self.recoveries,
            "summary": self.summary,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "approved": self.approved,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskRecord:
        return cls(
            spec=TaskSpec.from_dict(value),
            status=TaskStatus(str(value.get("status", "pending"))),
            attempts=int(value.get("attempts", 0)),
            recoveries=int(value.get("recoveries", 0)),
            summary=value.get("summary"),
            result=dict(value.get("result", {})),
            error=value.get("error"),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            approved=bool(value.get("approved", False)),
        )


@dataclass(slots=True)
class ExecutionPlan:
    id: str
    goal: str
    tasks: dict[str, TaskRecord]
    status: PlanStatus = PlanStatus.PENDING
    version: int = 1
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    context: dict[str, Any] = field(default_factory=dict)
    revisions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        plan_id: str,
        goal: str,
        specs: tuple[TaskSpec, ...],
        *,
        context: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        if not specs:
            raise ValueError("execution plan requires at least one task")
        if not SAFE_ID.fullmatch(plan_id) or plan_id in {".", ".."}:
            raise ValueError("plan id must be a non-empty safe segment")
        if len({spec.id for spec in specs}) != len(specs):
            raise ValueError("task ids must be unique")
        plan = cls(
            id=plan_id,
            goal=goal,
            tasks={spec.id: TaskRecord(spec) for spec in specs},
            context=context or {},
        )
        plan._validate_dag()
        return plan

    def _validate_dag(self) -> None:
        task_ids = set(self.tasks)
        for record in self.tasks.values():
            missing = set(record.spec.depends_on) - task_ids
            if missing:
                raise ValueError(
                    f"task {record.spec.id} has missing dependencies: {sorted(missing)}"
                )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("execution plan dependency graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in self.tasks[task_id].spec.depends_on:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in self.tasks:
            visit(task_id)

    def _touch(self) -> None:
        self.version += 1
        self.updated_at = _now()

    def revise(
        self,
        *,
        upsert: tuple[TaskSpec, ...] = (),
        remove: tuple[str, ...] = (),
        reason: str,
        actor: str = "lead-agent",
    ) -> None:
        """Atomically revise pending tasks while preserving completed evidence."""
        if self.status.terminal:
            raise ValueError("terminal execution plans cannot be revised")
        if not reason.strip():
            raise ValueError("plan revision requires a reason")
        if any(
            record.status in {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
            for record in self.tasks.values()
        ):
            raise ValueError("execution plan cannot be revised while tasks are active")
        if len({spec.id for spec in upsert}) != len(upsert):
            raise ValueError("revision task ids must be unique")

        candidate = dict(self.tasks)
        for task_id in remove:
            record = candidate.get(task_id)
            if record is None:
                raise ValueError(f"cannot remove unknown task: {task_id}")
            if record.status is not TaskStatus.PENDING:
                raise ValueError(f"cannot remove non-pending task: {task_id}")
            del candidate[task_id]
        for spec in upsert:
            current = candidate.get(spec.id)
            if current is not None and current.status is not TaskStatus.PENDING:
                raise ValueError(f"cannot replace non-pending task: {spec.id}")
            candidate[spec.id] = TaskRecord(spec)
        if not candidate:
            raise ValueError("execution plan requires at least one task")

        previous = self.tasks
        self.tasks = candidate
        try:
            self._validate_dag()
        except Exception:
            self.tasks = previous
            raise
        self.revisions.append(
            {
                "revision": len(self.revisions) + 1,
                "reason": reason.strip(),
                "actor": actor,
                "created_at": _now(),
                "upserted": [spec.id for spec in upsert],
                "removed": list(remove),
            }
        )
        self.status = PlanStatus.PENDING
        self._touch()

    def recover_from_failure(
        self,
        failed_task_id: str,
        *,
        add: tuple[TaskSpec, ...],
        remove_skipped: tuple[str, ...],
        reason: str,
        actor: str = "lead-agent",
    ) -> None:
        """Create an audited recovery branch without replaying the failed task."""
        if self.status is not PlanStatus.FAILED:
            raise ValueError("failure recovery requires a failed execution plan")
        failed = self.tasks.get(failed_task_id)
        if failed is None or failed.status is not TaskStatus.FAILED:
            raise ValueError("failure recovery requires a failed task")
        if failed.spec.side_effect:
            raise ValueError("side-effect failures require manual outcome resolution")
        if not reason.strip() or not add:
            raise ValueError("failure recovery requires a reason and replacement tasks")
        if len({spec.id for spec in add}) != len(add):
            raise ValueError("recovery task ids must be unique")

        candidate = dict(self.tasks)
        for task_id in remove_skipped:
            record = candidate.get(task_id)
            if record is None or record.status is not TaskStatus.SKIPPED:
                raise ValueError(f"can only remove skipped recovery tasks: {task_id}")
            del candidate[task_id]
        for spec in add:
            if spec.id in candidate:
                raise ValueError(f"recovery task already exists: {spec.id}")
            candidate[spec.id] = TaskRecord(spec)

        previous = self.tasks
        previous_status = failed.status
        previous_summary = failed.summary
        self.tasks = candidate
        failed.status = TaskStatus.SUPERSEDED
        failed.summary = "superseded by an audited recovery branch"
        try:
            self._validate_dag()
        except Exception:
            failed.status = previous_status
            failed.summary = previous_summary
            self.tasks = previous
            raise
        self.revisions.append(
            {
                "revision": len(self.revisions) + 1,
                "kind": "failure_recovery",
                "reason": reason.strip(),
                "actor": actor,
                "created_at": _now(),
                "superseded": failed_task_id,
                "added": [spec.id for spec in add],
                "removed": list(remove_skipped),
            }
        )
        self.status = PlanStatus.PENDING
        self._touch()

    def ready_task_ids(self) -> tuple[str, ...]:
        if self.status.terminal:
            return ()
        ready: list[str] = []
        for task_id, record in self.tasks.items():
            if record.status is not TaskStatus.PENDING:
                continue
            dependencies = [self.tasks[item].status for item in record.spec.depends_on]
            if all(status in {TaskStatus.SUCCEEDED, TaskStatus.SKIPPED} for status in dependencies):
                ready.append(task_id)
        return tuple(ready)

    def start_task(self, task_id: str) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.PENDING:
            raise ValueError(f"task {task_id} is not pending")
        if task_id not in self.ready_task_ids():
            raise ValueError(f"task {task_id} dependencies are not satisfied")
        record.status = TaskStatus.RUNNING
        record.attempts += 1
        record.started_at = _now()
        record.finished_at = None
        record.error = None
        self.status = PlanStatus.RUNNING
        self._touch()

    def succeed_task(
        self, task_id: str, result: dict[str, Any] | None = None, summary: str | None = None
    ) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.RUNNING:
            raise ValueError(f"task {task_id} is not running")
        record.status = TaskStatus.SUCCEEDED
        record.result = result or {}
        record.summary = summary
        record.finished_at = _now()
        self._refresh_status()

    def fail_task(self, task_id: str, error: str) -> bool:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.RUNNING:
            raise ValueError(f"task {task_id} is not running")
        record.error = error
        record.finished_at = _now()
        retry = record.attempts < record.spec.max_attempts
        record.status = TaskStatus.PENDING if retry else TaskStatus.FAILED
        if not retry:
            self._skip_blocked()
        self._refresh_status()
        return retry

    def mark_unknown_outcome(self, task_id: str, error: str) -> None:
        record = self.tasks[task_id]
        if not record.spec.side_effect:
            raise ValueError(f"task {task_id} is not a side effect")
        if record.status.terminal:
            raise ValueError(f"task {task_id} is already terminal")
        record.status = TaskStatus.UNKNOWN_OUTCOME
        record.error = error
        record.finished_at = _now()
        self._skip_blocked()
        self._stop_after_unknown_outcome()
        self._refresh_status()

    def wait_for_approval(self, task_id: str) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.RUNNING:
            raise ValueError(f"task {task_id} is not running")
        record.status = TaskStatus.WAITING_APPROVAL
        self.status = PlanStatus.WAITING_APPROVAL
        self._touch()

    def request_approval(self, task_id: str) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.PENDING:
            raise ValueError(f"task {task_id} is not pending")
        if task_id not in self.ready_task_ids():
            raise ValueError(f"task {task_id} dependencies are not satisfied")
        if not record.spec.approval_required:
            raise ValueError(f"task {task_id} does not require approval")
        record.status = TaskStatus.WAITING_APPROVAL
        self.status = PlanStatus.WAITING_APPROVAL
        self._touch()

    def resume_task(self, task_id: str) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.WAITING_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for approval")
        record.status = TaskStatus.PENDING
        self.status = PlanStatus.PENDING
        self._touch()

    def approve_task(self, task_id: str) -> None:
        record = self.tasks[task_id]
        if not record.spec.approval_required:
            raise ValueError(f"task {task_id} does not require approval")
        if record.status is not TaskStatus.WAITING_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for approval")
        record.approved = True
        self.resume_task(task_id)

    def skip_task(self, task_id: str, reason: str) -> None:
        record = self.tasks[task_id]
        if record.status is not TaskStatus.PENDING:
            raise ValueError(f"task {task_id} is not pending")
        record.status = TaskStatus.SKIPPED
        record.summary = reason
        record.finished_at = _now()
        self._refresh_status()

    def cancel(self) -> None:
        for record in self.tasks.values():
            if not record.status.terminal:
                record.status = TaskStatus.CANCELLED
                record.finished_at = _now()
        self.status = PlanStatus.CANCELLED
        self._touch()

    def abort(self, error: str) -> None:
        for record in self.tasks.values():
            if record.status is TaskStatus.RUNNING:
                record.status = TaskStatus.FAILED
                record.error = error
                record.finished_at = _now()
            elif not record.status.terminal:
                record.status = TaskStatus.SKIPPED
                record.summary = "plan aborted"
                record.finished_at = _now()
        self.status = PlanStatus.FAILED
        self._touch()

    def recover_interrupted(self) -> None:
        changed = False
        for record in self.tasks.values():
            if record.status is TaskStatus.RUNNING:
                record.status = (
                    TaskStatus.UNKNOWN_OUTCOME if record.spec.side_effect else TaskStatus.PENDING
                )
                record.recoveries += 1
                record.error = (
                    "interrupted side effect has unknown outcome"
                    if record.spec.side_effect
                    else "interrupted execution recovered"
                )
                changed = True
        if changed:
            self._skip_blocked()
            self._stop_after_unknown_outcome()
            self.status = (
                PlanStatus.FAILED
                if any(
                    record.status is TaskStatus.UNKNOWN_OUTCOME for record in self.tasks.values()
                )
                else PlanStatus.PENDING
            )
            self._touch()

    def _stop_after_unknown_outcome(self) -> None:
        if not any(record.status is TaskStatus.UNKNOWN_OUTCOME for record in self.tasks.values()):
            return
        for record in self.tasks.values():
            if not record.status.terminal:
                record.status = TaskStatus.SKIPPED
                record.summary = "plan stopped after unknown side-effect outcome"
                record.finished_at = _now()

    def _skip_blocked(self) -> None:
        failed = {
            task_id
            for task_id, task in self.tasks.items()
            if task.status in {TaskStatus.FAILED, TaskStatus.UNKNOWN_OUTCOME}
        }
        changed = True
        while changed:
            changed = False
            for task_id, record in self.tasks.items():
                if record.status is not TaskStatus.PENDING:
                    continue
                if set(record.spec.depends_on) & failed:
                    record.status = TaskStatus.SKIPPED
                    record.summary = "dependency failed"
                    record.finished_at = _now()
                    failed.add(task_id)
                    changed = True

    def _refresh_status(self) -> None:
        statuses = {record.status for record in self.tasks.values()}
        if statuses & {TaskStatus.FAILED, TaskStatus.UNKNOWN_OUTCOME}:
            self.status = PlanStatus.FAILED
        elif TaskStatus.WAITING_APPROVAL in statuses:
            self.status = PlanStatus.WAITING_APPROVAL
        elif all(status.terminal for status in statuses):
            self.status = PlanStatus.SUCCEEDED
        elif TaskStatus.RUNNING in statuses:
            self.status = PlanStatus.RUNNING
        else:
            self.status = PlanStatus.PENDING
        self._touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "context": self.context,
            "revisions": self.revisions,
            "tasks": [record.to_dict() for record in self.tasks.values()],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionPlan:
        records = [TaskRecord.from_dict(item) for item in value["tasks"]]
        plan = cls(
            id=str(value["id"]),
            goal=str(value["goal"]),
            tasks={record.spec.id: record for record in records},
            status=PlanStatus(str(value.get("status", "pending"))),
            version=int(value.get("version", 1)),
            created_at=str(value.get("created_at", _now())),
            updated_at=str(value.get("updated_at", _now())),
            context=dict(value.get("context", {})),
            revisions=[dict(item) for item in value.get("revisions", [])],
        )
        plan._validate_dag()
        return plan


def issue_triage_plan(run_id: str) -> ExecutionPlan:
    return ExecutionPlan.create(
        run_id,
        "复现问题、最小修复并验证结果",
        (
            TaskSpec("skill", "加载适用技能", "load_skill"),
            TaskSpec("baseline", "运行失败基线测试", "run_tests", depends_on=("skill",)),
            TaskSpec("inspect", "读取与定位源码", "read_source", depends_on=("baseline",)),
            TaskSpec("patch", "生成最小补丁", "create_patch", depends_on=("inspect",)),
            TaskSpec(
                "approval",
                "审批高风险写操作",
                "apply_patch",
                depends_on=("patch",),
                approval_required=True,
                side_effect=True,
                idempotency_key=f"{run_id}:apply_patch",
            ),
            TaskSpec("verify", "执行回归测试", "verify", depends_on=("approval",)),
            TaskSpec("report", "生成可校验报告", "finish", depends_on=("verify",)),
        ),
    )
