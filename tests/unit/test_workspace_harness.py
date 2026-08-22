from __future__ import annotations

import sys

import pytest

from aegisrun.core.errors import SandboxViolationError
from aegisrun.harness.workspace_tools import PersistentWorkspaceShell, WorkspaceFileEditor


def test_workspace_editor_requires_observed_state_before_overwrite(tmp_path) -> None:
    target = tmp_path / "thesis.md"
    target.write_text("old thesis\n", encoding="utf-8")
    editor = WorkspaceFileEditor(tmp_path, writable=True)

    with pytest.raises(SandboxViolationError, match="必须先调用 read"):
        editor.write("thesis.md", "blind overwrite\n")

    observed = editor.read("thesis.md")
    assert observed["lines"][0]["text"] == "old thesis"
    edited = editor.edit("thesis.md", "old", "updated")

    assert edited["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "updated thesis\n"


def test_workspace_editor_rejects_a_stale_observation(tmp_path) -> None:
    target = tmp_path / "thesis.md"
    target.write_text("first\n", encoding="utf-8")
    editor = WorkspaceFileEditor(tmp_path, writable=True)
    editor.read("thesis.md")
    target.write_text("changed elsewhere\n", encoding="utf-8")

    with pytest.raises(SandboxViolationError, match="其他程序修改"):
        editor.edit("thesis.md", "first", "second")


def test_workspace_editor_blocks_escape_symlinks_and_read_only_mutation(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "escape").symlink_to(outside)
    editor = WorkspaceFileEditor(tmp_path, writable=False)

    with pytest.raises(SandboxViolationError):
        editor.read("../outside.txt")
    with pytest.raises(SandboxViolationError, match="符号链接"):
        editor.read("escape")
    with pytest.raises(SandboxViolationError, match="只读"):
        editor.write("new.md", "blocked")


@pytest.mark.skipif(sys.platform != "darwin", reason="the enforced shell uses macOS Seatbelt")
async def test_persistent_workspace_shell_keeps_cwd_and_environment(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    shell = PersistentWorkspaceShell(tmp_path, writable=True, network_allowed=False)
    try:
        first = await shell.run("export EQUISEEK_TEST_VALUE=ready; cd notes")
        second = await shell.run('printf "%s" "$EQUISEEK_TEST_VALUE"')
        third = await shell.run("printf 'generated' > result.txt")
    finally:
        await shell.close()

    assert first.exit_code == 0
    assert first.cwd == str(tmp_path / "notes")
    assert second.output == "ready"
    assert second.cwd == str(tmp_path / "notes")
    assert third.exit_code == 0
    assert (tmp_path / "notes" / "result.txt").read_text(encoding="utf-8") == "generated"


@pytest.mark.skipif(sys.platform != "darwin", reason="the enforced shell uses macOS Seatbelt")
async def test_read_only_workspace_shell_cannot_write_selected_workspace(tmp_path) -> None:
    shell = PersistentWorkspaceShell(tmp_path, writable=False, network_allowed=False)
    try:
        result = await shell.run("printf 'blocked' > denied.txt")
    finally:
        await shell.close()

    assert result.exit_code != 0
    assert not (tmp_path / "denied.txt").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="the enforced shell uses macOS Seatbelt")
async def test_exited_persistent_shell_resets_before_the_next_call(tmp_path) -> None:
    shell = PersistentWorkspaceShell(tmp_path, writable=True, network_allowed=False)
    try:
        exited = await shell.run("export LOST=state; exit 7")
        restarted = await shell.run('printf "%s" "${LOST-unset}"')
    finally:
        await shell.close()

    assert exited.exit_code == 7
    assert exited.reset is True
    assert "persistent shell reset" in exited.output
    assert restarted.output == "unset"
