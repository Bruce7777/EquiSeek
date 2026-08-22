from __future__ import annotations

import pytest

from aegisrun.core.domain import BudgetSnapshot
from aegisrun.core.errors import BudgetExceededError
from aegisrun.tools.budget import BudgetManager


def test_budget_tracks_model_turns() -> None:
    manager = BudgetManager(BudgetSnapshot(max_model_turns=2))
    manager.consume_model_turn()
    assert manager.usage.model_turns == 1


def test_model_turn_limit() -> None:
    manager = BudgetManager(BudgetSnapshot(max_model_turns=0))
    with pytest.raises(BudgetExceededError, match="model turn"):
        manager.consume_model_turn()


def test_tool_call_limit() -> None:
    manager = BudgetManager(BudgetSnapshot(max_tool_calls=0))
    with pytest.raises(BudgetExceededError, match="tool call"):
        manager.consume_tool_call("read")


def test_repeated_action_limit() -> None:
    manager = BudgetManager(BudgetSnapshot(max_repeated_action=1))
    manager.consume_tool_call("same")
    with pytest.raises(BudgetExceededError, match="repeated action"):
        manager.consume_tool_call("same")


def test_output_limit_on_tool_call() -> None:
    manager = BudgetManager(BudgetSnapshot(max_output_bytes=1))
    with pytest.raises(BudgetExceededError, match="output"):
        manager.consume_tool_call("read", output_bytes=2)


def test_output_limit_after_execution() -> None:
    manager = BudgetManager(BudgetSnapshot(max_output_bytes=1))
    with pytest.raises(BudgetExceededError, match="output"):
        manager.consume_output(2)


def test_wall_clock_limit() -> None:
    manager = BudgetManager(BudgetSnapshot(max_wall_seconds=1))
    with pytest.raises(BudgetExceededError, match="wall-clock"):
        manager.check_wall_time(2)
