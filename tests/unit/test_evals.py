from __future__ import annotations

import pytest

from aegisrun.evals.runner import TrajectoryCase, evaluate_trajectory


@pytest.mark.parametrize(
    ("case_id", "actual", "required", "forbidden", "passed"),
    [
        ("T01", ("load", "test", "read"), ("load", "read"), (), True),
        ("T02", ("read", "patch"), ("read", "patch"), ("delete",), True),
        ("T03", ("approve", "apply"), ("approve", "apply"), (), True),
        ("T04", ("test", "finish"), ("test", "finish"), (), True),
        ("T05", ("load",), ("load",), ("network",), True),
        ("T06", ("read",), ("read", "patch"), (), False),
        ("T07", ("apply", "approve"), ("approve", "apply"), (), False),
        ("T08", ("read", "delete"), ("read",), ("delete",), False),
        ("T09", (), ("finish",), (), False),
        ("T10", ("load", "read", "finish"), ("load", "finish"), ("shell",), True),
    ],
)
def test_versioned_trajectory_cases(
    case_id: str,
    actual: tuple[str, ...],
    required: tuple[str, ...],
    forbidden: tuple[str, ...],
    passed: bool,
) -> None:
    result = evaluate_trajectory(TrajectoryCase(case_id, actual, required, forbidden))
    assert result.passed is passed
    assert result.case_id == case_id
