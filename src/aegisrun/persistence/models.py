from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RunModel(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    budget_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    budget_usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    runtime_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(200), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(64))
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RunEventModel(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_public: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_audit_ref: Mapped[str | None] = mapped_column(String(500))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalModel(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("run_id", "invocation_id", name="uq_approval_invocation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    invocation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolInvocationModel(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_artifact_id: Mapped[str | None] = mapped_column(String(36))
    external_reference: Mapped[str | None] = mapped_column(String(300))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workspace_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    creator_tool: Mapped[str | None] = mapped_column(String(100))
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_run_events_run_seq", RunEventModel.run_id, RunEventModel.seq)
