from __future__ import annotations

from pathlib import Path

import pytest

from aegisrun.artifacts.local import LocalArtifactBackend
from aegisrun.core.domain import BudgetSnapshot, PolicySnapshot
from aegisrun.core.errors import NotFoundError
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import RunRepository


@pytest.mark.asyncio
async def test_artifact_path_requires_existing_file(database: Database, tmp_path: Path) -> None:
    backend = LocalArtifactBackend(tmp_path / "artifacts")
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        artifact = await backend.put(
            session,
            run_id=run.id,
            artifact_type="text",
            content_type="text/plain",
            filename="value.txt",
            content=b"value",
        )
        await session.commit()
    path = backend.path_for(artifact)
    path.unlink()
    path.mkdir()
    with pytest.raises(NotFoundError):
        backend.path_for(artifact)
