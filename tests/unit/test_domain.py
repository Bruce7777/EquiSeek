from __future__ import annotations

import pytest

from aegisrun.core.domain import (
    ALLOWED_TRANSITIONS,
    BudgetSnapshot,
    BudgetUsage,
    PolicySnapshot,
    RunStatus,
    assert_transition,
)
from aegisrun.core.errors import InvalidTransitionError


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunStatus.QUEUED, RunStatus.RUNNING),
        (RunStatus.QUEUED, RunStatus.CANCELLED),
        (RunStatus.RUNNING, RunStatus.QUEUED),
        (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL),
        (RunStatus.RUNNING, RunStatus.SUCCEEDED),
        (RunStatus.RUNNING, RunStatus.FAILED),
        (RunStatus.RUNNING, RunStatus.CANCELLED),
        (RunStatus.WAITING_APPROVAL, RunStatus.QUEUED),
        (RunStatus.WAITING_APPROVAL, RunStatus.CANCELLED),
    ],
)
def test_allowed_transitions(source: RunStatus, target: RunStatus) -> None:
    assert_transition(source, target)


@pytest.mark.parametrize("terminal", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED])
def test_terminal_states_are_irreversible(terminal: RunStatus) -> None:
    assert not ALLOWED_TRANSITIONS[terminal]
    with pytest.raises(InvalidTransitionError):
        assert_transition(terminal, RunStatus.QUEUED)


def test_status_terminal_property() -> None:
    assert RunStatus.SUCCEEDED.terminal
    assert not RunStatus.RUNNING.terminal


def test_budget_snapshot_serialization() -> None:
    assert BudgetSnapshot(max_model_turns=3).to_dict()["max_model_turns"] == 3


def test_budget_usage_serialization_is_complete() -> None:
    assert BudgetUsage(model_turns=2).to_dict() == {
        "model_turns": 2,
        "tool_calls": 0,
        "output_bytes": 0,
        "repeated_actions": {},
    }


def test_policy_defaults_hide_network() -> None:
    policy = PolicySnapshot()
    assert "apply_patch" in policy.allowed_tools
    assert policy.network_allowed is False
    assert policy.to_dict()["approval_required"] == ["apply_patch"]
