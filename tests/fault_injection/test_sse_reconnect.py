from __future__ import annotations

import pytest

from aegisrun.core.domain import BudgetSnapshot, PolicySnapshot
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import RunRepository


@pytest.mark.asyncio
@pytest.mark.fault
@pytest.mark.parametrize("reconnect", range(10))
async def test_sse_cursor_reconnect_has_no_gap(database: Database, reconnect: int) -> None:
    async with database.session() as session:
        repository = RunRepository(session)
        run, _ = await repository.create(
            agent_name="issue_triage",
            input_json={"reconnect": reconnect},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        for index in range(8):
            await repository.append_event(run, f"event.{index}", "completed")
        run_id = run.id
        await session.commit()
    cursor = reconnect % 6
    async with database.session() as session:
        replay = await RunRepository(session).list_events(run_id, cursor, 100)
        assert [event.seq for event in replay] == list(range(cursor + 1, 11))
