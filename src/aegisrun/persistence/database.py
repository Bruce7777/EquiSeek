from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aegisrun.config import Settings, get_settings
from aegisrun.persistence.models import Base


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine: AsyncEngine = create_async_engine(
            self.settings.database_url,
            pool_pre_ping=True,
        )
        if self.engine.url.get_backend_name() == "sqlite":
            event.listen(self.engine.sync_engine, "connect", self._configure_sqlite)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self._secure_sqlite_file()

    async def drop_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def _secure_sqlite_file(self) -> None:
        if self.engine.url.get_backend_name() != "sqlite":
            return
        database = self.engine.url.database
        if database and database != ":memory:":
            path = Path(database)
            if path.exists():
                path.chmod(0o600)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session
