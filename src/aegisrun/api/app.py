from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select

from aegisrun.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalView,
    ArtifactView,
    CreateRunRequest,
    ErrorResponse,
    EventHistory,
    EventView,
    RunView,
    WorkspaceView,
)
from aegisrun.artifacts.local import LocalArtifactBackend
from aegisrun.config import Settings, get_settings
from aegisrun.core.domain import (
    ApprovalDecision,
    BudgetSnapshot,
    PolicySnapshot,
    RunStatus,
    TerminalReason,
)
from aegisrun.core.errors import AegisRunError, ConflictError, NotFoundError
from aegisrun.orchestration.models import ExecutionPlan, issue_triage_plan
from aegisrun.persistence.database import Database
from aegisrun.persistence.models import ArtifactModel, RunModel
from aegisrun.persistence.repository import ApprovalRepository, RunRepository
from aegisrun.sandbox.factory import sandbox_capabilities
from aegisrun.workspace.manager import WorkspaceManager


def event_view(event: Any) -> EventView:
    return EventView(
        seq=event.seq,
        event_type=event.event_type,
        phase=event.phase,
        payload=event.payload_public,
        created_at=event.created_at,
    )


def run_view(run: RunModel, approval: Any | None = None) -> RunView:
    approval_view = None
    if approval:
        approval_view = ApprovalView(
            id=approval.id,
            tool_name=approval.tool_name,
            arguments=approval.arguments_json,
            status=approval.status,
            version=approval.version,
        )
    return RunView(
        id=run.id,
        thread_id=run.thread_id,
        agent_name=run.agent_name,
        status=RunStatus(run.status),
        input=run.input_json,
        policy=run.policy_snapshot,
        budget=run.budget_snapshot,
        usage=run.budget_usage,
        terminal_reason=run.terminal_reason,
        version=run.version,
        created_at=run.created_at,
        updated_at=run.updated_at,
        approval=approval_view,
        plan=dict(run.runtime_state.get("plan") or issue_triage_plan(run.id).to_dict()),
    )


def database_from_request(request: Request) -> Database:
    return cast(Database, request.app.state.database)


DatabaseDependency = Annotated[Database, Depends(database_from_request)]


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app_database = database or Database(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if app_settings.auto_create_schema:
            await app_database.create_schema()
        yield
        if database is None:
            await app_database.dispose()

    app = FastAPI(
        title="EquiSeek",
        version="0.2.0",
        description="Recoverable and policy-aware Agent Harness reference implementation.",
        lifespan=lifespan,
    )
    app.state.database = app_database
    app.state.settings = app_settings

    @app.exception_handler(AegisRunError)
    async def aegisrun_error_handler(_: Request, error: AegisRunError) -> Any:
        http_status = (
            status.HTTP_404_NOT_FOUND
            if isinstance(error, NotFoundError)
            else status.HTTP_409_CONFLICT
        )
        if not isinstance(error, (NotFoundError, ConflictError)):
            http_status = status.HTTP_400_BAD_REQUEST
        return JSONResponse(
            status_code=http_status,
            content=ErrorResponse(code=error.code, message=str(error)).model_dump(),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "harness": {
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
            },
            "planning": {"durable": True, "dependency_dag": True, "recoverable": True},
            "subtasks": {
                "isolated_workspaces": True,
                "max_concurrency": app_settings.subtask_max_concurrency,
            },
            "workspace": {
                "layout_version": 1,
                "physical_path_exposed": False,
                "max_bytes_per_run": app_settings.workspace_max_bytes_per_run,
            },
            "sandbox": sandbox_capabilities(app_settings),
        }

    @app.post("/api/runs", response_model=RunView, status_code=status.HTTP_201_CREATED)
    async def create_run(
        body: CreateRunRequest,
        database_: DatabaseDependency,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> RunView:
        async with database_.session() as session:
            repository = RunRepository(session)
            run, created = await repository.create(
                agent_name=body.agent_name,
                input_json=body.input,
                policy=PolicySnapshot(),
                budget=BudgetSnapshot(),
                idempotency_key=idempotency_key,
            )
            await session.commit()
            if not created:
                # Stable identity is the idempotency contract; the view is otherwise unchanged.
                await session.refresh(run)
            return run_view(run)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, database_: DatabaseDependency) -> RunView:
        async with database_.session() as session:
            repository = RunRepository(session)
            run = await repository.get(run_id)
            approval = await ApprovalRepository(session).pending_for_run(run_id)
            return run_view(run, approval)

    @app.get("/api/runs/{run_id}/plan")
    async def get_run_plan(run_id: str, database_: DatabaseDependency) -> dict[str, Any]:
        async with database_.session() as session:
            run = await RunRepository(session).get(run_id)
            return dict(run.runtime_state.get("plan") or issue_triage_plan(run.id).to_dict())

    @app.get("/api/runs/{run_id}/workspace", response_model=WorkspaceView)
    async def get_run_workspace(run_id: str, database_: DatabaseDependency) -> WorkspaceView:
        async with database_.session() as session:
            await RunRepository(session).get(run_id)
        manager = WorkspaceManager(
            app_settings.workspace_root,
            max_bytes_per_run=app_settings.workspace_max_bytes_per_run,
        )
        description = manager.describe(run_id)
        description["root"] = f"workspace://{run_id}"
        return WorkspaceView.model_validate(description)

    @app.get("/api/runs/{run_id}/events/history", response_model=EventHistory)
    async def event_history(
        run_id: str,
        database_: DatabaseDependency,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> EventHistory:
        async with database_.session() as session:
            events = await RunRepository(session).list_events(run_id, after_seq, limit)
            items = [event_view(event) for event in events]
            return EventHistory(
                items=items,
                next_after_seq=items[-1].seq if len(items) == limit else None,
            )

    @app.get("/api/runs/{run_id}/events")
    async def event_stream(
        run_id: str,
        request: Request,
        database_: DatabaseDependency,
        after_seq: int = Query(default=0, ge=0),
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        cursor = max(after_seq, int(last_event_id or 0))

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                async with database_.session() as session:
                    repository = RunRepository(session)
                    run = await repository.get(run_id)
                    events = await repository.list_events(run_id, cursor, 200)
                    for event in events:
                        cursor = event.seq
                        payload = event_view(event).model_dump(mode="json")
                        data = json.dumps(payload, separators=(",", ":"))
                        yield f"id: {event.seq}\nevent: {event.event_type}\ndata: {data}\n\n"
                    terminal = RunStatus(run.status).terminal
                if terminal and not events:
                    return
                idle += 1
                if idle % 20 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/approvals/{approval_id}/decision", response_model=RunView)
    async def decide_approval(
        approval_id: str,
        body: ApprovalDecisionRequest,
        database_: DatabaseDependency,
    ) -> RunView:
        async with database_.session() as session:
            approvals = ApprovalRepository(session)
            approval = await approvals.decide(
                approval_id,
                ApprovalDecision(body.decision),
                body.expected_version,
                body.reason,
            )
            run = await approvals.runs.get(approval.run_id)
            await session.commit()
            return run_view(run)

    @app.post("/api/runs/{run_id}/cancel", response_model=RunView)
    async def cancel_run(run_id: str, database_: DatabaseDependency) -> RunView:
        async with database_.session() as session:
            repository = RunRepository(session)
            run = await repository.get(run_id, for_update=True)
            if not RunStatus(run.status).terminal:
                raw_plan = run.runtime_state.get("plan")
                if isinstance(raw_plan, dict):
                    plan = ExecutionPlan.from_dict(raw_plan)
                    plan.cancel()
                    run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                await repository.transition(
                    run,
                    RunStatus.CANCELLED,
                    reason=TerminalReason.USER_CANCELLED,
                    event_type="run.cancelled",
                )
                await session.commit()
            return run_view(run)

    @app.get("/api/artifacts/{artifact_id}", response_model=ArtifactView)
    async def get_artifact(
        artifact_id: str,
        database_: DatabaseDependency,
    ) -> ArtifactView:
        async with database_.session() as session:
            artifact = await session.scalar(
                select(ArtifactModel).where(ArtifactModel.id == artifact_id)
            )
            if not artifact:
                raise NotFoundError(f"artifact not found: {artifact_id}")
            return ArtifactView.model_validate(artifact)

    @app.get("/api/artifacts/{artifact_id}/content")
    async def artifact_content(
        artifact_id: str,
        database_: DatabaseDependency,
    ) -> FileResponse:
        async with database_.session() as session:
            artifact = await session.scalar(
                select(ArtifactModel).where(ArtifactModel.id == artifact_id)
            )
            if not artifact:
                raise NotFoundError(f"artifact not found: {artifact_id}")
            backend = LocalArtifactBackend(app_settings.artifact_root)
            return FileResponse(
                backend.path_for(artifact),
                media_type=artifact.content_type,
                filename=artifact.relative_path.rsplit("/", maxsplit=1)[-1],
            )

    return app


app = create_app()
