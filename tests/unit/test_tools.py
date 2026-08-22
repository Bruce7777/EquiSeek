from __future__ import annotations

from typing import Any

import pytest

from aegisrun.core.domain import PolicySnapshot
from aegisrun.core.errors import PolicyDeniedError
from aegisrun.tools.registry import ToolRegistry
from aegisrun.tools.spec import RiskLevel, ToolResult, ToolSpec


def spec(name: str = "echo") -> ToolSpec:
    return ToolSpec(
        name,
        "1.0",
        "Echo input",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        RiskLevel.LOW,
        False,
    )


async def echo(arguments: dict[str, Any]) -> ToolResult:
    return ToolResult("echoed", arguments)


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(spec(), echo)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec(), echo)


def test_visible_specs_are_policy_filtered() -> None:
    registry = ToolRegistry()
    registry.register(spec(), echo)
    assert registry.visible_specs(PolicySnapshot(allowed_tools=("echo",)))[0].name == "echo"
    assert registry.visible_specs(PolicySnapshot(allowed_tools=())) == []


@pytest.mark.asyncio
async def test_execute_validates_schema() -> None:
    registry = ToolRegistry()
    registry.register(spec(), echo)
    with pytest.raises(ValueError, match="required property"):
        await registry.execute("echo", {}, PolicySnapshot(allowed_tools=("echo",)))


@pytest.mark.asyncio
async def test_execute_denies_policy_miss() -> None:
    registry = ToolRegistry()
    registry.register(spec(), echo)
    with pytest.raises(PolicyDeniedError):
        await registry.execute("echo", {"text": "x"}, PolicySnapshot(allowed_tools=()))


@pytest.mark.asyncio
async def test_execute_returns_result() -> None:
    registry = ToolRegistry()
    registry.register(spec(), echo)
    result = await registry.execute(
        "echo", {"text": "hello"}, PolicySnapshot(allowed_tools=("echo",))
    )
    assert result.data == {"text": "hello"}
