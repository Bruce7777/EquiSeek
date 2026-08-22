from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aegisrun.core.domain import RunStatus


class CreateRunRequest(BaseModel):
    agent_name: Literal["issue_triage"] = "issue_triage"
    input: dict[str, Any] = Field(default_factory=dict)


class ApprovalView(BaseModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    version: int


class RunView(BaseModel):
    id: str
    thread_id: str
    agent_name: str
    status: RunStatus
    input: dict[str, Any]
    policy: dict[str, Any]
    budget: dict[str, Any]
    usage: dict[str, Any]
    terminal_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    approval: ApprovalView | None = None
    plan: dict[str, Any]


class WorkspaceView(BaseModel):
    run_id: str
    initialized: bool
    root: str
    layout_version: int
    task_ids: list[str]
    usage_bytes: int
    max_bytes: int


class EventView(BaseModel):
    seq: int
    event_type: str
    phase: str
    payload: dict[str, Any]
    created_at: datetime


class EventHistory(BaseModel):
    items: list[EventView]
    next_after_seq: int | None


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int
    reason: str | None = Field(default=None, max_length=1000)


class ArtifactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    artifact_type: str
    content_type: str
    checksum: str
    size_bytes: int
    workspace_revision: int
    creator_tool: str | None
    created_at: datetime


class ErrorResponse(BaseModel):
    code: str
    message: str
