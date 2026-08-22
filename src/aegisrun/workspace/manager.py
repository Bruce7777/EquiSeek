from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from aegisrun.core.errors import SandboxViolationError
from aegisrun.core.security import safe_join
from aegisrun.orchestration.models import ExecutionPlan

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise SandboxViolationError(f"invalid {label}: {value}")
    return value


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    shared: Path
    tasks: Path
    artifacts: Path
    state: Path
    logs: Path


@dataclass(frozen=True, slots=True)
class TaskWorkspace:
    root: Path
    input: Path
    output: Path
    temp: Path
    logs: Path


class PlanFileStore:
    def __init__(self, manager: WorkspaceManager, run_id: str) -> None:
        self.manager = manager
        self.run_id = run_id

    @property
    def path(self) -> Path:
        return self.manager.paths(self.run_id).state / "plan.json"

    def save(self, plan: ExecutionPlan) -> None:
        self.manager.write_control_json(self.path, plan.to_dict())

    def load(self) -> ExecutionPlan:
        return ExecutionPlan.from_dict(json.loads(self.path.read_text(encoding="utf-8")))


class WorkspaceManager:
    """Owns a constrained, inspectable directory tree for each run and task."""

    def __init__(self, root: Path, *, max_bytes_per_run: int = 512 * 1024 * 1024) -> None:
        if max_bytes_per_run < 1:
            raise ValueError("workspace quota must be positive")
        self.root = root.resolve()
        self.max_bytes_per_run = max_bytes_per_run
        self.root.mkdir(parents=True, exist_ok=True)

    def paths(self, run_id: str) -> WorkspacePaths:
        root = safe_join(self.root, _safe_id(run_id, "run id"))
        return WorkspacePaths(
            root=root,
            shared=root / "shared",
            tasks=root / "tasks",
            artifacts=root / "artifacts",
            state=root / ".state",
            logs=root / "logs",
        )

    def create_run(self, run_id: str, *, template: Path | None = None) -> WorkspacePaths:
        paths = self.paths(run_id)
        manifest = {
            "run_id": run_id,
            "layout_version": 1,
            "shared": "shared",
            "tasks": "tasks",
            "artifacts": "artifacts",
            "state": ".state",
            "logs": "logs",
        }
        template_root: Path | None = None
        if template is not None:
            template_root = template.resolve(strict=True)
            entries = tuple(template_root.rglob("*"))
            if any(entry.is_symlink() for entry in entries):
                raise SandboxViolationError("workspace templates cannot contain symlinks")
            template_bytes = sum(entry.stat().st_size for entry in entries if entry.is_file())
            manifest_bytes = len(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
            )
            if template_bytes + manifest_bytes > self.max_bytes_per_run:
                raise SandboxViolationError(f"workspace quota exceeded for run: {run_id}")
        for path in (
            paths.root,
            paths.shared,
            paths.tasks,
            paths.artifacts,
            paths.state,
            paths.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.write_control_json(paths.state / "workspace.json", manifest)
        if template_root is not None:
            if not any(paths.shared.iterdir()):
                for source in template_root.iterdir():
                    target = paths.shared / source.name
                    if source.is_dir():
                        shutil.copytree(source, target)
                    else:
                        shutil.copy2(source, target)
        return paths

    def create_task(self, run_id: str, task_id: str) -> TaskWorkspace:
        paths = self.create_run(run_id)
        root = safe_join(paths.tasks, _safe_id(task_id, "task id"))
        workspace = TaskWorkspace(
            root=root,
            input=root / "input",
            output=root / "output",
            temp=root / "tmp",
            logs=root / "logs",
        )
        for path in (
            workspace.root,
            workspace.input,
            workspace.output,
            workspace.temp,
            workspace.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return workspace

    def describe(self, run_id: str) -> dict[str, Any]:
        paths = self.paths(run_id)
        initialized = (paths.state / "workspace.json").is_file()
        task_ids = (
            sorted(path.name for path in paths.tasks.iterdir() if path.is_dir())
            if paths.tasks.is_dir()
            else []
        )
        return {
            "run_id": run_id,
            "initialized": initialized,
            "root": str(paths.root),
            "layout_version": 1,
            "task_ids": task_ids,
            "usage_bytes": self.usage_bytes(run_id) if paths.root.exists() else 0,
            "max_bytes": self.max_bytes_per_run,
        }

    def plan_store(self, run_id: str) -> PlanFileStore:
        self.create_run(run_id)
        return PlanFileStore(self, run_id)

    def usage_bytes(self, run_id: str) -> int:
        root = self.paths(run_id).root
        if not root.exists():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

    def ensure_within_quota(self, run_id: str) -> None:
        if self.usage_bytes(run_id) > self.max_bytes_per_run:
            raise SandboxViolationError(f"workspace quota exceeded for run: {run_id}")

    def cleanup_task(self, run_id: str, task_id: str) -> None:
        paths = self.paths(run_id)
        target = safe_join(paths.tasks, _safe_id(task_id, "task id"))
        if target.exists():
            shutil.rmtree(target)

    def cleanup_run(self, run_id: str) -> None:
        target = self.paths(run_id).root
        if target.exists():
            shutil.rmtree(target)

    def write_json(self, path: Path, value: dict[str, Any]) -> None:
        self._write_json(path, value, enforce_quota=True)

    def write_control_json(self, path: Path, value: dict[str, Any]) -> None:
        """Persist bounded runtime metadata even when task data exhausted its quota."""

        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxViolationError(f"path escapes workspace root: {path}")
        relative = resolved.relative_to(self.root)
        allowed = len(relative.parts) >= 3 and (
            relative.parts[1] == ".state"
            or (
                len(relative.parts) >= 5
                and relative.parts[1] == "tasks"
                and relative.parts[3] == "logs"
            )
        )
        if not allowed:
            raise SandboxViolationError(f"not a control-data path: {path}")
        self._write_json(path, value, enforce_quota=False, max_bytes=64_000)

    def _write_json(
        self,
        path: Path,
        value: dict[str, Any],
        *,
        enforce_quota: bool,
        max_bytes: int | None = None,
    ) -> None:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxViolationError(f"path escapes workspace root: {path}")
        run_relative = resolved.relative_to(self.root)
        if not run_relative.parts:
            raise SandboxViolationError("managed write requires a run workspace")
        run_id = run_relative.parts[0]
        encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()
        if max_bytes is not None and len(encoded) > max_bytes:
            raise SandboxViolationError(f"control record is too large: {path.name}")
        existing = resolved.stat().st_size if resolved.exists() else 0
        if (
            enforce_quota
            and self.usage_bytes(run_id) - existing + len(encoded) > self.max_bytes_per_run
        ):
            raise SandboxViolationError(f"workspace quota exceeded for run: {run_id}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
