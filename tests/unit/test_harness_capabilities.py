from __future__ import annotations

import asyncio

import pytest

from aegisrun.agents import AgentProfileSnapshot, AgentSpec
from aegisrun.harness import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityRegistry,
    ResourceScope,
)


def test_capability_scopes_are_layered_feature_checked_and_disposable() -> None:
    registry = CapabilityRegistry()
    provider = object()
    registration = registry.register(
        CapabilityDescriptor("model", "deepseek", "1", frozenset({"stream"})),
        provider,
    )
    child = registry.child({"model": frozenset({"deepseek"})})

    assert child.resolve("model", "deepseek", required_features=frozenset({"stream"})) is provider
    with pytest.raises(CapabilityError, match="lacks features"):
        child.resolve("model", "deepseek", required_features=frozenset({"vision"}))
    with pytest.raises(CapabilityError, match="outside"):
        registry.child({"model": frozenset()}).resolve("model", "deepseek")

    registration.dispose()
    registration.dispose()
    with pytest.raises(CapabilityError, match="unknown"):
        registry.resolve("model", "deepseek")


@pytest.mark.asyncio
async def test_resource_scope_cancels_owned_tasks_and_runs_all_disposers() -> None:
    disposed: list[str] = []
    started = asyncio.Event()

    async def background() -> None:
        started.set()
        await asyncio.Event().wait()

    scope = ResourceScope()
    scope.add_disposer(lambda: disposed.append("first"))
    scope.add_disposer(lambda: disposed.append("second"))
    task = scope.spawn(background())
    await started.wait()
    await scope.close()

    assert task.cancelled()
    assert disposed == ["second", "first"]


@pytest.mark.asyncio
async def test_parent_resource_scope_owns_child_scope_lifecycle() -> None:
    parent = ResourceScope()
    child = parent.child({})
    started = asyncio.Event()

    async def background() -> None:
        started.set()
        await asyncio.Event().wait()

    task = child.spawn(background())
    await started.wait()
    await parent.close()

    assert task.cancelled()
    with pytest.raises(RuntimeError, match="closed"):
        child.spawn(background())


def test_agent_profile_can_only_narrow_parent_authority() -> None:
    parent = AgentProfileSnapshot.from_spec(
        AgentSpec(
            "research-agent",
            "bounded",
            frozenset({"read", "summarize"}),
            frozenset({"market-skill"}),
            frozenset({"market-read"}),
            frozenset({"market-data"}),
            max_concurrency=2,
            network_allowed=True,
        )
    )
    child = parent.narrow(
        profile_id="read-only",
        allowed_handlers=frozenset({"read"}),
        max_concurrency=1,
        network_allowed=False,
    )

    assert child.allowed_handlers == frozenset({"read"})
    assert child.digest != parent.digest
    assert child.generation != parent.generation
    assert child.parent_generation == parent.generation
    assert AgentProfileSnapshot.from_dict(child.to_dict()) == child
    with pytest.raises(ValueError, match="expand"):
        parent.narrow(
            profile_id="escalated",
            allowed_tools=frozenset({"market-read", "shell"}),
        )
