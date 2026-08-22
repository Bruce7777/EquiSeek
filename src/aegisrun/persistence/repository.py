from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisrun.core.domain import (
    ApprovalDecision,
    ApprovalStatus,
    BudgetSnapshot,
    BudgetUsage,
    PolicySnapshot,
    RunStatus,
    TerminalReason,
    assert_transition,
)
from aegisrun.core.errors import ConflictError, NotFoundError
from aegisrun.core.security import canonical_hash
from aegisrun.orchestration.models import ExecutionPlan, TaskStatus, issue_triage_plan
from aegisrun.persistence.models import ApprovalModel, RunEventModel, RunModel, utcnow


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        agent_name: str,
        input_json: dict[str, Any],
        policy: PolicySnapshot,
        budget: BudgetSnapshot,
        idempotency_key: str | None,
    ) -> tuple[RunModel, bool]:
        request_hash = canonical_hash({"agent_name": agent_name, "input": input_json})
        if idempotency_key:
            existing = await self.session.scalar(
                select(RunModel).where(RunModel.idempotency_key == idempotency_key)
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise ConflictError("idempotency key was used with a different request")
                return existing, False
        run = RunModel(
            id=str(uuid4()),
            thread_id=str(uuid4()),
            agent_name=agent_name,
            status=RunStatus.QUEUED,
            input_json=input_json,
            policy_snapshot=policy.to_dict(),
            budget_snapshot=budget.to_dict(),
            budget_usage=BudgetUsage().to_dict(),
            runtime_state={"phase": "created"},
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(run)
        await self.session.flush()
        plan = issue_triage_plan(run.id)
        run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
        await self.append_event(run, "run.created", "completed", {"status": run.status})
        await self.append_event(
            run,
            "plan.created",
            "completed",
            {"goal": plan.goal, "tasks": list(plan.tasks)},
        )
        return run, True

    async def get(self, run_id: str, *, for_update: bool = False) -> RunModel:
        statement: Select[tuple[RunModel]] = select(RunModel).where(RunModel.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        run = await self.session.scalar(statement)
        if not run:
            raise NotFoundError(f"run not found: {run_id}")
        return run

    async def append_event(
        self,
        run: RunModel,
        event_type: str,
        phase: str,
        payload: dict[str, Any] | None = None,
        *,
        audit_ref: str | None = None,
    ) -> RunEventModel:
        seq = run.next_event_seq
        run.next_event_seq += 1
        event = RunEventModel(
            run_id=run.id,
            seq=seq,
            event_type=event_type,
            phase=phase,
            payload_public=payload or {},
            payload_audit_ref=audit_ref,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def transition(
        self,
        run: RunModel,
        target: RunStatus,
        *,
        reason: TerminalReason | None = None,
        event_type: str = "run.status_changed",
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = RunStatus(run.status)
        assert_transition(current, target)
        run.status = target
        run.version += 1
        run.updated_at = utcnow()
        if target.terminal:
            run.terminal_reason = reason or TerminalReason.INTERNAL_ERROR
            run.lease_owner = None
            run.lease_expires_at = None
        event_payload: dict[str, Any] = {
            "from": current,
            "to": target,
            "reason": reason,
        }
        event_payload.update(payload or {})
        await self.append_event(run, event_type, "completed", event_payload)

    async def claim_next(self, worker_id: str, lease_seconds: int) -> RunModel | None:
        now = datetime.now(UTC)
        statement = (
            select(RunModel)
            .where(
                or_(
                    RunModel.status == RunStatus.QUEUED,
                    (
                        (RunModel.status == RunStatus.RUNNING)
                        & RunModel.recoverable.is_(True)
                        & (RunModel.lease_expires_at < now)
                    ),
                )
            )
            .order_by(RunModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        run = await self.session.scalar(statement)
        if not run:
            return None
        if RunStatus(run.status) is RunStatus.RUNNING:
            await self.transition(
                run,
                RunStatus.QUEUED,
                event_type="lease.expired",
                payload={"previous_owner": run.lease_owner},
            )
        await self.transition(run, RunStatus.RUNNING, event_type="lease.acquired")
        run.lease_owner = worker_id
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.runtime_state = {**run.runtime_state, "last_worker": worker_id}
        return run

    async def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> RunModel:
        run = await self.get(run_id, for_update=True)
        if run.status != RunStatus.RUNNING or run.lease_owner != worker_id:
            raise ConflictError("worker does not own the active lease")
        run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        await self.append_event(run, "lease.heartbeat", "progress", {"owner": worker_id})
        return run

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[RunEventModel]:
        await self.get(run_id)
        result = await self.session.scalars(
            select(RunEventModel)
            .where(RunEventModel.run_id == run_id, RunEventModel.seq > after_seq)
            .order_by(RunEventModel.seq)
            .limit(limit)
        )
        return list(result)


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = RunRepository(session)

    async def create(
        self,
        *,
        run: RunModel,
        invocation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalModel:
        existing = await self.session.scalar(
            select(ApprovalModel).where(
                ApprovalModel.run_id == run.id,
                ApprovalModel.invocation_id == invocation_id,
            )
        )
        if existing:
            return existing
        approval = ApprovalModel(
            id=str(uuid4()),
            run_id=run.id,
            invocation_id=invocation_id,
            tool_name=tool_name,
            arguments_json=arguments,
            status=ApprovalStatus.PENDING,
        )
        self.session.add(approval)
        await self.runs.transition(
            run,
            RunStatus.WAITING_APPROVAL,
            event_type="approval.requested",
            payload={"approval_id": approval.id, "tool_name": tool_name},
        )
        return approval

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        expected_version: int,
        reason: str | None,
    ) -> ApprovalModel:
        approval = await self.session.scalar(
            select(ApprovalModel).where(ApprovalModel.id == approval_id).with_for_update()
        )
        if not approval:
            raise NotFoundError(f"approval not found: {approval_id}")
        if approval.version != expected_version or approval.status != ApprovalStatus.PENDING:
            raise ConflictError("approval was already decided or version is stale")
        run = await self.runs.get(approval.run_id, for_update=True)
        approval.status = (
            ApprovalStatus.APPROVED
            if decision is ApprovalDecision.APPROVE
            else ApprovalStatus.REJECTED
        )
        approval.version += 1
        approval.decision_reason = reason
        approval.decided_at = utcnow()
        if decision is ApprovalDecision.APPROVE:
            runtime_state = dict(run.runtime_state)
            raw_plan = runtime_state.get("plan")
            if isinstance(raw_plan, dict):
                plan = ExecutionPlan.from_dict(raw_plan)
                waiting = next(
                    (
                        task
                        for task in plan.tasks.values()
                        if task.status is TaskStatus.WAITING_APPROVAL
                        and task.spec.handler == approval.tool_name
                    ),
                    None,
                )
                if waiting is not None:
                    plan.approve_task(waiting.id)
                    runtime_state["plan"] = plan.to_dict()
            run.runtime_state = {
                **runtime_state,
                "approved_invocation_id": approval.invocation_id,
                "approval_id": approval.id,
            }
            await self.runs.transition(
                run,
                RunStatus.QUEUED,
                event_type="approval.approved",
                payload={"approval_id": approval.id},
            )
        else:
            raw_plan = run.runtime_state.get("plan")
            if isinstance(raw_plan, dict):
                plan = ExecutionPlan.from_dict(raw_plan)
                plan.cancel()
                run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
            await self.runs.transition(
                run,
                RunStatus.CANCELLED,
                reason=TerminalReason.APPROVAL_REJECTED,
                event_type="approval.rejected",
                payload={"approval_id": approval.id},
            )
        return approval

    async def pending_for_run(self, run_id: str) -> ApprovalModel | None:
        approval: ApprovalModel | None = await self.session.scalar(
            select(ApprovalModel).where(
                ApprovalModel.run_id == run_id,
                ApprovalModel.status == ApprovalStatus.PENDING,
            )
        )
        return approval
