from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from aegisrun.config import Settings
from aegisrun.core.domain import ApprovalDecision, BudgetSnapshot, PolicySnapshot, RunStatus
from aegisrun.persistence.database import Database
from aegisrun.persistence.models import ArtifactModel, ToolInvocationModel
from aegisrun.persistence.repository import ApprovalRepository, RunRepository
from aegisrun.runtime.worker import Worker


async def seed(database: Database) -> str:
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={"issue": "ISSUE.md"},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        run_id = run.id
        await session.commit()
        return run_id


@pytest.mark.asyncio
async def test_golden_runtime_requires_approval_then_succeeds(
    database: Database, settings: Settings
) -> None:
    run_id = await seed(database)
    worker = Worker(database, settings)
    for _ in range(10):
        await worker.run_once()
        async with database.session() as session:
            run = await RunRepository(session).get(run_id)
            approval = await ApprovalRepository(session).pending_for_run(run_id)
            if approval:
                workspace_file = settings.workspace_root / run_id / "shared" / "calculator.py"
                assert "return total / count" in workspace_file.read_text()
                await ApprovalRepository(session).decide(
                    approval.id,
                    ApprovalDecision.APPROVE,
                    approval.version,
                    "test",
                )
                await session.commit()
            if RunStatus(run.status).terminal:
                break
    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
        assert run.status == RunStatus.SUCCEEDED
        events = await RunRepository(session).list_events(run_id, 0, 200)
        event_types = [event.event_type for event in events]
        assert event_types.index("approval.approved") < event_types.index("run.succeeded")
        invocations = await session.scalar(select(func.count()).select_from(ToolInvocationModel))
        artifacts = await session.scalar(select(func.count()).select_from(ArtifactModel))
        assert invocations == 5
        assert artifacts == 4
        plan = run.runtime_state["plan"]
        assert plan["status"] == "succeeded"
        assert all(task["status"] == "succeeded" for task in plan["tasks"])
        task_root = settings.workspace_root / run_id / "tasks"
        assert sorted(path.name for path in task_root.iterdir()) == [
            "approval",
            "baseline",
            "inspect",
            "patch",
            "report",
            "skill",
            "verify",
        ]
        assert (settings.workspace_root / run_id / ".state" / "plan.json").is_file()


@pytest.mark.asyncio
async def test_reject_stops_before_patch(database: Database, settings: Settings) -> None:
    run_id = await seed(database)
    worker = Worker(database, settings)
    for _ in range(6):
        await worker.run_once()
    async with database.session() as session:
        approval = await ApprovalRepository(session).pending_for_run(run_id)
        assert approval
        await ApprovalRepository(session).decide(
            approval.id, ApprovalDecision.REJECT, approval.version, "no"
        )
        await session.commit()
        run = await RunRepository(session).get(run_id)
        assert run.runtime_state["plan"]["status"] == "cancelled"
    assert (
        "return total / count"
        in (settings.workspace_root / run_id / "shared" / "calculator.py").read_text()
    )


@pytest.mark.asyncio
async def test_final_report_artifact_checksum(database: Database, settings: Settings) -> None:
    run_id = await seed(database)
    worker = Worker(database, settings)
    for _ in range(15):
        await worker.run_once()
        async with database.session() as session:
            approval = await ApprovalRepository(session).pending_for_run(run_id)
            if approval:
                await ApprovalRepository(session).decide(
                    approval.id, ApprovalDecision.APPROVE, approval.version, "yes"
                )
                await session.commit()
            run = await RunRepository(session).get(run_id)
            if RunStatus(run.status).terminal:
                break
    async with database.session() as session:
        artifact = await session.scalar(
            select(ArtifactModel).where(ArtifactModel.artifact_type == "markdown_report")
        )
        assert artifact and len(artifact.checksum) == 64
        assert await asyncio.to_thread((settings.artifact_root / artifact.relative_path).exists)
