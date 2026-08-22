from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegisrun.agents import (
    AgentProfileSnapshot,
    AgentSpec,
    ContinuableSubagentManager,
    ContinuableSubagentRequest,
    InProcessContinuableProvider,
    InProcessSubagentProvider,
    SubagentStartRequest,
    SubagentStopReason,
    SubagentWorkResult,
)
from aegisrun.harness import EventSource, WorkspaceEventStore, default_invariants


def profile() -> AgentProfileSnapshot:
    return AgentProfileSnapshot.from_spec(AgentSpec("worker", "bounded", frozenset({"work"})))


@pytest.mark.asyncio
async def test_one_shot_subagent_publishes_paired_lifecycle(tmp_path: Path) -> None:
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )

    async def work() -> SubagentWorkResult:
        return SubagentWorkResult("done", {"value": 1})

    handle = await InProcessSubagentProvider().start(
        SubagentStartRequest("child-1", "work", "run-1", "task-1", 1, 1, profile(), work),
        events,
    )
    result = await handle.result
    await handle.dispose()

    assert result.stop_reason is SubagentStopReason.COMPLETED
    assert result.data == {"value": 1}
    assert [event.event_type for event in await events.load()] == [
        "subagent/started",
        "subagent/ended",
    ]


@pytest.mark.asyncio
async def test_subagent_cancel_settles_and_dispose_is_idempotent(tmp_path: Path) -> None:
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    started = asyncio.Event()

    async def work() -> SubagentWorkResult:
        started.set()
        await asyncio.Event().wait()
        return SubagentWorkResult("never", {})

    handle = await InProcessSubagentProvider().start(
        SubagentStartRequest("child-1", "work", "run-1", "task-1", 1, 1, profile(), work),
        events,
    )
    await started.wait()
    handle.cancel("user cancelled")
    result = await handle.result
    await handle.dispose()
    await handle.dispose()

    assert result.stop_reason is SubagentStopReason.CANCELLED
    assert result.error == "user cancelled"


@pytest.mark.asyncio
async def test_subagent_error_is_structured_and_lifecycle_is_closed(tmp_path: Path) -> None:
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )

    async def work() -> SubagentWorkResult:
        raise RuntimeError("worker defect")

    handle = await InProcessSubagentProvider().start(
        SubagentStartRequest("child-1", "work", "run-1", "task-1", 1, 1, profile(), work),
        events,
    )
    result = await handle.result
    await handle.dispose()

    assert result.stop_reason is SubagentStopReason.ERROR
    assert result.error == "RuntimeError: worker defect"
    recorded = await events.load()
    assert [event.event_type for event in recorded] == [
        "subagent/started",
        "subagent/ended",
    ]
    assert recorded[-1].payload["stop_reason"] == "error"


@pytest.mark.asyncio
async def test_continuable_subagent_serializes_followups_reports_and_settlement(
    tmp_path: Path,
) -> None:
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    turns: list[str] = []
    active = 0
    max_active = 0

    async def work(descriptor, message, report):  # type: ignore[no-untyped-def]
        nonlocal active, max_active
        assert descriptor.profile.generation == profile().generation
        active += 1
        max_active = max(max_active, active)
        turns.append(message)
        await report(f"report:{message}", {"message": message})
        await asyncio.sleep(0)
        active -= 1
        return SubagentWorkResult(f"done:{message}", {"message": message})

    manager = ContinuableSubagentManager(events, (InProcessContinuableProvider(work),))
    await manager.create(
        ContinuableSubagentRequest("child-c1", "research", "run-1", "task-1", 1, 1, profile())
    )
    results = await asyncio.gather(
        manager.followup("child-c1", "first"),
        manager.followup("child-c1", "second"),
    )
    await manager.settle("child-c1")
    await manager.settle("child-c1")

    assert turns == ["first", "second"]
    assert max_active == 1
    assert [result.summary for result in results] == ["done:first", "done:second"]
    recorded = await events.load()
    assert [event.event_type for event in recorded].count("subagent/report") == 2
    assert [event.event_type for event in recorded].count("subagent/settled") == 1
    assert recorded[0].payload["approval_mode"] == "never"
    assert recorded[0].payload["profile_generation"] == profile().generation


@pytest.mark.asyncio
async def test_continuable_subagent_cold_resume_recreates_only_activation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    events = WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants())
    activations: list[str] = []

    async def work(descriptor, message, report):  # type: ignore[no-untyped-def]
        activations.append(descriptor.profile.generation)
        return SubagentWorkResult(message, {})

    provider = InProcessContinuableProvider(work)
    manager = ContinuableSubagentManager(events, (provider,))
    original = await manager.create(
        ContinuableSubagentRequest("child-cold", "research", "run-1", "task-1", 1, 1, profile())
    )
    await manager.followup("child-cold", "before restart")
    await manager.suspend()

    reopened = WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants())
    restored = await ContinuableSubagentManager.restore(reopened, (provider,))
    result = await restored.followup("child-cold", "after restart")

    assert result.summary == "after restart"
    assert restored.descriptors()[0] == original
    assert activations == [original.profile.generation, original.profile.generation]


@pytest.mark.asyncio
async def test_continuable_restore_marks_open_turn_outcome_unknown_without_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    events = WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants())
    request = ContinuableSubagentRequest(
        "child-open", "research", "run-1", "task-1", 1, 1, profile()
    )
    descriptor = await ContinuableSubagentManager(
        events,
        (InProcessContinuableProvider(_unexpected_work),),
    ).create(request)
    await events.append(
        "subagent/followup",
        {"child_id": "child-open", "followup_id": "followup-open", "message": "work"},
        source=EventSource("runtime", actor_id="run-1"),
        parent_session_id="run-1",
        task_id="task-1",
    )
    await events.append(
        "subagent/turn-started",
        {
            "child_id": "child-open",
            "followup_id": "followup-open",
            "turn_id": "turn-open",
            "turn_number": 1,
        },
        source=EventSource("runtime", actor_id="run-1"),
        parent_session_id="run-1",
        turn_id="turn-open",
        task_id="task-1",
    )

    restored = await ContinuableSubagentManager.restore(
        WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants()),
        (InProcessContinuableProvider(_unexpected_work),),
    )

    assert restored.descriptors() == (descriptor,)
    terminal = (
        await WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants()).load()
    )[-1]
    assert terminal.event_type == "subagent/turn-ended"
    assert terminal.payload["recovered"] is True
    assert terminal.payload["error"].startswith("outcome unknown")


async def _unexpected_work(*_args):  # type: ignore[no-untyped-def]
    raise AssertionError("an interrupted turn must not be retried")
