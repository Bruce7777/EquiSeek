from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aegisrun.artifacts.local import LocalArtifactBackend
from aegisrun.config import Settings
from aegisrun.core.domain import (
    BudgetSnapshot,
    BudgetUsage,
    InvocationStatus,
    PolicySnapshot,
    RunStatus,
    TerminalReason,
)
from aegisrun.core.errors import BudgetExceededError
from aegisrun.core.security import authorize_relative_path, canonical_hash, safe_join
from aegisrun.orchestration.models import ExecutionPlan, TaskStatus, issue_triage_plan
from aegisrun.persistence.database import Database
from aegisrun.persistence.models import RunModel
from aegisrun.persistence.repository import ApprovalRepository, RunRepository
from aegisrun.runtime.checkpoints import CheckpointCoordinator
from aegisrun.runtime.fake_model import FakeIssueTriageModel
from aegisrun.runtime.skills import SkillLoader
from aegisrun.sandbox.base import SandboxPolicy, SandboxProvider
from aegisrun.sandbox.factory import create_sandbox
from aegisrun.tools.budget import BudgetManager
from aegisrun.tools.invocations import InvocationRepository
from aegisrun.tools.pipeline import ToolExecutionContext, ToolPipeline
from aegisrun.tools.registry import ToolRegistry
from aegisrun.tools.spec import RiskLevel, ToolResult, ToolSpec
from aegisrun.workspace.manager import WorkspaceManager

PATCH_CONTENT = """def divide(total: float, count: float) -> float:
    if count == 0:
        return 0.0
    return total / count
"""


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    run_id: str
    invocation_id: str
    tool_name: str
    arguments: dict[str, Any]
    phase: str
    policy: PolicySnapshot


class IssueTriageRuntime:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.model = FakeIssueTriageModel()
        self.artifacts = LocalArtifactBackend(settings.artifact_root)
        self.checkpoints = CheckpointCoordinator(settings.effective_checkpoint_url)
        self.sandbox: SandboxProvider = create_sandbox(settings)
        self.workspaces = WorkspaceManager(
            settings.workspace_root, max_bytes_per_run=settings.workspace_max_bytes_per_run
        )

    def workspace_for(self, run_id: str) -> Path:
        return self.workspaces.paths(run_id).shared

    def ensure_workspace(self, run_id: str) -> Path:
        workspace = self.workspace_for(run_id)
        if workspace.exists() and any(workspace.iterdir()):
            return workspace
        fixture = (
            Path(__file__).parents[3]
            / "examples"
            / "issue_triage"
            / "fixtures"
            / "broken_repository"
        )
        if not fixture.exists():
            fixture = Path.cwd() / "examples/issue_triage/fixtures/broken_repository"
        return self.workspaces.create_run(run_id, template=fixture).shared

    def _plan(self, run: RunModel) -> ExecutionPlan:
        raw = run.runtime_state.get("plan")
        return ExecutionPlan.from_dict(raw) if isinstance(raw, dict) else issue_triage_plan(run.id)

    def _record_plan_action(
        self, run: RunModel, plan: ExecutionPlan, action_name: str, *, completed: bool
    ) -> None:
        mapping = {
            "load_skill": "skill",
            "run_tests": "baseline",
            "read_source": "inspect",
            "create_patch": "patch",
            "apply_patch": "approval",
            "verify": "verify",
            "finish": "report",
        }
        task_id = mapping.get(action_name)
        if task_id is None:
            return
        record = plan.tasks[task_id]
        if completed:
            if record.status is TaskStatus.RUNNING:
                result = {"action": action_name}
                plan.succeed_task(task_id, result=result, summary="阶段完成")
                workspace = self.workspaces.create_task(run.id, task_id)
                self.workspaces.write_json(
                    workspace.output / "result.json",
                    {"summary": "阶段完成", "data": result},
                )
        else:
            if record.status is TaskStatus.WAITING_APPROVAL:
                if not record.approved:
                    return
                plan.resume_task(task_id)
            if record.status is TaskStatus.PENDING and task_id in plan.ready_task_ids():
                if record.spec.approval_required and not record.approved:
                    return
                plan.start_task(task_id)
                workspace = self.workspaces.create_task(run.id, task_id)
                self.workspaces.write_json(
                    workspace.input / "context.json",
                    {"action": action_name, "dependencies": list(record.depends_on)},
                )
        run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
        self.workspaces.plan_store(run.id).save(plan)

    async def execute_claimed(self, run_id: str, worker_id: str) -> None:
        prepared: PreparedToolCall | None = None
        async with self.database.session() as session:
            run_repo = RunRepository(session)
            run = await run_repo.get(run_id, for_update=True)
            if run.status != RunStatus.RUNNING or run.lease_owner != worker_id:
                return
            try:
                prepared = await self._advance(session, run)
            except BudgetExceededError as error:
                plan = self._plan(run)
                plan.abort(str(error))
                run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                self.workspaces.plan_store(run.id).save(plan)
                await run_repo.transition(
                    run,
                    RunStatus.FAILED,
                    reason=TerminalReason.BUDGET_EXHAUSTED,
                    event_type="budget.exhausted",
                    payload={"message": str(error)},
                )
            except Exception as error:
                plan = self._plan(run)
                plan.abort(f"{type(error).__name__}: {error}")
                run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                self.workspaces.plan_store(run.id).save(plan)
                await run_repo.transition(
                    run,
                    RunStatus.FAILED,
                    reason=TerminalReason.INTERNAL_ERROR,
                    event_type="run.failed",
                    payload={"error_type": type(error).__name__, "message": str(error)},
                )
            await session.commit()

        if prepared:
            await self._execute_prepared(prepared, worker_id)

        async with self.database.session() as session:
            run = await RunRepository(session).get(run_id)
            phase = str(run.runtime_state.get("phase", "created"))
            steps = int(run.budget_usage.get("model_turns", 0))
            thread_id = run.thread_id
        # PostgresSaver.setup uses CREATE INDEX CONCURRENTLY. Never keep a product
        # database transaction open while initializing or writing a checkpoint.
        await self.checkpoints.record(thread_id, phase, steps)

    async def _advance(self, session: Any, run: RunModel) -> PreparedToolCall | None:
        run_repo = RunRepository(session)
        policy = PolicySnapshot(
            allowed_tools=tuple(run.policy_snapshot["allowed_tools"]),
            approval_required=tuple(run.policy_snapshot["approval_required"]),
            readable_prefixes=tuple(run.policy_snapshot["readable_prefixes"]),
            writable_prefixes=tuple(run.policy_snapshot["writable_prefixes"]),
            network_allowed=bool(run.policy_snapshot["network_allowed"]),
        )
        budget = BudgetManager(
            BudgetSnapshot(**run.budget_snapshot),
            BudgetUsage(
                **{
                    **run.budget_usage,
                    "repeated_actions": run.budget_usage.get("repeated_actions", {}),
                }
            ),
        )
        phase = str(run.runtime_state.get("phase", "created"))
        created_at = run.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        budget.check_wall_time((datetime.now(UTC) - created_at).total_seconds())
        action = self.model.next_action(phase)
        plan = self._plan(run)
        self._record_plan_action(run, plan, action.name, completed=False)
        budget.consume_model_turn()
        await run_repo.append_event(run, "model.action", "completed", {"action": action.name})
        workspace = self.ensure_workspace(run.id)
        registry = self._build_registry(session, run.id, workspace, policy)

        if action.name == "load_skill":
            skill_root = Path.cwd() / "examples/issue_triage/skills"
            skills = SkillLoader(skill_root).discover()
            await run_repo.append_event(
                run,
                "skill.loaded",
                "completed",
                {
                    "skills": [item.name for item in skills],
                    "checksums": [item.checksum for item in skills],
                },
            )
            run.runtime_state = {**run.runtime_state, "phase": "skill_loaded"}
            self._record_plan_action(run, plan, action.name, completed=True)
        elif action.name == "finish":
            report = self._final_report(run)
            artifact = await self.artifacts.put(
                session,
                run_id=run.id,
                artifact_type="markdown_report",
                content_type="text/markdown",
                filename="final-report.md",
                content=report.encode(),
            )
            await run_repo.append_event(
                run,
                "artifact.created",
                "completed",
                {"artifact_id": artifact.id, "type": artifact.artifact_type},
            )
            await run_repo.transition(
                run,
                RunStatus.SUCCEEDED,
                reason=TerminalReason.COMPLETED,
                event_type="run.succeeded",
            )
            self._record_plan_action(run, plan, action.name, completed=True)
        elif action.tool_name:
            spec = registry.get(action.tool_name).spec
            arguments = action.arguments or {}
            action_key = f"{action.tool_name}:{canonical_hash(arguments)}"
            invocation_key = f"{run.id}:{action.name}:{action.tool_name}"
            invocations = InvocationRepository(session)
            invocation, created = await invocations.begin(
                run_id=run.id,
                spec=spec,
                arguments=arguments,
                idempotency_key=invocation_key,
            )
            approved_id = run.runtime_state.get("approved_invocation_id")
            if action.tool_name in policy.approval_required and approved_id != invocation.id:
                approvals = ApprovalRepository(session)
                approval = await approvals.create(
                    run=run,
                    invocation_id=invocation.id,
                    tool_name=action.tool_name,
                    arguments=arguments,
                )
                run.runtime_state = {
                    **run.runtime_state,
                    "phase": phase,
                    "pending_approval_id": approval.id,
                }
                plan.request_approval("approval")
                approval_workspace = self.workspaces.create_task(run.id, "approval")
                self.workspaces.write_json(
                    approval_workspace.input / "context.json",
                    {
                        "tool": action.tool_name,
                        "invocation_id": invocation.id,
                        "arguments_hash": canonical_hash(arguments),
                    },
                )
                run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                self.workspaces.plan_store(run.id).save(plan)
                run.budget_usage = budget.usage.to_dict()
                return None
            budget.consume_tool_call(action_key)
            if not created and invocation.status == "succeeded":
                result = ToolResult(
                    summary=str((invocation.result_json or {}).get("summary", "cached")),
                    data=invocation.result_json or {},
                    artifact_id=invocation.result_artifact_id,
                )
            elif not created and invocation.status in {
                InvocationStatus.RUNNING,
                InvocationStatus.UNKNOWN_OUTCOME,
            }:
                if spec.side_effect:
                    await invocations.mark_unknown(invocation)
                    plan.mark_unknown_outcome(
                        "approval", "interrupted side effect has unknown external outcome"
                    )
                    run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                    self.workspaces.plan_store(run.id).save(plan)
                    await run_repo.transition(
                        run,
                        RunStatus.FAILED,
                        reason=TerminalReason.UNKNOWN_EXTERNAL_OUTCOME,
                        event_type="tool.unknown_outcome",
                        payload={"tool": action.tool_name, "invocation_id": invocation.id},
                    )
                    run.budget_usage = budget.usage.to_dict()
                    return None
                invocation.attempt += 1
                await invocations.mark_running(invocation)
                await run_repo.append_event(
                    run,
                    "tool.retried",
                    "started",
                    {
                        "tool": action.tool_name,
                        "invocation_id": invocation.id,
                        "attempt": invocation.attempt,
                    },
                )
                run.budget_usage = budget.usage.to_dict()
                return PreparedToolCall(
                    run_id=run.id,
                    invocation_id=invocation.id,
                    tool_name=action.tool_name,
                    arguments=arguments,
                    phase=phase,
                    policy=policy,
                )
            else:
                await invocations.mark_running(invocation)
                await run_repo.append_event(
                    run,
                    "tool.started",
                    "started",
                    {"tool": action.tool_name, "invocation_id": invocation.id},
                )
                run.budget_usage = budget.usage.to_dict()
                return PreparedToolCall(
                    run_id=run.id,
                    invocation_id=invocation.id,
                    tool_name=action.tool_name,
                    arguments=arguments,
                    phase=phase,
                    policy=policy,
                )
            await run_repo.append_event(
                run,
                "tool.completed",
                "completed",
                {
                    "tool": action.tool_name,
                    "invocation_id": invocation.id,
                    "summary": result.summary,
                    "artifact_id": result.artifact_id,
                    "cached": not created,
                },
            )
            next_phase = {
                "run_tests": "verified" if phase == "patch_applied" else "tests_failed",
                "read_file": "source_read",
                "create_patch": "patch_created",
                "apply_patch": "patch_applied",
            }[action.tool_name]
            run.runtime_state = {
                **run.runtime_state,
                "phase": next_phase,
                "last_artifact_id": result.artifact_id,
            }
            self._record_plan_action(run, plan, action.name, completed=True)
        run.budget_usage = budget.usage.to_dict()
        if run.status == RunStatus.RUNNING:
            await run_repo.transition(run, RunStatus.QUEUED, event_type="run.step_completed")
        return None

    async def _execute_prepared(self, prepared: PreparedToolCall, worker_id: str) -> None:
        try:
            async with self.database.session() as tool_session:
                workspace = self.ensure_workspace(prepared.run_id)
                registry = self._build_registry(
                    tool_session, prepared.run_id, workspace, prepared.policy
                )

                async def already_approved(_: ToolExecutionContext, __: str) -> bool:
                    # _advance persists approval before a prepared side effect can reach here.
                    return True

                result = await ToolPipeline(
                    registry,
                    approval_hook=already_approved,
                ).execute(
                    prepared.tool_name,
                    prepared.arguments,
                    prepared.policy,
                    agent_id="issue-triage-agent",
                )
                await tool_session.commit()
        except Exception as error:
            await self._fail_prepared(prepared, worker_id, error)
            return

        async with self.database.session() as session:
            run_repo = RunRepository(session)
            run = await run_repo.get(prepared.run_id, for_update=True)
            if run.status != RunStatus.RUNNING or run.lease_owner != worker_id:
                return
            invocations = InvocationRepository(session)
            invocation = await invocations.get(prepared.invocation_id)
            await invocations.complete(
                invocation,
                {"summary": result.summary, **result.data},
                artifact_id=result.artifact_id,
                external_reference=result.external_reference,
            )
            budget = BudgetManager(
                BudgetSnapshot(**run.budget_snapshot),
                BudgetUsage(**run.budget_usage),
            )
            output_bytes = len(json.dumps(result.data, ensure_ascii=False).encode())
            budget.consume_output(output_bytes)
            await run_repo.append_event(
                run,
                "tool.completed",
                "completed",
                {
                    "tool": prepared.tool_name,
                    "invocation_id": invocation.id,
                    "summary": result.summary,
                    "artifact_id": result.artifact_id,
                    "cached": False,
                },
            )
            next_phase = {
                "run_tests": "verified" if prepared.phase == "patch_applied" else "tests_failed",
                "read_file": "source_read",
                "create_patch": "patch_created",
                "apply_patch": "patch_applied",
            }[prepared.tool_name]
            run.runtime_state = {
                **run.runtime_state,
                "phase": next_phase,
                "last_artifact_id": result.artifact_id,
            }
            plan = self._plan(run)
            self._record_plan_action(
                run,
                plan,
                {
                    "run_tests": "verify" if prepared.phase == "patch_applied" else "run_tests",
                    "read_file": "read_source",
                    "create_patch": "create_patch",
                    "apply_patch": "apply_patch",
                }[prepared.tool_name],
                completed=True,
            )
            run.budget_usage = budget.usage.to_dict()
            await run_repo.transition(run, RunStatus.QUEUED, event_type="run.step_completed")
            await session.commit()

    async def _fail_prepared(
        self,
        prepared: PreparedToolCall,
        worker_id: str,
        error: Exception,
    ) -> None:
        async with self.database.session() as session:
            run_repo = RunRepository(session)
            run = await run_repo.get(prepared.run_id, for_update=True)
            if run.status != RunStatus.RUNNING or run.lease_owner != worker_id:
                return
            invocation = await InvocationRepository(session).get(prepared.invocation_id)
            await InvocationRepository(session).fail(invocation, str(error))
            plan = self._plan(run)
            task_id = {
                "run_tests": "verify" if prepared.phase == "patch_applied" else "baseline",
                "read_file": "inspect",
                "create_patch": "patch",
                "apply_patch": "approval",
            }.get(prepared.tool_name)
            if task_id is not None and plan.tasks[task_id].status is TaskStatus.RUNNING:
                plan.fail_task(task_id, f"{type(error).__name__}: {error}")
                run.runtime_state = {**run.runtime_state, "plan": plan.to_dict()}
                workspace = self.workspaces.create_task(run.id, task_id)
                self.workspaces.write_control_json(
                    workspace.logs / f"attempt-{plan.tasks[task_id].attempts}.json",
                    {"error_type": type(error).__name__, "message": str(error)[:2_000]},
                )
                self.workspaces.plan_store(run.id).save(plan)
            terminal_reason = (
                TerminalReason.SANDBOX_TIMEOUT
                if isinstance(error, TimeoutError)
                else TerminalReason.TOOL_UNRECOVERABLE
            )
            await run_repo.transition(
                run,
                RunStatus.FAILED,
                reason=terminal_reason,
                event_type="tool.failed",
                payload={
                    "tool": prepared.tool_name,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
            await session.commit()

    def _build_registry(
        self,
        session: Any,
        run_id: str,
        workspace: Path,
        policy: PolicySnapshot,
    ) -> ToolRegistry:
        registry = ToolRegistry()

        async def run_tests(_: dict[str, Any]) -> ToolResult:
            result = await self.sandbox.exec(
                workspace,
                ["python", "-m", "pytest", "-q"],
                timeout_seconds=30,
                policy=SandboxPolicy(
                    network_allowed=(
                        policy.network_allowed and self.settings.sandbox_network_allowed
                    ),
                    require_isolation=self.settings.sandbox_require_isolation,
                    max_output_bytes=self.settings.sandbox_max_output_bytes,
                    memory_limit=self.settings.sandbox_memory_limit,
                    cpu_limit=self.settings.sandbox_cpu_limit,
                    pids_limit=self.settings.sandbox_pids_limit,
                ),
            )
            body = json.dumps(
                {
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "output_truncated": result.output_truncated,
                    "enforcement": result.enforcement.value,
                },
                indent=2,
            )
            artifact = await self.artifacts.put(
                session,
                run_id=run_id,
                artifact_type="test_report",
                content_type="application/json",
                filename="test-report.json",
                content=body.encode(),
                creator_tool="run_tests",
            )
            return ToolResult(
                summary=f"pytest exit code {result.exit_code}",
                data={
                    "exit_code": result.exit_code,
                    "sandbox_enforcement": result.enforcement.value,
                },
                artifact_id=artifact.id,
            )

        async def read_file(arguments: dict[str, Any]) -> ToolResult:
            relative_path = str(arguments["path"])
            authorize_relative_path(relative_path, policy.readable_prefixes)
            path = safe_join(workspace, relative_path, must_exist=True)
            text = path.read_text(encoding="utf-8")
            return ToolResult(summary=f"read {path.name}", data={"content": text})

        async def list_files(_: dict[str, Any]) -> ToolResult:
            def collect_files() -> list[str]:
                return [
                    str(path.relative_to(workspace))
                    for path in sorted(workspace.rglob("*"))
                    if path.is_file() and ".pytest_cache" not in path.parts
                ]

            files = await asyncio.to_thread(collect_files)
            return ToolResult(summary=f"listed {len(files)} files", data={"files": files})

        async def search_text(arguments: dict[str, Any]) -> ToolResult:
            query = str(arguments["query"])

            def collect_matches() -> list[dict[str, Any]]:
                matches: list[dict[str, Any]] = []
                for path in sorted(workspace.rglob("*.py")):
                    for line_number, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), start=1
                    ):
                        if query in line:
                            matches.append(
                                {
                                    "path": str(path.relative_to(workspace)),
                                    "line": line_number,
                                    "text": line.strip(),
                                }
                            )
                return matches

            matches = await asyncio.to_thread(collect_matches)
            return ToolResult(
                summary=f"found {len(matches)} matches", data={"matches": matches[:100]}
            )

        async def create_patch(_: dict[str, Any]) -> ToolResult:
            artifact = await self.artifacts.put(
                session,
                run_id=run_id,
                artifact_type="patch",
                content_type="text/x-python",
                filename="calculator.py",
                content=PATCH_CONTENT.encode(),
                creator_tool="create_patch",
            )
            return ToolResult(
                summary="created minimal divide-by-zero patch",
                data={"target": "calculator.py"},
                artifact_id=artifact.id,
            )

        async def apply_patch(_: dict[str, Any]) -> ToolResult:
            authorize_relative_path("calculator.py", policy.writable_prefixes)
            target = safe_join(workspace, "calculator.py", must_exist=True)
            target.write_text(PATCH_CONTENT, encoding="utf-8")
            return ToolResult(summary="patch applied", data={"target": "calculator.py"})

        common_schema = {"type": "object", "additionalProperties": False}
        registry.register(
            ToolSpec(
                "list_files",
                "1.0",
                "List workspace files",
                common_schema,
                RiskLevel.LOW,
                False,
            ),
            list_files,
        )
        registry.register(
            ToolSpec("run_tests", "1.0", "Run pytest", common_schema, RiskLevel.LOW, False),
            run_tests,
        )
        registry.register(
            ToolSpec(
                "read_file",
                "1.0",
                "Read a workspace file",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                RiskLevel.LOW,
                False,
            ),
            read_file,
        )
        registry.register(
            ToolSpec(
                "search_text",
                "1.0",
                "Search Python source text",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                RiskLevel.LOW,
                False,
            ),
            search_text,
        )
        registry.register(
            ToolSpec("create_patch", "1.0", "Create patch", common_schema, RiskLevel.MEDIUM, False),
            create_patch,
        )
        registry.register(
            ToolSpec("apply_patch", "1.0", "Apply patch", common_schema, RiskLevel.HIGH, True),
            apply_patch,
        )
        return registry

    @staticmethod
    def _final_report(run: RunModel) -> str:
        return (
            "# EquiSeek Issue Triage Report\n\n"
            f"- Run: `{run.id}`\n"
            "- Result: division-by-zero behavior fixed\n"
            "- Verification: deterministic pytest tool completed after approval\n"
            "- Safety: patch was not applied before approval\n"
        )
