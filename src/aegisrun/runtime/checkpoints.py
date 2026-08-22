from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from aegisrun.runtime.graph import build_checkpoint_graph


def _postgres_saver_class() -> Any:
    try:
        module = import_module("langgraph.checkpoint.postgres.aio")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "PostgreSQL checkpoints require the optional 'aegisrun[postgres]' extra"
        ) from error
    return module.AsyncPostgresSaver


class CheckpointCoordinator:
    """Maps a product Run thread to a LangGraph checkpoint thread."""

    def __init__(self, connection_url: str | None) -> None:
        self.connection_url = connection_url
        self._memory_saver = InMemorySaver()
        self._memory_graph = build_checkpoint_graph(self._memory_saver)
        self._postgres_ready = False
        self._sqlite_ready = False

    async def record(self, thread_id: str, phase: str, steps: int) -> dict[str, Any]:
        input_state = {"phase": phase, "steps": steps}
        config = cast(RunnableConfig, {"configurable": {"thread_id": thread_id}})
        if self._uses_memory:
            result = await self._memory_graph.ainvoke(input_state, config=config)
            return dict(result)

        if self._is_postgres:
            async_postgres_saver = _postgres_saver_class()
            async with async_postgres_saver.from_conn_string(self.connection_url) as saver:
                if not self._postgres_ready:
                    await saver.setup()
                    self._postgres_ready = True
                graph = build_checkpoint_graph(saver)
                result = await graph.ainvoke(input_state, config=config)
                return dict(result)
        async with AsyncSqliteSaver.from_conn_string(self._sqlite_path) as saver:
            if not self._sqlite_ready:
                await saver.setup()
                self._sqlite_ready = True
                self._secure_sqlite_file()
            graph = build_checkpoint_graph(saver)
            result = await graph.ainvoke(input_state, config=config)
            return dict(result)

    async def latest(self, thread_id: str) -> dict[str, Any] | None:
        config = cast(RunnableConfig, {"configurable": {"thread_id": thread_id}})
        if self._uses_memory:
            snapshot = await self._memory_graph.aget_state(config)
            return dict(snapshot.values) if snapshot.values else None
        if self._is_postgres:
            async_postgres_saver = _postgres_saver_class()
            async with async_postgres_saver.from_conn_string(self.connection_url) as saver:
                snapshot = await saver.aget_tuple(config)
                return dict(snapshot.checkpoint["channel_values"]) if snapshot else None
        async with AsyncSqliteSaver.from_conn_string(self._sqlite_path) as saver:
            if not self._sqlite_ready:
                await saver.setup()
                self._sqlite_ready = True
                self._secure_sqlite_file()
            snapshot = await saver.aget_tuple(config)
            return dict(snapshot.checkpoint["channel_values"]) if snapshot else None

    @property
    def _uses_memory(self) -> bool:
        return not self.connection_url or self.connection_url == ":memory:"

    @property
    def _is_postgres(self) -> bool:
        return bool(
            self.connection_url
            and self.connection_url.startswith(("postgres://", "postgresql://"))
        )

    @property
    def _sqlite_path(self) -> str:
        assert self.connection_url is not None
        if "://" in self.connection_url:
            raise ValueError("checkpoint URL must be a PostgreSQL URL or local SQLite path")
        path = Path(self.connection_url).expanduser()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
        return str(path)

    def _secure_sqlite_file(self) -> None:
        path = Path(self._sqlite_path)
        if str(path) != ":memory:" and path.exists():
            path.chmod(0o600)
