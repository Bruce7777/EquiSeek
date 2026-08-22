from __future__ import annotations

from aegisrun.core.domain import BudgetSnapshot, BudgetUsage
from aegisrun.core.errors import BudgetExceededError


class BudgetManager:
    def __init__(self, snapshot: BudgetSnapshot, usage: BudgetUsage | None = None) -> None:
        self.snapshot = snapshot
        self.usage = usage or BudgetUsage()

    def consume_model_turn(self) -> None:
        self.usage.model_turns += 1
        if self.usage.model_turns > self.snapshot.max_model_turns:
            raise BudgetExceededError("model turn budget exhausted")

    def consume_tool_call(self, action_key: str, output_bytes: int = 0) -> None:
        self.usage.tool_calls += 1
        self.usage.output_bytes += output_bytes
        repeats = self.usage.repeated_actions.get(action_key, 0) + 1
        self.usage.repeated_actions[action_key] = repeats
        if self.usage.tool_calls > self.snapshot.max_tool_calls:
            raise BudgetExceededError("tool call budget exhausted")
        if self.usage.output_bytes > self.snapshot.max_output_bytes:
            raise BudgetExceededError("tool output budget exhausted")
        if repeats > self.snapshot.max_repeated_action:
            raise BudgetExceededError("repeated action limit exceeded")

    def consume_output(self, output_bytes: int) -> None:
        self.usage.output_bytes += output_bytes
        if self.usage.output_bytes > self.snapshot.max_output_bytes:
            raise BudgetExceededError("tool output budget exhausted")

    def check_wall_time(self, elapsed_seconds: float) -> None:
        if elapsed_seconds > self.snapshot.max_wall_seconds:
            raise BudgetExceededError("wall-clock budget exhausted")
