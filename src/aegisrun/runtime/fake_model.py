from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeAction:
    name: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None


class FakeIssueTriageModel:
    """Deterministic action planner used by tests and the offline demo."""

    def next_action(self, phase: str) -> FakeAction:
        actions = {
            "created": FakeAction("load_skill"),
            "skill_loaded": FakeAction("run_tests", "run_tests", {}),
            "tests_failed": FakeAction("read_source", "read_file", {"path": "calculator.py"}),
            "source_read": FakeAction("create_patch", "create_patch", {}),
            "patch_created": FakeAction("apply_patch", "apply_patch", {}),
            "patch_applied": FakeAction("verify", "run_tests", {}),
            "verified": FakeAction("finish"),
        }
        return actions.get(phase, FakeAction("finish"))
