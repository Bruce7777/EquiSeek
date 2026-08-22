from __future__ import annotations

import stat
from pathlib import Path

import pytest

from aegisrun.config import Settings
from aegisrun.core.domain import BudgetSnapshot, PolicySnapshot, RunStatus
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import RunRepository


@pytest.mark.asyncio
async def test_local_sqlite_service_recovers_run_after_database_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "user-data" / "aegisrun.sqlite3"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database_path}",
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspaces",
    )
    settings.prepare_directories()
    first = Database(settings)
    await first.create_schema()
    async with first.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="local-investment-agent",
            input_json={"mode": "local-sqlite"},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key="local-restart",
        )
        run_id = run.id
        await session.commit()
    async with first.engine.connect() as connection:
        journal_mode = await connection.exec_driver_sql("PRAGMA journal_mode")
        foreign_keys = await connection.exec_driver_sql("PRAGMA foreign_keys")
        busy_timeout = await connection.exec_driver_sql("PRAGMA busy_timeout")
        assert journal_mode.scalar_one().casefold() == "wal"
        assert foreign_keys.scalar_one() == 1
        assert busy_timeout.scalar_one() == 5_000
    await first.dispose()

    reopened = Database(settings)
    async with reopened.session() as session:
        restored = await RunRepository(session).get(run_id)
        events = await RunRepository(session).list_events(run_id)
        claimed = await RunRepository(session).claim_next("single-local-worker", 30)
        await session.commit()

    assert restored.input_json == {"mode": "local-sqlite"}
    assert [event.seq for event in events] == [1, 2]
    assert claimed is not None and claimed.id == run_id
    assert claimed.status == RunStatus.RUNNING
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700
    await reopened.dispose()
