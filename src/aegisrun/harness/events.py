from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from aegisrun.harness.invariants import InvariantRegistry

EVENT_TYPE = re.compile(r"^[a-z][a-z0-9-]*/[a-z][a-z0-9-]*$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
MAX_EVENT_BYTES = 256 * 1024


class EventError(ValueError):
    """Base error for the durable Harness event contract."""


class EventCorruptionError(EventError):
    """The persisted stream cannot be replayed without inventing facts."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_snapshot(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise EventError("event payload must be lossless JSON") from error
    try:
        return json.loads(encoded)
    except json.JSONDecodeError as error:  # pragma: no cover - encoder output is valid JSON
        raise EventError("event payload could not be snapshotted") from error


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, dict | MappingProxyType):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class EventSource:
    kind: str
    actor_id: str | None = None
    form: str | None = None

    def __post_init__(self) -> None:
        if not SAFE_ID.fullmatch(self.kind):
            raise EventError("event source kind is invalid")
        for value, label in ((self.actor_id, "actor id"), (self.form, "form")):
            if value is not None and not SAFE_ID.fullmatch(value):
                raise EventError(f"event source {label} is invalid")

    def to_dict(self) -> dict[str, str]:
        value = {"kind": self.kind}
        if self.actor_id is not None:
            value["actor_id"] = self.actor_id
        if self.form is not None:
            value["form"] = self.form
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EventSource:
        return cls(
            kind=str(value["kind"]),
            actor_id=str(value["actor_id"]) if value.get("actor_id") is not None else None,
            form=str(value["form"]) if value.get("form") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class AgentEvent:
    run_id: str
    session_id: str
    seq: int
    event_type: str
    schema_version: int
    created_at: str
    source: EventSource
    payload: Any
    parent_session_id: str | None = None
    turn_id: str | None = None
    step_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not SAFE_ID.fullmatch(self.run_id) or not SAFE_ID.fullmatch(self.session_id):
            raise EventError("run and session ids must be safe identifiers")
        if self.seq < 1 or self.schema_version < 1:
            raise EventError("event sequence and schema version must be positive")
        if not EVENT_TYPE.fullmatch(self.event_type):
            raise EventError(f"invalid event type: {self.event_type}")
        for value, label in (
            (self.parent_session_id, "parent session id"),
            (self.turn_id, "turn id"),
            (self.step_id, "step id"),
            (self.task_id, "task id"),
        ):
            if value is not None and not SAFE_ID.fullmatch(value):
                raise EventError(f"invalid event {label}")
        snapshot = _json_snapshot(self.payload)
        if not isinstance(snapshot, dict):
            raise EventError("event payload must be a JSON object")
        object.__setattr__(self, "payload", _freeze(snapshot))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": self.source.to_dict(),
            "payload": _thaw(self.payload),
        }
        for name in ("parent_session_id", "turn_id", "step_id", "task_id"):
            item = getattr(self, name)
            if item is not None:
                value[name] = item
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentEvent:
        source = value.get("source")
        payload = value.get("payload")
        if not isinstance(source, dict) or not isinstance(payload, dict):
            raise EventCorruptionError("event source and payload must be objects")
        try:
            return cls(
                run_id=str(value["run_id"]),
                session_id=str(value["session_id"]),
                seq=int(value["seq"]),
                event_type=str(value["event_type"]),
                schema_version=int(value["schema_version"]),
                created_at=str(value["created_at"]),
                source=EventSource.from_dict(source),
                payload=payload,
                parent_session_id=_optional_string(value, "parent_session_id"),
                turn_id=_optional_string(value, "turn_id"),
                step_id=_optional_string(value, "step_id"),
                task_id=_optional_string(value, "task_id"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise EventCorruptionError("persisted event is malformed") from error


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    return str(item) if item is not None else None


class EventStore(Protocol):
    run_id: str
    session_id: str

    async def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: EventSource,
        parent_session_id: str | None = None,
        turn_id: str | None = None,
        step_id: str | None = None,
        task_id: str | None = None,
    ) -> AgentEvent: ...

    async def load(self) -> tuple[AgentEvent, ...]: ...

    async def flush(self) -> None: ...


class WorkspaceEventStore:
    """Append-only, fsync-backed event stream for one local desktop session."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        session_id: str | None = None,
        invariants: InvariantRegistry | None = None,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.session_id = session_id or run_id
        self.invariants = invariants
        self._events: tuple[AgentEvent, ...] | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> tuple[AgentEvent, ...]:
        async with self._lock:
            return await self._load_locked()

    async def _load_locked(self) -> tuple[AgentEvent, ...]:
        if self._events is None:
            events = await asyncio.to_thread(self._load_sync)
            if self.invariants is not None:
                self.invariants.validate(events)
            self._events = events
        return self._events

    def _load_sync(self) -> tuple[AgentEvent, ...]:
        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise EventCorruptionError("event store path is not a regular file")
        data = self.path.read_bytes()
        if not data:
            return ()
        if not data.endswith(b"\n"):
            raise EventCorruptionError("event log ends with an incomplete record")
        events: list[AgentEvent] = []
        for line_number, raw in enumerate(data.splitlines(), start=1):
            if not raw or len(raw) > MAX_EVENT_BYTES:
                raise EventCorruptionError(f"invalid event record at line {line_number}")
            try:
                decoded = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise EventCorruptionError(f"invalid event JSON at line {line_number}") from error
            if not isinstance(decoded, dict):
                raise EventCorruptionError(f"event at line {line_number} is not an object")
            event = AgentEvent.from_dict(decoded)
            if event.run_id != self.run_id or event.session_id != self.session_id:
                raise EventCorruptionError("event log identity does not match its store")
            events.append(event)
        return tuple(events)

    async def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: EventSource,
        parent_session_id: str | None = None,
        turn_id: str | None = None,
        step_id: str | None = None,
        task_id: str | None = None,
    ) -> AgentEvent:
        async with self._lock:
            current = await self._load_locked()
            event = AgentEvent(
                run_id=self.run_id,
                session_id=self.session_id,
                seq=len(current) + 1,
                event_type=event_type,
                schema_version=1,
                created_at=_now(),
                source=source,
                payload=payload,
                parent_session_id=parent_session_id,
                turn_id=turn_id,
                step_id=step_id,
                task_id=task_id,
            )
            candidate = (*current, event)
            if self.invariants is not None:
                self.invariants.validate(candidate)
            await asyncio.to_thread(self._append_sync, event)
            self._events = candidate
            return event

    def _append_sync(self, event: AgentEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise EventCorruptionError("event store cannot be a symbolic link")
        encoded = (
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            + b"\n"
        )
        if len(encoded) > MAX_EVENT_BYTES:
            raise EventError("event record exceeds the configured size limit")
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def flush(self) -> None:
        if not self.path.exists():
            return
        await asyncio.to_thread(self._flush_sync)

    def _flush_sync(self) -> None:
        # Windows implements fsync through _commit(), which rejects a
        # read-only descriptor with EBADF even though POSIX accepts it.
        descriptor = os.open(self.path, os.O_RDWR)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
