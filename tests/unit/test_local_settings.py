from __future__ import annotations

import stat
from pathlib import Path

from aegisrun.config import Settings


def test_default_settings_use_stable_private_user_sqlite(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # type: ignore[attr-defined]
    settings = Settings(
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspaces",
        _env_file=None,
    )

    settings.prepare_directories()

    expected = tmp_path / ".equiseek" / "user-data" / "equiseek.sqlite3"
    expected_checkpoints = expected.with_name("equiseek-checkpoints.sqlite3")
    assert settings.database_url == f"sqlite+aiosqlite:///{expected}"
    assert settings.effective_checkpoint_url == str(expected_checkpoints)
    assert expected.parent.is_dir()
    assert stat.S_IMODE(expected.parent.stat().st_mode) == 0o700


def test_explicit_checkpoint_url_takes_precedence(tmp_path: Path) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        checkpoint_url=str(tmp_path / "custom" / "state.sqlite3"),
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspaces",
        _env_file=None,
    )

    settings.prepare_directories()

    assert settings.effective_checkpoint_url == str(tmp_path / "custom" / "state.sqlite3")
    assert stat.S_IMODE((tmp_path / "custom").stat().st_mode) == 0o700
