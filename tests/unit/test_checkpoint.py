from __future__ import annotations

import stat
from pathlib import Path

import pytest

from aegisrun.runtime import checkpoints as checkpoint_module
from aegisrun.runtime.checkpoints import CheckpointCoordinator


@pytest.mark.asyncio
async def test_in_memory_checkpoint_round_trip() -> None:
    coordinator = CheckpointCoordinator(None)
    result = await coordinator.record("thread-1", "patch_created", 4)
    latest = await coordinator.latest("thread-1")
    assert result["phase"] == "patch_created"
    assert latest and latest["steps"] == 4


@pytest.mark.asyncio
async def test_checkpoint_threads_are_isolated() -> None:
    coordinator = CheckpointCoordinator(None)
    await coordinator.record("thread-a", "one", 1)
    await coordinator.record("thread-b", "two", 2)
    first = await coordinator.latest("thread-a")
    second = await coordinator.latest("thread-b")
    assert first and first["phase"] == "one"
    assert second and second["phase"] == "two"


@pytest.mark.asyncio
async def test_explicit_memory_checkpoint_round_trip() -> None:
    coordinator = CheckpointCoordinator(":memory:")

    await coordinator.record("thread-memory", "screening", 3)

    assert await coordinator.latest("thread-memory") == {"phase": "screening", "steps": 3}


@pytest.mark.asyncio
async def test_local_sqlite_checkpoint_survives_coordinator_restart(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "private" / "checkpoints.sqlite3"
    first = CheckpointCoordinator(str(checkpoint_path))

    assert await first.latest("missing-thread") is None
    await first.record("thread-local", "strategy_ready", 7)

    reopened = CheckpointCoordinator(str(checkpoint_path))
    latest = await reopened.latest("thread-local")

    assert latest == {"phase": "strategy_ready", "steps": 7}
    assert stat.S_IMODE(checkpoint_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(checkpoint_path.parent.stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_checkpoint_rejects_unsupported_remote_url() -> None:
    coordinator = CheckpointCoordinator("mysql://localhost/checkpoints")

    with pytest.raises(ValueError, match="PostgreSQL URL or local SQLite path"):
        await coordinator.latest("thread-1")


def test_postgres_checkpoint_reports_optional_extra_when_not_installed(
    monkeypatch: object,
) -> None:
    def missing(_: str) -> None:
        raise ModuleNotFoundError("optional package missing")

    monkeypatch.setattr(checkpoint_module, "import_module", missing)  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match=r"aegisrun\[postgres\]"):
        checkpoint_module._postgres_saver_class()
