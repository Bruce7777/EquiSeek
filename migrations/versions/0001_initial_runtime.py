"""Create the product runtime tables.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("thread_id", sa.String(36), nullable=False, unique=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("budget_snapshot", sa.JSON(), nullable=False),
        sa.Column("budget_usage", sa.JSON(), nullable=False),
        sa.Column("runtime_state", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("lease_owner", sa.String(200)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_reason", sa.String(64)),
        sa.Column("recoverable", sa.Boolean(), nullable=False),
        sa.Column("next_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_lease_owner", "runs", ["lease_owner"])
    op.create_index("ix_runs_lease_expires_at", "runs", ["lease_expires_at"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("payload_public", sa.JSON(), nullable=False),
        sa.Column("payload_audit_ref", sa.String(500)),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("span_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_run_seq", "run_events", ["run_id", "seq"])
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("side_effect", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("result_artifact_id", sa.String(36)),
        sa.Column("external_reference", sa.String(300)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("invocation_id", sa.String(36), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "invocation_id", name="uq_approval_invocation"),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("relative_path", sa.String(500), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("creator_tool", sa.String(100)),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("approvals")
    op.drop_table("tool_invocations")
    op.drop_table("run_events")
    op.drop_table("runs")
