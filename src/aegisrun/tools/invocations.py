from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisrun.core.domain import InvocationStatus
from aegisrun.core.security import canonical_hash
from aegisrun.persistence.models import ToolInvocationModel
from aegisrun.tools.spec import ToolSpec


class InvocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def begin(
        self,
        *,
        run_id: str,
        spec: ToolSpec,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[ToolInvocationModel, bool]:
        existing = await self.session.scalar(
            select(ToolInvocationModel).where(
                ToolInvocationModel.idempotency_key == idempotency_key
            )
        )
        if existing:
            return existing, False
        invocation = ToolInvocationModel(
            id=str(uuid4()),
            run_id=run_id,
            tool_name=spec.name,
            tool_version=spec.version,
            arguments_hash=canonical_hash(arguments),
            idempotency_key=idempotency_key,
            risk_level=spec.risk,
            side_effect=spec.side_effect,
            status=InvocationStatus.PENDING,
        )
        self.session.add(invocation)
        await self.session.flush()
        return invocation, True

    async def get(self, invocation_id: str) -> ToolInvocationModel:
        invocation = await self.session.scalar(
            select(ToolInvocationModel).where(ToolInvocationModel.id == invocation_id)
        )
        if not invocation:
            raise ValueError(f"tool invocation not found: {invocation_id}")
        return invocation

    async def mark_running(self, invocation: ToolInvocationModel) -> None:
        invocation.status = InvocationStatus.RUNNING

    async def complete(
        self,
        invocation: ToolInvocationModel,
        result: dict[str, Any],
        *,
        artifact_id: str | None = None,
        external_reference: str | None = None,
    ) -> None:
        invocation.status = InvocationStatus.SUCCEEDED
        invocation.result_json = result
        invocation.result_artifact_id = artifact_id
        invocation.external_reference = external_reference

    async def mark_unknown(self, invocation: ToolInvocationModel) -> None:
        invocation.status = InvocationStatus.UNKNOWN_OUTCOME

    async def fail(self, invocation: ToolInvocationModel, message: str) -> None:
        invocation.status = InvocationStatus.FAILED
        invocation.result_json = {"error": message}
