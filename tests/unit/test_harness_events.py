from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import aegisrun.harness.events as events_module
from aegisrun.harness import (
    EventCorruptionError,
    EventSource,
    InvariantError,
    WorkspaceEventStore,
    default_invariants,
    project_events,
)


@pytest.mark.asyncio
async def test_workspace_events_are_immutable_replayable_and_projected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants())
    header = await store.append(
        "session/header",
        {"goal": "audit", "nested": {"value": 1}},
        source=EventSource("runtime", actor_id="lead-agent"),
    )
    await store.append(
        "subagent/started",
        {"child_id": "child-1", "provider": "in-process"},
        source=EventSource("runtime", actor_id="lead-agent"),
        task_id="task-1",
    )
    await store.append(
        "subagent/ended",
        {"child_id": "child-1", "stop_reason": "completed"},
        source=EventSource("runtime", actor_id="lead-agent"),
        task_id="task-1",
    )

    with pytest.raises(TypeError):
        header.payload["goal"] = "mutated"
    replay = await WorkspaceEventStore(path, run_id="run-1", invariants=default_invariants()).load()
    assert [event.seq for event in replay] == [1, 2, 3]
    projection = project_events(replay)
    assert projection.subagents["child-1"]["status"] == "completed"
    assert projection.last_seq == 3


@pytest.mark.asyncio
async def test_candidate_invariant_failure_does_not_append(tmp_path: Path) -> None:
    store = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    with pytest.raises(InvariantError, match="orphan"):
        await store.append(
            "tool/result",
            {"call_id": "missing", "is_error": False},
            source=EventSource("runtime", actor_id="lead-agent"),
        )
    assert await store.load() == ()


@pytest.mark.asyncio
async def test_turn_and_step_lifecycle_must_be_paired_before_session_ends(
    tmp_path: Path,
) -> None:
    store = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    source = EventSource("runtime", actor_id="lead-agent")
    await store.append(
        "turn/started",
        {"turn_id": "turn-1"},
        source=source,
        turn_id="turn-1",
    )
    await store.append(
        "step/started",
        {"step_id": "step-1"},
        source=source,
        turn_id="turn-1",
        step_id="step-1",
    )
    with pytest.raises(InvariantError, match="open step"):
        await store.append(
            "turn/ended",
            {"turn_id": "turn-1"},
            source=source,
            turn_id="turn-1",
        )
    await store.append(
        "step/ended",
        {"step_id": "step-1", "status": "succeeded"},
        source=source,
        turn_id="turn-1",
        step_id="step-1",
    )
    await store.append(
        "turn/ended",
        {"turn_id": "turn-1", "status": "succeeded"},
        source=source,
        turn_id="turn-1",
    )
    await store.append("session/ended", {"status": "succeeded"}, source=source)


@pytest.mark.asyncio
async def test_incomplete_tail_is_reported_as_corruption(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"seq": 1}), encoding="utf-8")
    store = WorkspaceEventStore(path, run_id="run-1")
    with pytest.raises(EventCorruptionError, match="incomplete"):
        await store.load()


def test_event_flush_uses_a_windows_compatible_writable_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    flags: list[int] = []
    real_open = os.open

    def tracked_open(value: str | bytes | os.PathLike[str], open_flags: int) -> int:
        flags.append(open_flags)
        return real_open(value, open_flags)

    monkeypatch.setattr(events_module.os, "open", tracked_open)
    WorkspaceEventStore(path, run_id="run-1")._flush_sync()

    assert flags == [os.O_RDWR]


@pytest.mark.asyncio
async def test_model_request_must_reference_its_exact_header(tmp_path: Path) -> None:
    store = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    header = await store.append(
        "request/header",
        {"request_id": "request-1"},
        source=EventSource("runtime", actor_id="agent-1"),
    )
    with pytest.raises(InvariantError, match="reference"):
        await store.append(
            "model/request",
            {"request_id": "request-1", "header_seq": header.seq + 1},
            source=EventSource("agent", actor_id="agent-1"),
        )


@pytest.mark.asyncio
async def test_surface_replacement_hides_old_messages_without_deleting_raw_events(
    tmp_path: Path,
) -> None:
    store = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    for index in range(2):
        await store.append(
            "user/message",
            {
                "role": "user",
                "content": [{"type": "text", "text": f"old-{index}"}],
            },
            source=EventSource("user"),
        )
    await store.append(
        "surface/replace",
        {
            "start_seq": 1,
            "end_seq": 2,
            "source_event_seqs": [1, 2],
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "compacted"}],
            },
        },
        source=EventSource("runtime", actor_id="lead-agent"),
    )

    raw = await store.load()
    projection = project_events(raw)
    assert len(raw) == 3
    assert len(projection.surface) == 1
    assert projection.surface[0].content[0]["text"] == "compacted"
    assert projection.surface[0].source_event_seqs == (1, 2)
