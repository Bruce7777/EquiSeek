from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from aegisrun.core.security import canonical_hash
from aegisrun.harness.events import AgentEvent, EventSource, EventStore
from aegisrun.harness.prompt import RUNTIME_CONTEXT_CLEARED, PromptAssembly

MESSAGE_EVENT_TYPES = frozenset({"user/message", "assistant/message", "tool/result"})


@dataclass(frozen=True, slots=True)
class SurfaceMessage:
    role: str
    content: tuple[dict[str, Any], ...]
    source_event_seqs: tuple[int, ...]
    message_id: str | None = None
    tool_call_id: str | None = None

    def to_request_message(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "role": self.role,
            "content": [dict(item) for item in self.content],
        }
        if self.tool_call_id is not None:
            value["tool_call_id"] = self.tool_call_id
        return value


def _content(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("surface message content must be a non-empty list")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError("surface message blocks must be objects")
    return tuple(dict(item) for item in value)


def _message_from_event(event: AgentEvent) -> SurfaceMessage | None:
    payload = event.to_dict()["payload"]
    if event.event_type in {"user/message", "assistant/message"}:
        role = event.event_type.split("/", 1)[0]
        return SurfaceMessage(
            role,
            _content(payload.get("content")),
            (event.seq,),
            message_id=str(payload.get("message_id")) if payload.get("message_id") else None,
        )
    if event.event_type == "tool/result":
        raw_content = payload.get("content")
        if raw_content is None:
            rendered = payload.get("summary")
            if rendered is None:
                rendered = json.dumps(payload.get("data"), ensure_ascii=False, sort_keys=True)
            raw_content = [{"type": "text", "text": str(rendered)}]
        return SurfaceMessage(
            "tool",
            _content(raw_content),
            (event.seq,),
            tool_call_id=str(payload.get("call_id")) if payload.get("call_id") else None,
        )
    if event.event_type == "surface/replace":
        replacement = payload.get("message")
        if not isinstance(replacement, dict):
            raise ValueError("surface replacement requires a message object")
        raw_role = replacement.get("role")
        if raw_role not in {"user", "assistant", "tool"}:
            raise ValueError("surface replacement role is invalid")
        role = str(raw_role)
        return SurfaceMessage(
            role,
            _content(replacement.get("content")),
            tuple(int(item) for item in payload.get("source_event_seqs", [])),
            message_id=(
                str(replacement.get("message_id")) if replacement.get("message_id") else None
            ),
            tool_call_id=(
                str(replacement.get("tool_call_id")) if replacement.get("tool_call_id") else None
            ),
        )
    return None


def derive_surface(events: tuple[AgentEvent, ...]) -> tuple[SurfaceMessage, ...]:
    replaced: set[int] = set()
    for event in events:
        if event.event_type != "surface/replace":
            continue
        payload = event.payload
        start = int(payload["start_seq"])
        end = int(payload["end_seq"])
        replaced.update(range(start, end + 1))
    messages: list[SurfaceMessage] = []
    for event in events:
        if event.event_type in MESSAGE_EVENT_TYPES and event.seq in replaced:
            continue
        message = _message_from_event(event)
        if message is not None:
            messages.append(message)
    return tuple(messages)


class RuntimeContextProjection:
    """Append only changed runtime-context snapshots as durable user messages."""

    def __init__(self) -> None:
        self._last_digest: str | None = None

    async def project(
        self,
        assembly: PromptAssembly,
        events: EventStore,
        *,
        actor_id: str,
        task_id: str | None = None,
    ) -> AgentEvent | None:
        if self._last_digest is None:
            for event in reversed(await events.load()):
                if event.event_type != "user/message":
                    continue
                source = event.payload.get("message_source")
                if isinstance(source, dict) and source.get("kind") == "runtime-context":
                    digest = source.get("digest")
                    if isinstance(digest, str):
                        self._last_digest = digest
                    break
        text = assembly.runtime_context
        digest = canonical_hash(text)
        if digest == self._last_digest:
            return None
        if text is None and self._last_digest is None:
            return None
        visible = text or RUNTIME_CONTEXT_CLEARED
        event = await events.append(
            "user/message",
            {
                "message_id": f"runtime-context-{uuid4()}",
                "role": "user",
                "content": [{"type": "text", "text": visible}],
                "message_source": {"kind": "runtime-context", "digest": digest},
            },
            source=EventSource("runtime-context", actor_id=actor_id),
            task_id=task_id,
        )
        self._last_digest = digest
        return event
