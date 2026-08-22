from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

from aegisrun.harness.capabilities import CapabilityRegistry

Disposer = Callable[[], Awaitable[None] | None]


class ResourceScope:
    """Owns registrations, background tasks, and teardown for one runtime scope."""

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        *,
        parent: ResourceScope | None = None,
        allowed: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.registry: CapabilityRegistry
        if parent is not None:
            if allowed is None:
                raise ValueError("child scopes require an explicit capability ceiling")
            self.registry = parent.registry.child(allowed)
        else:
            self.registry = registry or CapabilityRegistry()
        self.parent = parent
        self._disposers: list[Disposer] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    def add_disposer(self, disposer: Disposer) -> None:
        if self._closed:
            raise RuntimeError("resource scope is closed")
        self._disposers.append(disposer)

    def spawn(
        self, work: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> asyncio.Task[Any]:
        if self._closed:
            work.close()
            raise RuntimeError("resource scope is closed")
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def child(self, allowed: Mapping[str, frozenset[str]]) -> ResourceScope:
        if self._closed:
            raise RuntimeError("resource scope is closed")
        child = ResourceScope(parent=self, allowed=allowed)
        self.add_disposer(child.close)
        return child

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        errors: list[Exception] = []
        for disposer in reversed(self._disposers):
            try:
                result = disposer()
                if inspect.isawaitable(result):
                    await result
            except Exception as error:  # teardown must try every disposer
                errors.append(error)
        self.registry.close()
        if errors:
            raise ExceptionGroup("resource scope teardown failed", errors)

    async def __aenter__(self) -> ResourceScope:
        if self._closed:
            raise RuntimeError("resource scope is closed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
