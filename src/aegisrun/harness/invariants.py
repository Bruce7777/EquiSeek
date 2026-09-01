from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aegisrun.harness.events import AgentEvent


class InvariantError(ValueError):
    def __init__(self, owner: str, message: str) -> None:
        super().__init__(f"{owner}: {message}")
        self.owner = owner


InvariantCheck = Callable[[Sequence["AgentEvent"]], None]


@dataclass(slots=True)
class _Registration:
    owner: str
    check: InvariantCheck


class InvariantRegistry:
    """Package-owned checks over a candidate durable event stream."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, owner: str, check: InvariantCheck) -> Callable[[], None]:
        if not owner.strip() or owner in self._registrations:
            raise ValueError(f"duplicate or empty invariant owner: {owner}")
        registration = _Registration(owner, check)
        self._registrations[owner] = registration
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._registrations.get(owner) is registration:
                del self._registrations[owner]
            active = False

        return dispose

    def validate(self, events: Sequence[AgentEvent]) -> None:
        for registration in tuple(self._registrations.values()):
            try:
                registration.check(events)
            except InvariantError:
                raise
            except Exception as error:
                raise InvariantError(registration.owner, str(error)) from error

    def owners(self) -> tuple[str, ...]:
        return tuple(self._registrations)


def _identity(payload: Any, key: str, owner: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvariantError(owner, f"event is missing {key}")
    return value


def session_invariant(events: Sequence[AgentEvent]) -> None:
    if not events:
        return
    run_id = events[0].run_id
    session_id = events[0].session_id
    for expected, event in enumerate(events, start=1):
        if event.seq != expected:
            raise InvariantError("session", "event sequence is discontinuous")
        if event.run_id != run_id or event.session_id != session_id:
            raise InvariantError("session", "event identity changed inside one stream")


def lifecycle_invariant(events: Sequence[AgentEvent]) -> None:
    open_tools: set[str] = set()
    closed_tools: set[str] = set()
    open_children: set[str] = set()
    closed_children: set[str] = set()
    open_models: set[str] = set()
    closed_models: set[str] = set()
    request_headers: dict[str, int] = {}
    continuable_children: set[str] = set()
    settled_continuable_children: set[str] = set()
    followups: dict[str, str] = {}
    consumed_followups: set[str] = set()
    open_turns: dict[str, str] = {}
    closed_turns: set[str] = set()
    open_agent_turns: set[str] = set()
    closed_agent_turns: set[str] = set()
    open_agent_steps: dict[str, str] = {}
    closed_agent_steps: set[str] = set()
    for event in events:
        if event.event_type == "turn/started":
            turn_id = _identity(event.payload, "turn_id", "turn")
            if event.turn_id != turn_id:
                raise InvariantError("turn", "event metadata does not match turn id")
            if turn_id in open_agent_turns or turn_id in closed_agent_turns:
                raise InvariantError("turn", "duplicate turn id")
            open_agent_turns.add(turn_id)
        elif event.event_type == "step/started":
            step_id = _identity(event.payload, "step_id", "step")
            if event.step_id != step_id:
                raise InvariantError("step", "event metadata does not match step id")
            if event.turn_id not in open_agent_turns:
                raise InvariantError("step", "step is outside an open turn")
            if step_id in open_agent_steps or step_id in closed_agent_steps:
                raise InvariantError("step", "duplicate step id")
            open_agent_steps[step_id] = event.turn_id
        elif event.event_type == "step/ended":
            step_id = _identity(event.payload, "step_id", "step")
            if (
                event.step_id != step_id
                or event.turn_id is None
                or open_agent_steps.get(step_id) != event.turn_id
            ):
                raise InvariantError("step", "orphan or mismatched step ending")
            del open_agent_steps[step_id]
            closed_agent_steps.add(step_id)
        elif event.event_type == "turn/ended":
            turn_id = _identity(event.payload, "turn_id", "turn")
            if event.turn_id != turn_id or turn_id not in open_agent_turns:
                raise InvariantError("turn", "orphan or mismatched turn ending")
            if turn_id in open_agent_steps.values():
                raise InvariantError("turn", "turn ended with an open step")
            open_agent_turns.remove(turn_id)
            closed_agent_turns.add(turn_id)
        elif event.event_type == "session/ended":
            if open_agent_turns or open_agent_steps or open_models or open_tools:
                raise InvariantError("session", "session ended with open lifecycle work")
        elif event.event_type == "request/header":
            request_id = _identity(event.payload, "request_id", "request")
            if request_id in request_headers:
                raise InvariantError("request", "duplicate request header id")
            request_headers[request_id] = event.seq
        elif event.event_type == "tool/call":
            call_id = _identity(event.payload, "call_id", "tool")
            if call_id in open_tools or call_id in closed_tools:
                raise InvariantError("tool", "duplicate tool call id")
            open_tools.add(call_id)
        elif event.event_type == "tool/result":
            call_id = _identity(event.payload, "call_id", "tool")
            if call_id not in open_tools or call_id in closed_tools:
                raise InvariantError("tool", "orphan or duplicate tool result")
            open_tools.remove(call_id)
            closed_tools.add(call_id)
        elif event.event_type == "subagent/started":
            child_id = _identity(event.payload, "child_id", "subagent")
            if (
                child_id in open_children
                or child_id in closed_children
                or child_id in continuable_children
            ):
                raise InvariantError("subagent", "duplicate child id")
            open_children.add(child_id)
        elif event.event_type == "subagent/ended":
            child_id = _identity(event.payload, "child_id", "subagent")
            if child_id not in open_children or child_id in closed_children:
                raise InvariantError("subagent", "orphan or duplicate child ending")
            open_children.remove(child_id)
            closed_children.add(child_id)
        elif event.event_type == "subagent/created":
            child_id = _identity(event.payload, "child_id", "subagent")
            if (
                child_id in continuable_children
                or child_id in open_children
                or child_id in closed_children
            ):
                raise InvariantError("subagent", "duplicate continuable child id")
            continuable_children.add(child_id)
        elif event.event_type == "subagent/followup":
            child_id = _identity(event.payload, "child_id", "subagent")
            followup_id = _identity(event.payload, "followup_id", "subagent")
            if child_id not in continuable_children or child_id in settled_continuable_children:
                raise InvariantError("subagent", "followup targets an unavailable child")
            if followup_id in followups:
                raise InvariantError("subagent", "duplicate followup id")
            followups[followup_id] = child_id
        elif event.event_type == "subagent/turn-started":
            child_id = _identity(event.payload, "child_id", "subagent")
            followup_id = _identity(event.payload, "followup_id", "subagent")
            turn_id = _identity(event.payload, "turn_id", "subagent")
            if followups.get(followup_id) != child_id or followup_id in consumed_followups:
                raise InvariantError("subagent", "turn does not consume one queued followup")
            if child_id in open_turns or turn_id in closed_turns:
                raise InvariantError("subagent", "continuable child already has an open turn")
            consumed_followups.add(followup_id)
            open_turns[child_id] = turn_id
        elif event.event_type == "subagent/report":
            child_id = _identity(event.payload, "child_id", "subagent")
            turn_id = _identity(event.payload, "turn_id", "subagent")
            if open_turns.get(child_id) != turn_id:
                raise InvariantError("subagent", "report is outside its open child turn")
        elif event.event_type == "subagent/turn-ended":
            child_id = _identity(event.payload, "child_id", "subagent")
            turn_id = _identity(event.payload, "turn_id", "subagent")
            if open_turns.get(child_id) != turn_id:
                raise InvariantError("subagent", "orphan or mismatched child turn ending")
            del open_turns[child_id]
            closed_turns.add(turn_id)
        elif event.event_type == "subagent/settled":
            child_id = _identity(event.payload, "child_id", "subagent")
            if (
                child_id not in continuable_children
                or child_id in settled_continuable_children
                or child_id in open_turns
            ):
                raise InvariantError("subagent", "invalid continuable settlement")
            settled_continuable_children.add(child_id)
        elif event.event_type == "model/request":
            request_id = _identity(event.payload, "request_id", "model")
            if request_id in open_models or request_id in closed_models:
                raise InvariantError("model", "duplicate model request id")
            header_seq = event.payload.get("header_seq")
            if header_seq is not None and request_headers.get(request_id) != header_seq:
                raise InvariantError("request", "model request does not reference its header")
            open_models.add(request_id)
        elif event.event_type in {"model/response", "model/failure"}:
            request_id = _identity(event.payload, "request_id", "model")
            if request_id not in open_models or request_id in closed_models:
                raise InvariantError("model", "orphan or duplicate model terminal event")
            open_models.remove(request_id)
            closed_models.add(request_id)


def surface_invariant(events: Sequence[AgentEvent]) -> None:
    for event in events:
        if event.event_type in {"user/message", "assistant/message"}:
            expected_role = event.event_type.split("/", 1)[0]
            if event.payload.get("role") != expected_role:
                raise InvariantError("surface", "message role does not match event type")
            content = event.payload.get("content")
            if not isinstance(content, tuple) or not content:
                raise InvariantError("surface", "message content must be non-empty")
        elif event.event_type == "surface/replace":
            start = event.payload.get("start_seq")
            end = event.payload.get("end_seq")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or start > end
                or end >= event.seq
            ):
                raise InvariantError("surface", "replacement range is invalid")
            if not isinstance(event.payload.get("message"), Mapping):
                raise InvariantError("surface", "replacement message is missing")


def default_invariants() -> InvariantRegistry:
    registry = InvariantRegistry()
    registry.register("session", session_invariant)
    registry.register("lifecycle", lifecycle_invariant)
    registry.register("surface", surface_invariant)
    return registry
