from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aegisrun.core.domain import PolicySnapshot
from aegisrun.core.errors import PolicyDeniedError
from aegisrun.harness import WorkspaceEventStore, default_invariants
from aegisrun.tools import (
    RiskLevel,
    ToolInvocation,
    ToolPipeline,
    ToolPolicyAction,
    ToolPolicyDecision,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


def tool_spec() -> ToolSpec:
    return ToolSpec(
        "echo",
        "1",
        "echo",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        RiskLevel.LOW,
        False,
    )


@pytest.mark.asyncio
async def test_pipeline_pairs_model_visible_call_and_result_events(tmp_path: Path) -> None:
    registry = ToolRegistry()

    async def echo(arguments: dict[str, Any]) -> ToolResult:
        return ToolResult("ok", arguments)

    registry.register(tool_spec(), echo)
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    result = await ToolPipeline(registry, events=events).execute(
        "echo",
        {"text": "hello"},
        PolicySnapshot(allowed_tools=("echo",)),
        agent_id="agent-1",
    )

    assert result.data == {"text": "hello"}
    assert [event.event_type for event in await events.load()] == [
        "policy/decision",
        "tool/call",
        "tool/result",
    ]


@pytest.mark.asyncio
async def test_pipeline_denies_before_handler_and_before_call_event(tmp_path: Path) -> None:
    called = False
    registry = ToolRegistry()

    async def echo(_: dict[str, Any]) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult("unexpected", {})

    async def deny(_: object) -> ToolPolicyDecision:
        return ToolPolicyDecision(ToolPolicyAction.DENY, "blocked")

    registry.register(tool_spec(), echo)
    events = WorkspaceEventStore(tmp_path / "events.jsonl", run_id="run-1")
    with pytest.raises(PolicyDeniedError, match="blocked"):
        await ToolPipeline(registry, events=events, policy_hooks=(deny,)).execute(  # type: ignore[arg-type]
            "echo",
            {"text": "hello"},
            PolicySnapshot(allowed_tools=("echo",)),
            agent_id="agent-1",
        )
    assert called is False
    recorded = await events.load()
    assert [event.event_type for event in recorded] == ["policy/decision"]
    assert recorded[0].payload["decision"] == "deny"


@pytest.mark.asyncio
async def test_pipeline_records_error_result_before_propagating(tmp_path: Path) -> None:
    registry = ToolRegistry()

    async def broken(_: dict[str, Any]) -> ToolResult:
        raise RuntimeError("tool defect")

    registry.register(tool_spec(), broken)
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )

    with pytest.raises(RuntimeError, match="tool defect"):
        await ToolPipeline(registry, events=events).execute(
            "echo",
            {"text": "hello"},
            PolicySnapshot(allowed_tools=("echo",)),
            agent_id="agent-1",
        )

    recorded = await events.load()
    assert [event.event_type for event in recorded] == [
        "policy/decision",
        "tool/call",
        "tool/result",
    ]
    assert recorded[-1].payload["is_error"] is True


@pytest.mark.asyncio
async def test_batch_runs_only_consecutive_safe_calls_in_parallel_and_keeps_order(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    active = 0
    max_active = 0
    timeline: list[str] = []

    def spec(name: str, *, safe: bool) -> ToolSpec:
        return ToolSpec(
            name,
            "1",
            name,
            {"type": "object", "additionalProperties": False},
            RiskLevel.LOW,
            False,
            concurrency_safe=safe,
        )

    def handler(name: str, *, exclusive: bool = False):  # type: ignore[no-untyped-def]
        async def run(_: dict[str, Any]) -> ToolResult:
            nonlocal active, max_active
            if exclusive:
                assert active == 0
            active += 1
            max_active = max(max_active, active)
            timeline.append(f"start:{name}")
            await asyncio.sleep(0.02 if name == "safe-a" else 0.005)
            timeline.append(f"end:{name}")
            active -= 1
            return ToolResult(name, {"name": name})

        return run

    registry.register(spec("safe-a", safe=True), handler("safe-a"))
    registry.register(spec("safe-b", safe=True), handler("safe-b"))
    registry.register(spec("exclusive", safe=False), handler("exclusive", exclusive=True))
    registry.register(spec("safe-c", safe=True), handler("safe-c"))
    policy = PolicySnapshot(allowed_tools=("safe-a", "safe-b", "exclusive", "safe-c"))

    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    outcomes = await ToolPipeline(registry, events=events).execute_batch(
        (
            ToolInvocation("safe-a", {}, "call-a"),
            ToolInvocation("safe-b", {}, "call-b"),
            ToolInvocation("exclusive", {}, "call-x"),
            ToolInvocation("safe-c", {}, "call-c"),
        ),
        policy,
        agent_id="agent-1",
    )

    assert max_active == 2
    assert [outcome.call_id for outcome in outcomes] == [
        "call-a",
        "call-b",
        "call-x",
        "call-c",
    ]
    assert timeline.index("start:exclusive") > timeline.index("end:safe-a")
    assert timeline.index("start:exclusive") > timeline.index("end:safe-b")
    assert timeline.index("start:safe-c") > timeline.index("end:exclusive")
    recorded = await events.load()
    assert [
        event.payload["call_id"] for event in recorded if event.event_type == "tool/result"
    ] == ["call-a", "call-b", "call-x", "call-c"]


@pytest.mark.asyncio
async def test_pipeline_timeout_is_paired_and_side_effect_cannot_be_concurrency_safe(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="side-effecting"):
        ToolSpec(
            "invalid",
            "1",
            "invalid",
            {"type": "object"},
            RiskLevel.HIGH,
            True,
            concurrency_safe=True,
        )
    registry = ToolRegistry()

    async def slow(_: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(2)
        return ToolResult("late", {})

    spec = tool_spec()
    registry.register(
        ToolSpec(
            spec.name,
            spec.version,
            spec.description,
            spec.input_schema,
            spec.risk,
            spec.side_effect,
            timeout_seconds=1,
        ),
        slow,
    )
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )

    with pytest.raises(TimeoutError):
        await ToolPipeline(registry, events=events).execute(
            "echo",
            {"text": "hello"},
            PolicySnapshot(allowed_tools=("echo",)),
            agent_id="agent-1",
        )

    recorded = await events.load()
    assert [event.event_type for event in recorded][-2:] == ["tool/call", "tool/result"]
    assert recorded[-1].payload["is_error"] is True
    assert recorded[-1].payload["error"] == "tool timed out after 1s"
