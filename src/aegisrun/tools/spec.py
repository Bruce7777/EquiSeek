from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel
    side_effect: bool
    timeout_seconds: int = 30
    required_capabilities: frozenset[str] = frozenset()
    concurrency_safe: bool = False

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("tool timeout must be positive")
        if self.concurrency_safe and self.side_effect:
            raise ValueError("side-effecting tools cannot be marked concurrency-safe")


@dataclass(frozen=True, slots=True)
class ToolResult:
    summary: str
    data: dict[str, Any]
    artifact_id: str | None = None
    external_reference: str | None = None


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult]]
