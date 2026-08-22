from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegisrun.core.errors import SandboxViolationError
from aegisrun.workspace.manager import WorkspaceManager


def test_workspace_manager_creates_run_and_isolated_task_layout(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "input.txt").write_text("seed", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "workspaces")

    run = manager.create_run("run-123", template=template)
    first = manager.create_task("run-123", "fetch")
    second = manager.create_task("run-123", "analyze")

    assert (run.shared / "input.txt").read_text(encoding="utf-8") == "seed"
    assert first.root != second.root
    assert {path.name for path in (first.input, first.output, first.temp)} == {
        "input",
        "output",
        "tmp",
    }
    description = manager.describe("run-123")
    assert description["initialized"] is True
    assert description["task_ids"] == ["analyze", "fetch"]


def test_workspace_ids_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    with pytest.raises(SandboxViolationError):
        manager.create_run("../escape")
    manager.create_run("safe")
    with pytest.raises(SandboxViolationError):
        manager.create_task("safe", "../escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    (manager.paths("safe").artifacts / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxViolationError):
        manager.write_json(manager.paths("safe").artifacts / "link" / "escaped.json", {"bad": True})


def test_atomic_json_does_not_leave_temporary_file(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    run = manager.create_run("atomic")
    target = run.artifacts / "snapshot.json"

    manager.write_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not list(target.parent.glob("*.tmp"))


def test_workspace_manifest_reports_usage_and_cleanup(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", max_bytes_per_run=1_000)
    workspace = manager.create_task("usage", "task")
    (workspace.output / "small.txt").write_text("ok", encoding="utf-8")

    description = manager.describe("usage")

    assert description["usage_bytes"] > 0
    assert description["max_bytes"] == 1_000
    manager.cleanup_task("usage", "task")
    assert not workspace.root.exists()
    manager.cleanup_run("usage")
    assert not manager.paths("usage").root.exists()


def test_workspace_quota_is_enforced_for_managed_writes(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", max_bytes_per_run=1_000)
    run = manager.create_run("quota")

    with pytest.raises(SandboxViolationError, match="quota"):
        manager.write_json(run.artifacts / "too-large.json", {"body": "x" * 2_000})

    manager.write_control_json(run.state / "plan.json", {"status": "failed"})
    assert (run.state / "plan.json").is_file()
    with pytest.raises(SandboxViolationError, match="control-data"):
        manager.write_control_json(run.artifacts / "bypass.json", {"bad": True})


def test_workspace_template_cannot_bypass_quota(tmp_path: Path) -> None:
    template = tmp_path / "large-template"
    template.mkdir()
    (template / "large.bin").write_bytes(b"x" * 2_000)
    manager = WorkspaceManager(tmp_path / "workspaces", max_bytes_per_run=1_000)

    with pytest.raises(SandboxViolationError, match="quota"):
        manager.create_run("template-quota", template=template)
    assert not (manager.paths("template-quota").shared / "large.bin").exists()
    assert not manager.paths("template-quota").root.exists()


def test_workspace_template_rejects_symlinks(tmp_path: Path) -> None:
    template = tmp_path / "symlink-template"
    template.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    (template / "link.txt").symlink_to(target)
    manager = WorkspaceManager(tmp_path / "workspaces")

    with pytest.raises(SandboxViolationError, match="symlinks"):
        manager.create_run("symlink-template", template=template)
    assert not manager.paths("symlink-template").root.exists()
