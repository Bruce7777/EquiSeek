from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegisrun.agents import (
    AgentContext,
    AgentOutcome,
    AgentRegistry,
    AgentSpec,
    LocalAgentRuntime,
)
from aegisrun.orchestration import ExecutionPlan, PlanStatus, TaskSpec
from aegisrun.skills import builtin_skill_catalog
from aegisrun.workspace import WorkspaceManager


def registry(handler) -> AgentRegistry:  # type: ignore[no-untyped-def]
    value = AgentRegistry()
    value.register(
        AgentSpec(
            "indicator-agent",
            "bounded indicator worker",
            frozenset({"calculate"}),
            frozenset({"technical-indicators"}),
            capabilities=frozenset({"indicator-engine"}),
            max_concurrency=1,
        ),
        {"calculate": handler},
    )
    return value


def plan(task_count: int = 1) -> ExecutionPlan:
    return ExecutionPlan.create(
        "agent-plan",
        "bounded delegation",
        tuple(
            TaskSpec(
                f"task-{index}",
                f"task {index}",
                "calculate",
                agent="indicator-agent",
                skills=("technical-indicators",),
                required_capabilities=("indicator-engine",),
            )
            for index in range(task_count)
        ),
    )


@pytest.mark.asyncio
async def test_local_agent_runtime_delegates_with_isolated_context_and_ledger(
    tmp_path: Path,
) -> None:
    observed: list[AgentContext] = []

    async def calculate(context: AgentContext) -> AgentOutcome:
        observed.append(context)
        return AgentOutcome("calculated", {"agent": context.spec.name})

    manager = WorkspaceManager(tmp_path)
    runtime = LocalAgentRuntime(manager, registry(calculate), builtin_skill_catalog())
    result = await runtime.execute(plan())

    assert result.status is PlanStatus.SUCCEEDED
    assert observed[0].skills[0].summary.name == "technical-indicators"
    context = json.loads(
        (tmp_path / "agent-plan/tasks/task-0/input/context.json").read_text(encoding="utf-8")
    )
    assert context["agent"] == "indicator-agent"
    ledger = json.loads(
        (tmp_path / "agent-plan/.state/delegations.json").read_text(encoding="utf-8")
    )
    assert ledger["delegations"][0]["status"] == "succeeded"
    assert result.context["agent_runtime"]["mode"] == "local"


@pytest.mark.asyncio
async def test_agent_concurrency_and_global_delegation_budget_are_enforced(
    tmp_path: Path,
) -> None:
    active = 0
    peak = 0

    async def calculate(_: AgentContext) -> AgentOutcome:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return AgentOutcome("done", {})

    runtime = LocalAgentRuntime(
        WorkspaceManager(tmp_path),
        registry(calculate),
        builtin_skill_catalog(),
        max_concurrency=4,
        max_delegations=2,
    )
    with pytest.raises(ValueError, match="budget"):
        await runtime.execute(plan(3))

    result = await runtime.execute(plan(2))
    assert result.status is PlanStatus.SUCCEEDED
    assert peak == 1


def test_agent_runtime_rejects_capability_or_skill_escalation(tmp_path: Path) -> None:
    async def calculate(_: AgentContext) -> AgentOutcome:
        return AgentOutcome("done", {})

    runtime = LocalAgentRuntime(
        WorkspaceManager(tmp_path), registry(calculate), builtin_skill_catalog()
    )
    unauthorized = ExecutionPlan.create(
        "unauthorized",
        "no escalation",
        (
            TaskSpec(
                "task",
                "task",
                "calculate",
                agent="indicator-agent",
                skills=("deepseek-summary",),
                required_capabilities=("model-summary",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="lacks capabilities"):
        runtime.validate_plan(unauthorized)


def test_agent_runtime_enforces_profile_child_and_depth_limits(tmp_path: Path) -> None:
    async def calculate(_: AgentContext) -> AgentOutcome:
        return AgentOutcome("done", {})

    limited = AgentRegistry()
    limited.register(
        AgentSpec(
            "limited-agent",
            "bounded",
            frozenset({"calculate"}),
            capabilities=frozenset({"indicator-engine"}),
            max_depth=1,
            max_children=1,
        ),
        {"calculate": calculate},
    )
    runtime = LocalAgentRuntime(WorkspaceManager(tmp_path), limited, builtin_skill_catalog())
    too_many = ExecutionPlan.create(
        "too-many-children",
        "bounded",
        tuple(
            TaskSpec(
                f"task-{index}",
                "task",
                "calculate",
                agent="limited-agent",
                required_capabilities=("indicator-engine",),
            )
            for index in range(2)
        ),
    )

    with pytest.raises(ValueError, match="child delegation budget"):
        runtime.validate_plan(too_many)

    no_depth = AgentRegistry()
    no_depth.register(
        AgentSpec(
            "lead-only-agent",
            "cannot delegate",
            frozenset({"calculate"}),
            max_depth=0,
        ),
        {"calculate": calculate},
    )
    no_depth_runtime = LocalAgentRuntime(
        WorkspaceManager(tmp_path), no_depth, builtin_skill_catalog()
    )
    delegated = ExecutionPlan.create(
        "no-depth",
        "bounded",
        (TaskSpec("task", "task", "calculate", agent="lead-only-agent"),),
    )
    with pytest.raises(ValueError, match="does not allow"):
        no_depth_runtime.validate_plan(delegated)


def test_delegation_ledger_recovers_interrupted_records(tmp_path: Path) -> None:
    from aegisrun.agents.runtime import DelegationLedger

    manager = WorkspaceManager(tmp_path)
    manager.create_run("resume")
    manager.write_control_json(
        manager.paths("resume").state / "delegations.json",
        {
            "version": 1,
            "plan_id": "resume",
            "delegations": [{"id": "task:1", "status": "running"}],
        },
    )

    ledger = DelegationLedger(manager, "resume")

    assert ledger.records[0]["status"] == "interrupted"
    assert ledger.records[0]["error"] == "local runtime was interrupted"


def test_delegation_ledger_rejects_corrupt_control_state(tmp_path: Path) -> None:
    from aegisrun.agents.runtime import DelegationLedger

    manager = WorkspaceManager(tmp_path)
    manager.create_run("corrupt")
    (manager.paths("corrupt").state / "delegations.json").write_text("[", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable"):
        DelegationLedger(manager, "corrupt")


@pytest.mark.asyncio
async def test_timed_out_agent_closes_its_delegation_record(tmp_path: Path) -> None:
    async def calculate(_: AgentContext) -> AgentOutcome:
        await asyncio.sleep(1)
        return AgentOutcome("late", {})

    manager = WorkspaceManager(tmp_path)
    timed = ExecutionPlan.create(
        "timed-agent",
        "timeout",
        (
            TaskSpec(
                "slow",
                "slow",
                "calculate",
                agent="indicator-agent",
                skills=("technical-indicators",),
                required_capabilities=("indicator-engine",),
                timeout_seconds=1,
            ),
        ),
    )
    runtime = LocalAgentRuntime(manager, registry(calculate), builtin_skill_catalog())

    result = await runtime.execute(timed)

    assert result.status is PlanStatus.FAILED
    ledger = json.loads(
        (manager.paths("timed-agent").state / "delegations.json").read_text(encoding="utf-8")
    )
    assert ledger["delegations"][0]["status"] == "cancelled"
