from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aegisrun.core.errors import InvalidTransitionError


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class TerminalReason(StrEnum):
    COMPLETED = "completed"
    APPROVAL_REJECTED = "approval_rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOOP_DETECTED = "loop_detected"
    TOOL_UNRECOVERABLE = "tool_unrecoverable"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    UNKNOWN_EXTERNAL_OUTCOME = "unknown_external_outcome"
    INTERNAL_ERROR = "internal_error"
    USER_CANCELLED = "user_cancelled"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class InvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.QUEUED,
        RunStatus.WAITING_APPROVAL,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_APPROVAL: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def assert_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"cannot transition run from {current} to {target}")


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    max_model_turns: int = 12
    max_tool_calls: int = 20
    max_output_bytes: int = 64_000
    max_wall_seconds: int = 300
    max_repeated_action: int = 3

    def to_dict(self) -> dict[str, int]:
        return {
            "max_model_turns": self.max_model_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_output_bytes": self.max_output_bytes,
            "max_wall_seconds": self.max_wall_seconds,
            "max_repeated_action": self.max_repeated_action,
        }


@dataclass(slots=True)
class BudgetUsage:
    model_turns: int = 0
    tool_calls: int = 0
    output_bytes: int = 0
    repeated_actions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_turns": self.model_turns,
            "tool_calls": self.tool_calls,
            "output_bytes": self.output_bytes,
            "repeated_actions": self.repeated_actions,
        }


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    allowed_tools: tuple[str, ...] = (
        "list_files",
        "read_file",
        "search_text",
        "run_tests",
        "create_patch",
        "apply_patch",
    )
    approval_required: tuple[str, ...] = ("apply_patch",)
    readable_prefixes: tuple[str, ...] = (".",)
    writable_prefixes: tuple[str, ...] = (".",)
    network_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "approval_required": list(self.approval_required),
            "readable_prefixes": list(self.readable_prefixes),
            "writable_prefixes": list(self.writable_prefixes),
            "network_allowed": self.network_allowed,
        }
