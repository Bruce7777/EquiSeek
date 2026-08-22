from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrajectoryCase:
    case_id: str
    actual: tuple[str, ...]
    required_subsequence: tuple[str, ...]
    forbidden: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrajectoryResult:
    case_id: str
    passed: bool
    reasons: tuple[str, ...]


def evaluate_trajectory(case: TrajectoryCase) -> TrajectoryResult:
    reasons: list[str] = []
    cursor = 0
    for required in case.required_subsequence:
        try:
            cursor = case.actual.index(required, cursor) + 1
        except ValueError:
            reasons.append(f"missing ordered action: {required}")
    for action in case.forbidden:
        if action in case.actual:
            reasons.append(f"forbidden action observed: {action}")
    return TrajectoryResult(case.case_id, not reasons, tuple(reasons))
