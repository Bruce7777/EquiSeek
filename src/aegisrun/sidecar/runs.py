from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

RunCallable = Callable[[Callable[[str, dict[str, Any]], None]], Awaitable[dict[str, Any]]]
NotificationCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class RunRecord:
    run_id: str
    kind: str
    status: str = "queued"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=512))
    task: asyncio.Task[None] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def view(self, *, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runId": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "createdAt": self.created_at,
            "lastSeq": self.events[-1]["seq"] if self.events else 0,
            "error": self.error,
        }
        if include_result:
            payload["result"] = self.result
        return payload


class RunRegistry:
    def __init__(
        self,
        notify: NotificationCallback | None = None,
        *,
        history_path: Path | None = None,
    ) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._notify = notify
        self.history_path = history_path
        self._load_history()

    def set_notifier(self, notify: NotificationCallback | None) -> None:
        self._notify = notify

    def start(self, kind: str, execute: RunCallable, *, run_id: str | None = None) -> RunRecord:
        run = RunRecord(run_id or f"{kind}-{uuid4().hex}", kind)
        if run.run_id in self._runs:
            raise ValueError(f"duplicate run: {run.run_id}")
        self._runs[run.run_id] = run
        run.task = asyncio.create_task(self._drive(run, execute), name=run.run_id)
        return run

    def get(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run: {run_id}") from error

    def list_recent(self) -> list[dict[str, Any]]:
        recent = tuple(self._runs.values())[-30:]
        return [item.view(include_result=False) for item in reversed(recent)]

    def delete(self, run_id: str) -> None:
        run = self.get(run_id)
        if run.task is not None and not run.task.done():
            raise ValueError("running task cannot be deleted")
        del self._runs[run_id]
        self._persist_history()

    async def cancel(self, run_id: str) -> RunRecord:
        run = self.get(run_id)
        if run.task is not None and not run.task.done():
            run.task.cancel()
            try:
                await run.task
            except asyncio.CancelledError:
                pass
        return run

    def events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return [event for event in self.get(run_id).events if event["seq"] > after_seq]

    async def shutdown(self) -> None:
        pending = [run.task for run in self._runs.values() if run.task and not run.task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _load_history(self) -> None:
        path = self.history_path
        if path is None or not path.is_file() or path.is_symlink():
            return
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = raw.get("runs", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            return
        for item in items[-50:]:
            if not isinstance(item, dict):
                continue
            run_id = item.get("runId")
            kind = item.get("kind")
            status = item.get("status")
            created_at = item.get("createdAt")
            if (
                not isinstance(run_id, str)
                or not isinstance(kind, str)
                or status not in {"succeeded", "failed", "cancelled"}
                or not isinstance(created_at, str)
            ):
                continue
            self._runs[run_id] = RunRecord(
                run_id=run_id,
                kind=kind,
                status=status,
                result=item.get("result") if isinstance(item.get("result"), dict) else None,
                error=item.get("error") if isinstance(item.get("error"), dict) else None,
                created_at=created_at,
            )

    def _persist_history(self) -> None:
        path = self.history_path
        if path is None:
            return
        completed = [
            {
                **run.view(include_result=True),
                "events": list(run.events),
            }
            for run in tuple(self._runs.values())[-50:]
            if run.status in {"succeeded", "failed", "cancelled"}
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"schemaVersion": 1, "runs": completed},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)

    async def _drive(self, run: RunRecord, execute: RunCallable) -> None:
        run.status = "running"
        await self._emit(run, "run.started", {"kind": run.kind})
        report_tasks: list[asyncio.Task[None]] = []

        def report(event_type: str, payload: dict[str, Any]) -> None:
            task = asyncio.get_running_loop().create_task(self._emit(run, event_type, payload))
            report_tasks.append(task)

        try:
            run.result = await execute(report)
        except asyncio.CancelledError:
            if report_tasks:
                await asyncio.gather(*report_tasks, return_exceptions=True)
            run.status = "cancelled"
            await self._emit(run, "run.cancelled", {})
            self._persist_history()
            raise
        except Exception as error:
            if report_tasks:
                await asyncio.gather(*report_tasks, return_exceptions=True)
            run.status = "failed"
            run.error = {
                "code": type(error).__name__,
                "message": str(error)[:800],
                "retryable": type(error).__name__ in {"TimeoutError", "ConnectError"},
            }
            await self._emit(run, "run.failed", run.error)
            self._persist_history()
        else:
            if report_tasks:
                await asyncio.gather(*report_tasks, return_exceptions=True)
            run.status = "succeeded"
            await self._emit(run, "run.succeeded", {"resultKind": run.result.get("kind")})
            self._persist_history()

    async def _emit(self, run: RunRecord, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "runId": run.run_id,
            "seq": (run.events[-1]["seq"] + 1) if run.events else 1,
            "type": event_type,
            "at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        run.events.append(event)
        if self._notify is not None:
            await self._notify(event)
