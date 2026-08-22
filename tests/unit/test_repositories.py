from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegisrun.core.domain import (
    ApprovalDecision,
    BudgetSnapshot,
    PolicySnapshot,
    RunStatus,
)
from aegisrun.core.errors import ConflictError
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import ApprovalRepository, RunRepository


async def create_run(database: Database, key: str | None = None) -> str:
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={"issue": "x"},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=key,
        )
        run_id = run.id
        await session.commit()
        return run_id


@pytest.mark.asyncio
async def test_create_run_is_idempotent(database: Database) -> None:
    async with database.session() as session:
        repository = RunRepository(session)
        first, first_created = await repository.create(
            agent_name="issue_triage",
            input_json={"issue": "x"},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key="same",
        )
        await session.commit()
    async with database.session() as session:
        second, second_created = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={"issue": "x"},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key="same",
        )
        assert first.id == second.id
        assert first_created is True
        assert second_created is False


@pytest.mark.asyncio
async def test_idempotency_conflict(database: Database) -> None:
    await create_run(database, "same")
    async with database.session() as session:
        with pytest.raises(ConflictError):
            await RunRepository(session).create(
                agent_name="issue_triage",
                input_json={"issue": "different"},
                policy=PolicySnapshot(),
                budget=BudgetSnapshot(),
                idempotency_key="same",
            )


@pytest.mark.asyncio
async def test_claim_assigns_lease(database: Database) -> None:
    run_id = await create_run(database)
    async with database.session() as session:
        run = await RunRepository(session).claim_next("worker-a", 30)
        assert run and run.id == run_id
        assert run.status == RunStatus.RUNNING
        assert run.lease_owner == "worker-a"


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed(database: Database) -> None:
    await create_run(database)
    async with database.session() as session:
        run = await RunRepository(session).claim_next("dead-worker", 30)
        assert run
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    async with database.session() as session:
        run = await RunRepository(session).claim_next("replacement", 30)
        assert run and run.lease_owner == "replacement"
        events = await RunRepository(session).list_events(run.id)
        assert "lease.expired" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_heartbeat_requires_owner(database: Database) -> None:
    run_id = await create_run(database)
    async with database.session() as session:
        await RunRepository(session).claim_next("owner", 30)
        await session.commit()
    async with database.session() as session:
        with pytest.raises(ConflictError):
            await RunRepository(session).heartbeat(run_id, "intruder", 30)


@pytest.mark.asyncio
async def test_event_sequence_is_monotonic(database: Database) -> None:
    run_id = await create_run(database)
    async with database.session() as session:
        repository = RunRepository(session)
        run = await repository.get(run_id, for_update=True)
        await repository.append_event(run, "one", "completed")
        await repository.append_event(run, "two", "completed")
        await session.commit()
    async with database.session() as session:
        events = await RunRepository(session).list_events(run_id)
        assert [event.seq for event in events] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_approval_decision_is_single_use(database: Database) -> None:
    await create_run(database)
    async with database.session() as session:
        repository = RunRepository(session)
        run = await repository.claim_next("owner", 30)
        assert run
        approval = await ApprovalRepository(session).create(
            run=run,
            invocation_id="invocation-1",
            tool_name="apply_patch",
            arguments={},
        )
        approval_id = approval.id
        version = approval.version
        await session.commit()
    async with database.session() as session:
        await ApprovalRepository(session).decide(
            approval_id, ApprovalDecision.APPROVE, version, "looks good"
        )
        await session.commit()
    async with database.session() as session:
        with pytest.raises(ConflictError):
            await ApprovalRepository(session).decide(
                approval_id, ApprovalDecision.APPROVE, version, None
            )


@pytest.mark.asyncio
async def test_approval_rejection_cancels_run(database: Database) -> None:
    run_id = await create_run(database)
    async with database.session() as session:
        run = await RunRepository(session).claim_next("owner", 30)
        assert run
        approval = await ApprovalRepository(session).create(
            run=run,
            invocation_id="invocation-1",
            tool_name="apply_patch",
            arguments={},
        )
        approval_id = approval.id
        version = approval.version
        await session.commit()
    async with database.session() as session:
        await ApprovalRepository(session).decide(
            approval_id, ApprovalDecision.REJECT, version, "unsafe"
        )
        run = await RunRepository(session).get(run_id)
        assert run.status == RunStatus.CANCELLED
        assert run.terminal_reason == "approval_rejected"
