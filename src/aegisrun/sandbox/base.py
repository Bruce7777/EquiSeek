from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SandboxEnforcement(StrEnum):
    """How completely a backend enforces the boundary it advertises."""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SandboxCapabilities:
    """Honest, machine-readable facts about one sandbox provider."""

    backend: str
    enforcement: SandboxEnforcement
    security_boundary: bool
    file_effects: SandboxEnforcement
    network: SandboxEnforcement
    process: SandboxEnforcement
    resource_limits: SandboxEnforcement
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "enforcement": self.enforcement.value,
            "security_boundary": self.security_boundary,
            "file_effects": self.file_effects.value,
            "network": self.network.value,
            "process": self.process.value,
            "resource_limits_enforcement": self.resource_limits.value,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    output_truncated: bool = False
    enforcement: SandboxEnforcement = SandboxEnforcement.NONE


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    network_allowed: bool = False
    require_isolation: bool = False
    read_only_workspace: bool = False
    max_output_bytes: int = 64_000
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    pids_limit: int = 128
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_output_bytes < 1 or self.cpu_limit <= 0 or self.pids_limit < 1:
            raise ValueError("sandbox limits must be positive")


class SandboxProvider(Protocol):
    def capabilities(self) -> SandboxCapabilities: ...

    async def exec(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int,
        policy: SandboxPolicy | None = None,
    ) -> SandboxResult: ...
