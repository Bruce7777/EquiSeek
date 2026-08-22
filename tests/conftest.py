from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio

from aegisrun.api.app import create_app
from aegisrun.config import Settings
from aegisrun.persistence.database import Database


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspaces",
        sandbox_backend="local",
        worker_id="test-worker",
        lease_seconds=1,
    )


@pytest_asyncio.fixture
async def settings(tmp_path: Path) -> Settings:
    value = make_settings(tmp_path)
    value.prepare_directories()
    return value


@pytest_asyncio.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    value = Database(settings)
    await value.create_schema()
    yield value
    await value.dispose()


@pytest_asyncio.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, database)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value
