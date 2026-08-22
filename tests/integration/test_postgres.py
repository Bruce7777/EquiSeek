from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import or_, select

from aegisrun.config import Settings
from aegisrun.core.domain import BudgetSnapshot, PolicySnapshot
from aegisrun.persistence.database import Database
from aegisrun.persistence.models import RunModel
from aegisrun.persistence.repository import RunRepository
from aegisrun.runtime.checkpoints import CheckpointCoordinator

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_claim_and_checkpoint() -> None:
    database_url = os.getenv("EQUISEEK_TEST_POSTGRES_URL")
    checkpoint_url = os.getenv("EQUISEEK_TEST_CHECKPOINT_URL")
    if not database_url or not checkpoint_url:
        pytest.skip("PostgreSQL test URLs are not configured")
    database = Database(Settings(database_url=database_url))
    await database.create_schema()
    key = f"postgres-{uuid4()}"
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={"postgres": True},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=key,
        )
        run_id = run.id
        await session.commit()
    async with database.session() as session:
        now = datetime.now(UTC)
        expected = await session.scalar(
            select(RunModel)
            .where(
                or_(
                    RunModel.status == "queued",
                    (
                        (RunModel.status == "running")
                        & RunModel.recoverable.is_(True)
                        & (RunModel.lease_expires_at < now)
                    ),
                )
            )
            .order_by(RunModel.created_at)
            .limit(1)
        )
        assert expected is not None
        claimed = await RunRepository(session).claim_next("postgres-worker", 30)
        assert claimed and claimed.id == expected.id
        thread_id = claimed.thread_id
        await session.commit()
    async with database.session() as session:
        assert (await RunRepository(session).get(run_id)).id == run_id
    checkpoints = CheckpointCoordinator(checkpoint_url)
    await checkpoints.record(thread_id, "claimed", 1)
    latest = await checkpoints.latest(thread_id)
    assert latest and latest["phase"] == "claimed"
    await database.dispose()
