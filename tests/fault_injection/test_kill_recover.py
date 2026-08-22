from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from aegisrun.config import Settings
from aegisrun.core.domain import (
    ApprovalDecision,
    BudgetSnapshot,
    InvocationStatus,
    PolicySnapshot,
    RunStatus,
)
from aegisrun.persistence.database import Database
from aegisrun.persistence.models import ToolInvocationModel
from aegisrun.persistence.repository import ApprovalRepository, RunRepository
from aegisrun.runtime.worker import Worker
from aegisrun.sandbox.base import SandboxPolicy, SandboxResult
from aegisrun.tools.invocations import InvocationRepository
from aegisrun.tools.spec import RiskLevel, ToolSpec


async def seed(database: Database) -> str:
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={"fault": True},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        run_id = run.id
        await session.commit()
        return run_id


class FastSandbox:
    async def exec(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int,
        policy: SandboxPolicy | None = None,
    ) -> SandboxResult:
        source = (workspace / "calculator.py").read_text()
        exit_code = 1 if "return total / count" in source else 0
        return SandboxResult(exit_code, "deterministic fake pytest", "", 1)


@pytest.mark.asyncio
@pytest.mark.fault
async def test_expired_claim_recovers(database: Database) -> None:
    run_id = await seed(database)
    async with database.session() as session:
        dead = await RunRepository(session).claim_next("dead", 30)
        assert dead
        dead.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    async with database.session() as session:
        recovered = await RunRepository(session).claim_next("alive", 30)
        assert recovered and recovered.id == run_id
        assert recovered.lease_owner == "alive"


@pytest.mark.asyncio
@pytest.mark.fault
async def test_read_tool_running_record_is_safely_retried(
    database: Database, settings: Settings
) -> None:
    run_id = await seed(database)
    worker = Worker(database, settings)
    worker.runtime.sandbox = FastSandbox()
    await worker.run_once()  # skill
    async with database.session() as session:
        run = await RunRepository(session).get(run_id, for_update=True)
        run.runtime_state = {**run.runtime_state, "phase": "skill_loaded"}
        await session.commit()
    await worker.run_once()
    async with database.session() as session:
        invocation = await session.scalar(
            select(ToolInvocationModel).where(ToolInvocationModel.tool_name == "run_tests")
        )
        assert invocation and invocation.status == InvocationStatus.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.fault
async def test_unknown_write_outcome_is_not_retried(database: Database, settings: Settings) -> None:
    run_id = await seed(database)
    async with database.session() as session:
        repository = RunRepository(session)
        run = await repository.get(run_id, for_update=True)
        run.runtime_state = {"phase": "patch_created", "approved_invocation_id": "pending"}
        run = await repository.claim_next("dead", 30)
        assert run
        invocation, _ = await InvocationRepository(session).begin(
            run_id=run_id,
            spec=ToolSpec(
                "apply_patch",
                "1.0",
                "write",
                {"type": "object"},
                RiskLevel.HIGH,
                True,
            ),
            arguments={},
            idempotency_key=f"{run_id}:apply_patch:apply_patch",
        )
        await InvocationRepository(session).mark_running(invocation)
        run.runtime_state = {
            **run.runtime_state,
            "approved_invocation_id": invocation.id,
        }
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    worker = Worker(database, settings)
    worker.runtime.sandbox = FastSandbox()
    await worker.run_once()
    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
        invocation = await session.scalar(
            select(ToolInvocationModel).where(ToolInvocationModel.run_id == run_id)
        )
        assert run.status == RunStatus.FAILED
        assert run.terminal_reason == "unknown_external_outcome"
        assert run.runtime_state["plan"]["status"] == "failed"
        approval_task = next(
            task for task in run.runtime_state["plan"]["tasks"] if task["id"] == "approval"
        )
        assert approval_task["status"] == "unknown_outcome"
        assert invocation and invocation.status == InvocationStatus.UNKNOWN_OUTCOME


@pytest.mark.asyncio
@pytest.mark.fault
@pytest.mark.parametrize("attempt", range(10))
async def test_kill_recover_matrix(database: Database, settings: Settings, attempt: int) -> None:
    run_id = await seed(database)
    worker = Worker(database, settings)
    worker.runtime.sandbox = FastSandbox()
    await worker.run_once()
    async with database.session() as session:
        run = await RunRepository(session).get(run_id, for_update=True)
        run.status = RunStatus.RUNNING
        run.lease_owner = f"killed-{attempt}"
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    for _ in range(20):
        await worker.run_once()
        async with database.session() as session:
            approval = await ApprovalRepository(session).pending_for_run(run_id)
            if approval:
                await ApprovalRepository(session).decide(
                    approval.id, ApprovalDecision.APPROVE, approval.version, "fault test"
                )
                await session.commit()
            run = await RunRepository(session).get(run_id)
            if RunStatus(run.status).terminal:
                assert run.status == RunStatus.SUCCEEDED
                return
    pytest.fail("recovered run did not terminate")
