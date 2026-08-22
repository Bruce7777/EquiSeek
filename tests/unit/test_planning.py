from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegisrun.orchestration.executor import SubtaskContext, SubtaskExecutor, SubtaskOutcome
from aegisrun.orchestration.models import ExecutionPlan, PlanStatus, TaskSpec, TaskStatus
from aegisrun.workspace.manager import WorkspaceManager


def sample_plan() -> ExecutionPlan:
    return ExecutionPlan.create(
        "plan-1",
        "形成可审计研究报告",
        (
            TaskSpec("fetch", "获取行情", "fetch", network_allowed=True),
            TaskSpec("indicators", "计算指标", "calculate", depends_on=("fetch",)),
            TaskSpec("facts", "生成事实摘要", "summarize", depends_on=("indicators",)),
        ),
    )


def test_plan_validates_dag_and_transitions() -> None:
    plan = sample_plan()

    assert plan.ready_task_ids() == ("fetch",)
    plan.start_task("fetch")
    plan.succeed_task("fetch", {"bars": 120})
    assert plan.ready_task_ids() == ("indicators",)
    plan.start_task("indicators")
    plan.wait_for_approval("indicators")
    plan.resume_task("indicators")
    plan.start_task("indicators")
    plan.succeed_task("indicators")
    plan.skip_task("facts", "model disabled")

    assert plan.status is PlanStatus.SUCCEEDED
    assert plan.tasks["facts"].status is TaskStatus.SKIPPED
    assert ExecutionPlan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()


@pytest.mark.parametrize(
    "tasks",
    [
        (TaskSpec("a", "A", "x", depends_on=("missing",)),),
        (
            TaskSpec("a", "A", "x", depends_on=("b",)),
            TaskSpec("b", "B", "x", depends_on=("a",)),
        ),
    ],
)
def test_plan_rejects_missing_dependencies_and_cycles(tasks: tuple[TaskSpec, ...]) -> None:
    with pytest.raises(ValueError):
        ExecutionPlan.create("invalid", "invalid", tasks)


@pytest.mark.parametrize("unsafe_id", ("../bad", "bad/path", r"bad\path", "空格"))
def test_plan_rejects_unsafe_cross_platform_ids(unsafe_id: str) -> None:
    with pytest.raises(ValueError):
        ExecutionPlan.create(unsafe_id, "invalid", (TaskSpec("safe", "safe", "safe"),))
    with pytest.raises(ValueError):
        TaskSpec(unsafe_id, "invalid", "invalid")


def test_interrupted_plan_recovers_running_tasks() -> None:
    plan = sample_plan()
    plan.start_task("fetch")

    recovered = ExecutionPlan.from_dict(plan.to_dict())
    recovered.recover_interrupted()

    assert recovered.tasks["fetch"].status is TaskStatus.PENDING
    assert recovered.tasks["fetch"].recoveries == 1
    assert recovered.ready_task_ids() == ("fetch",)


def test_interrupted_side_effect_has_unknown_outcome() -> None:
    plan = ExecutionPlan.create(
        "side-effect",
        "safe write",
        (
            TaskSpec(
                "write",
                "write",
                "write",
                side_effect=True,
                idempotency_key="side-effect:write",
            ),
        ),
    )
    plan.start_task("write")

    plan.recover_interrupted()

    assert plan.status is PlanStatus.FAILED
    assert plan.tasks["write"].status is TaskStatus.UNKNOWN_OUTCOME

    second = ExecutionPlan.create(
        "reported-side-effect",
        "safe write",
        (
            TaskSpec(
                "write",
                "write",
                "write",
                side_effect=True,
                idempotency_key="reported-side-effect:write",
            ),
        ),
    )
    second.mark_unknown_outcome("write", "worker disappeared")
    assert second.status is PlanStatus.FAILED


def test_abort_marks_running_failed_and_remaining_tasks_skipped() -> None:
    plan = sample_plan()
    plan.start_task("fetch")

    plan.abort("budget exhausted")

    assert plan.status is PlanStatus.FAILED
    assert plan.tasks["fetch"].status is TaskStatus.FAILED
    assert plan.tasks["indicators"].status is TaskStatus.SKIPPED
    assert plan.tasks["facts"].status is TaskStatus.SKIPPED


def test_plan_revision_is_atomic_audited_and_preserves_completed_tasks() -> None:
    plan = sample_plan()
    plan.start_task("fetch")
    plan.succeed_task("fetch", {"bars": 120})
    before_version = plan.version

    plan.revise(
        upsert=(
            TaskSpec(
                "indicators",
                "重新计算指标",
                "calculate",
                depends_on=("fetch",),
            ),
            TaskSpec("audit", "审计结果", "audit", depends_on=("indicators",)),
        ),
        remove=("facts",),
        reason="数据源触发公式版本复核",
    )

    assert plan.tasks["fetch"].status is TaskStatus.SUCCEEDED
    assert plan.tasks["indicators"].title == "重新计算指标"
    assert plan.ready_task_ids() == ("indicators",)
    assert plan.version > before_version
    assert plan.revisions[0]["actor"] == "lead-agent"
    assert ExecutionPlan.from_dict(plan.to_dict()).revisions == plan.revisions

    snapshot = plan.to_dict()
    with pytest.raises(ValueError, match="missing dependencies"):
        plan.revise(
            upsert=(TaskSpec("broken", "broken", "x", depends_on=("missing",)),),
            reason="invalid revision",
        )
    assert plan.to_dict() == snapshot


def test_plan_revision_rejects_active_or_completed_task_mutation() -> None:
    plan = sample_plan()
    plan.start_task("fetch")
    with pytest.raises(ValueError, match="active"):
        plan.revise(remove=("indicators",), reason="too early")
    plan.succeed_task("fetch")
    with pytest.raises(ValueError, match="non-pending"):
        plan.revise(remove=("fetch",), reason="preserve evidence")


def test_failed_read_task_can_be_superseded_by_audited_recovery_branch() -> None:
    plan = sample_plan()
    plan.start_task("fetch")
    plan.fail_task("fetch", "primary source unavailable")

    plan.recover_from_failure(
        "fetch",
        add=(
            TaskSpec("fallback", "使用备用数据", "fallback"),
            TaskSpec("recalculate", "重新计算", "calculate", depends_on=("fallback",)),
        ),
        remove_skipped=("indicators", "facts"),
        reason="切换到用户确认的公开历史数据",
    )

    assert plan.status is PlanStatus.PENDING
    assert plan.tasks["fetch"].status is TaskStatus.SUPERSEDED
    assert plan.ready_task_ids() == ("fallback",)
    plan.start_task("fallback")
    plan.succeed_task("fallback")
    plan.start_task("recalculate")
    plan.succeed_task("recalculate")
    assert plan.status is PlanStatus.SUCCEEDED
    assert plan.revisions[-1]["kind"] == "failure_recovery"


@pytest.mark.asyncio
async def test_approval_task_waits_until_explicitly_approved(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path)
    plan = ExecutionPlan.create(
        "approval-plan",
        "approval",
        (TaskSpec("write", "write", "write", approval_required=True),),
    )
    called = False

    async def handler(_: SubtaskContext) -> SubtaskOutcome:
        nonlocal called
        called = True
        return SubtaskOutcome("done", {})

    executor = SubtaskExecutor(manager)
    await executor.execute(plan, {"write": handler})
    assert plan.status is PlanStatus.WAITING_APPROVAL
    assert plan.tasks["write"].attempts == 0
    assert called is False

    executor.approve(plan, "write")
    await executor.execute(plan, {"write": handler})
    assert plan.status is PlanStatus.SUCCEEDED
    assert called is True


@pytest.mark.asyncio
async def test_subtask_executor_uses_isolated_workspaces_and_persists_plan(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path)
    plan = sample_plan()
    observed: dict[str, Path] = {}

    async def handler(context: SubtaskContext) -> SubtaskOutcome:
        observed[context.task.id] = context.workspace.root
        marker = context.workspace.output / f"{context.task.id}.txt"
        marker.write_text(context.task.title, encoding="utf-8")
        await asyncio.sleep(0)
        return SubtaskOutcome(summary=context.task.title, data={"task": context.task.id})

    executor = SubtaskExecutor(manager, max_concurrency=2)
    result = await executor.execute(
        plan,
        {"fetch": handler, "calculate": handler, "summarize": handler},
    )

    assert result.status is PlanStatus.SUCCEEDED
    assert len(set(observed.values())) == 3
    assert all(path.is_relative_to(tmp_path / "plan-1" / "tasks") for path in observed.values())
    stored = manager.plan_store("plan-1").load()
    assert stored.status is PlanStatus.SUCCEEDED
    assert (tmp_path / "plan-1" / "tasks" / "fetch" / "output" / "result.json").exists()


@pytest.mark.asyncio
async def test_subtask_failure_retries_then_skips_dependents(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path)
    plan = ExecutionPlan.create(
        "retry-plan",
        "retry",
        (
            TaskSpec("unstable", "unstable", "fail", max_attempts=2),
            TaskSpec("downstream", "downstream", "ok", depends_on=("unstable",)),
        ),
    )
    attempts = 0

    async def fail(_: SubtaskContext) -> SubtaskOutcome:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    result = await SubtaskExecutor(manager).execute(plan, {"fail": fail})

    assert attempts == 2
    assert result.status is PlanStatus.FAILED
    assert result.tasks["unstable"].status is TaskStatus.FAILED
    assert result.tasks["downstream"].status is TaskStatus.SKIPPED


@pytest.mark.asyncio
async def test_subtask_executor_runs_independent_ready_tasks_concurrently(
    tmp_path: Path,
) -> None:
    plan = ExecutionPlan.create(
        "parallel-plan",
        "parallel",
        (
            TaskSpec("left", "left", "work"),
            TaskSpec("right", "right", "work"),
            TaskSpec("join", "join", "work", depends_on=("left", "right")),
        ),
    )
    active = 0
    peak = 0

    async def work(context: SubtaskContext) -> SubtaskOutcome:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02 if context.task.id != "join" else 0)
        active -= 1
        return SubtaskOutcome("done", {})

    result = await SubtaskExecutor(WorkspaceManager(tmp_path), max_concurrency=2).execute(
        plan, {"work": work}
    )

    assert result.status is PlanStatus.SUCCEEDED
    assert peak == 2
