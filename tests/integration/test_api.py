from __future__ import annotations

import httpx
import pytest

from aegisrun.artifacts.local import LocalArtifactBackend
from aegisrun.config import Settings
from aegisrun.core.domain import ApprovalDecision, BudgetSnapshot, PolicySnapshot, RunStatus
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import ApprovalRepository, RunRepository


@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_get_run(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/runs", json={"input": {"issue": "ISSUE.md"}})
    assert created.status_code == 201
    run_id = created.json()["id"]
    fetched = await client.get(f"/api/runs/{run_id}")
    assert fetched.json()["status"] == "queued"
    assert fetched.json()["plan"]["goal"]
    assert len(fetched.json()["plan"]["tasks"]) == 7


@pytest.mark.asyncio
async def test_plan_and_workspace_endpoints(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/runs", json={"input": {"issue": "ISSUE.md"}})
    run_id = created.json()["id"]

    plan = await client.get(f"/api/runs/{run_id}/plan")
    workspace = await client.get(f"/api/runs/{run_id}/workspace")
    capabilities = await client.get("/api/capabilities")

    assert plan.status_code == 200
    assert plan.json()["status"] == "pending"
    assert workspace.json()["initialized"] is False
    assert workspace.json()["root"] == f"workspace://{run_id}"
    assert capabilities.json()["sandbox"]["backend"] == "local"
    assert capabilities.json()["sandbox"]["security_boundary"] is False
    assert capabilities.json()["workspace"]["physical_path_exposed"] is False
    assert capabilities.json()["harness"] == {
        "event_schema_version": 1,
        "workspace_event_store": True,
        "database_event_store": False,
        "replay_projections": True,
        "runtime_invariants": True,
        "capability_scopes": True,
        "prompt_registry": True,
        "dynamic_context_diff": True,
        "conversation_surface": True,
        "exact_request_envelope": True,
        "profile_generations": True,
        "skill_providers": True,
        "tool_pipeline": True,
        "ordered_safe_tool_batches": True,
        "subagent_modes": ["one-shot", "continuable"],
        "continuable_cold_resume": True,
        "continuable_fifo_followup": True,
        "max_delegation_depth": 1,
    }


@pytest.mark.asyncio
async def test_api_idempotency(client: httpx.AsyncClient) -> None:
    headers = {"Idempotency-Key": "api-key"}
    first = await client.post("/api/runs", json={"input": {}}, headers=headers)
    second = await client.post("/api/runs", json={"input": {}}, headers=headers)
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_api_idempotency_conflict(client: httpx.AsyncClient) -> None:
    headers = {"Idempotency-Key": "api-conflict"}
    await client.post("/api/runs", json={"input": {"x": 1}}, headers=headers)
    response = await client.post("/api/runs", json={"input": {"x": 2}}, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


@pytest.mark.asyncio
async def test_event_history_cursor(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/runs", json={"input": {}})
    run_id = created.json()["id"]
    all_events = await client.get(f"/api/runs/{run_id}/events/history")
    after = await client.get(f"/api/runs/{run_id}/events/history?after_seq=1")
    assert [item["seq"] for item in all_events.json()["items"]] == [1, 2]
    assert [item["seq"] for item in after.json()["items"]] == [2]


@pytest.mark.asyncio
async def test_cancel_is_idempotent(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/runs", json={"input": {}})
    run_id = created.json()["id"]
    first = await client.post(f"/api/runs/{run_id}/cancel")
    second = await client.post(f"/api/runs/{run_id}/cancel")
    assert first.json()["status"] == "cancelled"
    assert second.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_missing_run_returns_structured_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/runs/missing")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_approval_decision_api(client: httpx.AsyncClient, database: Database) -> None:
    async with database.session() as session:
        repository = RunRepository(session)
        run, _ = await repository.create(
            agent_name="issue_triage",
            input_json={},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        await repository.transition(run, RunStatus.RUNNING, event_type="lease.acquired")
        approval = await ApprovalRepository(session).create(
            run=run,
            invocation_id="invocation-api",
            tool_name="apply_patch",
            arguments={"path": "calculator.py"},
        )
        approval_id = approval.id
        version = approval.version
        await session.commit()
    response = await client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": ApprovalDecision.APPROVE, "expected_version": version},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_artifact_metadata_and_download(
    client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with database.session() as session:
        run, _ = await RunRepository(session).create(
            agent_name="issue_triage",
            input_json={},
            policy=PolicySnapshot(),
            budget=BudgetSnapshot(),
            idempotency_key=None,
        )
        backend = LocalArtifactBackend(settings.artifact_root)
        artifact = await backend.put(
            session,
            run_id=run.id,
            artifact_type="report",
            content_type="text/plain",
            filename="report.txt",
            content=b"artifact-body",
        )
        artifact_id = artifact.id
        await session.commit()
    metadata = await client.get(f"/api/artifacts/{artifact_id}")
    content = await client.get(f"/api/artifacts/{artifact_id}/content")
    assert metadata.status_code == 200
    assert metadata.json()["size_bytes"] == 13
    assert content.text == "artifact-body"


@pytest.mark.asyncio
async def test_missing_artifact_returns_404(client: httpx.AsyncClient) -> None:
    metadata = await client.get("/api/artifacts/missing")
    content = await client.get("/api/artifacts/missing/content")
    assert metadata.status_code == 404
    assert content.status_code == 404
