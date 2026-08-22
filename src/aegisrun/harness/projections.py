from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aegisrun.harness.events import AgentEvent
from aegisrun.harness.surface import SurfaceMessage, derive_surface


@dataclass(slots=True)
class HarnessProjection:
    run_id: str | None = None
    session_id: str | None = None
    last_seq: int = 0
    session_header: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    subagents: dict[str, dict[str, Any]] = field(default_factory=dict)
    skill_catalog: list[dict[str, Any]] = field(default_factory=list)
    model_requests: dict[str, str] = field(default_factory=dict)
    request_headers: dict[str, dict[str, Any]] = field(default_factory=dict)
    surface: tuple[SurfaceMessage, ...] = ()

    def apply(self, event: AgentEvent) -> None:
        self.run_id = event.run_id
        self.session_id = event.session_id
        self.last_seq = event.seq
        payload = event.to_dict()["payload"]
        if event.event_type == "session/header":
            self.session_header = payload
        elif event.event_type in {"plan/created", "plan/revised", "plan/status"}:
            self.plan.update(payload)
        elif event.event_type in {"task/started", "task/ended", "task/approval-requested"}:
            task_id = event.task_id or str(payload.get("task_id", ""))
            if task_id:
                current = self.tasks.setdefault(task_id, {})
                current.update(payload)
                current["event_type"] = event.event_type
                current["seq"] = event.seq
        elif event.event_type == "subagent/started":
            child_id = str(payload["child_id"])
            self.subagents[child_id] = {
                **payload,
                "status": "running",
                "started_seq": event.seq,
            }
        elif event.event_type == "subagent/ended":
            child_id = str(payload["child_id"])
            current = self.subagents.setdefault(child_id, {"child_id": child_id})
            current.update(payload)
            current["status"] = str(payload.get("stop_reason", "error"))
            current["ended_seq"] = event.seq
        elif event.event_type == "subagent/created":
            child_id = str(payload["child_id"])
            self.subagents[child_id] = {
                **payload,
                "mode": "continuable",
                "status": "idle",
                "created_seq": event.seq,
                "reports": [],
            }
        elif event.event_type == "subagent/turn-started":
            child_id = str(payload["child_id"])
            current = self.subagents.setdefault(child_id, {"child_id": child_id})
            current.update(payload)
            current["status"] = "running"
        elif event.event_type == "subagent/report":
            child_id = str(payload["child_id"])
            current = self.subagents.setdefault(child_id, {"child_id": child_id})
            reports = current.setdefault("reports", [])
            if isinstance(reports, list):
                reports.append(payload)
        elif event.event_type == "subagent/turn-ended":
            child_id = str(payload["child_id"])
            current = self.subagents.setdefault(child_id, {"child_id": child_id})
            current["status"] = "idle"
            current["last_turn"] = payload
        elif event.event_type == "subagent/settled":
            child_id = str(payload["child_id"])
            current = self.subagents.setdefault(child_id, {"child_id": child_id})
            current.update(payload)
            current["status"] = str(payload.get("stop_reason", "error"))
            current["settled_seq"] = event.seq
        elif event.event_type == "skill/catalog":
            entries = payload.get("entries", [])
            self.skill_catalog = [dict(item) for item in entries if isinstance(item, dict)]
        elif event.event_type == "request/header":
            self.request_headers[str(payload["request_id"])] = payload
        elif event.event_type == "model/request":
            self.model_requests[str(payload["request_id"])] = "running"
        elif event.event_type in {"model/response", "model/failure"}:
            self.model_requests[str(payload["request_id"])] = event.event_type.rsplit("/", 1)[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "last_seq": self.last_seq,
            "session_header": self.session_header,
            "plan": self.plan,
            "tasks": self.tasks,
            "subagents": self.subagents,
            "skill_catalog": self.skill_catalog,
            "model_requests": self.model_requests,
            "request_headers": self.request_headers,
            "surface": [message.to_request_message() for message in self.surface],
        }


def project_events(events: tuple[AgentEvent, ...]) -> HarnessProjection:
    projection = HarnessProjection()
    for event in events:
        projection.apply(event)
    projection.surface = derive_surface(events)
    return projection
