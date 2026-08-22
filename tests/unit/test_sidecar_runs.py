from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from aegisrun.sidecar.runs import RunRegistry


@pytest.mark.asyncio
async def test_run_registry_keeps_monotonic_events_and_terminal_result() -> None:
    notifications: list[dict[str, Any]] = []

    async def notify(event: dict[str, Any]) -> None:
        notifications.append(event)

    async def execute(report: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        report("agent.progress", {"stage": "plan"})
        await asyncio.sleep(0)
        return {"kind": "agent", "answer": "done"}

    registry = RunRegistry(notify)
    run = registry.start("agent", execute)
    assert run.task is not None
    await run.task

    assert run.status == "succeeded"
    assert run.result == {"kind": "agent", "answer": "done"}
    assert [event["seq"] for event in run.events] == [1, 2, 3]
    assert [event["type"] for event in notifications] == [
        "run.started",
        "agent.progress",
        "run.succeeded",
    ]
    assert registry.events(run.run_id, after_seq=1)[0]["seq"] == 2


@pytest.mark.asyncio
async def test_run_registry_cancel_is_explicit_terminal_state() -> None:
    started = asyncio.Event()

    async def execute(_report: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {"kind": "unreachable"}

    registry = RunRegistry()
    run = registry.start("research", execute)
    await started.wait()
    cancelled = await registry.cancel(run.run_id)

    assert cancelled.status == "cancelled"
    assert cancelled.events[-1]["type"] == "run.cancelled"
    assert cancelled.result is None


@pytest.mark.asyncio
async def test_run_registry_maps_failure_without_traceback() -> None:
    async def execute(_report: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        raise ValueError("invalid symbol")

    registry = RunRegistry()
    run = registry.start("research", execute)
    assert run.task is not None
    await run.task

    assert run.status == "failed"
    assert run.error == {
        "code": "ValueError",
        "message": "invalid symbol",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_run_registry_persists_completed_history_for_replay(tmp_path) -> None:
    history = tmp_path / "run-history.json"

    async def execute(_report: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        return {"kind": "research", "symbol": "600050.SH"}

    registry = RunRegistry(history_path=history)
    run = registry.start("research", execute)
    assert run.task is not None
    await run.task

    restored = RunRegistry(history_path=history)
    assert restored.get(run.run_id).result == {
        "kind": "research",
        "symbol": "600050.SH",
    }
    assert restored.list_recent()[0]["runId"] == run.run_id

    restored.delete(run.run_id)
    assert restored.list_recent() == []
