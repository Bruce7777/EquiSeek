from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from aegisrun.agents.profiles import AgentProfileSnapshot
from aegisrun.agents.subagents import (
    InProcessSubagentProvider,
    SubagentProvider,
    SubagentStartRequest,
    SubagentStopReason,
    SubagentWorkResult,
)
from aegisrun.core.security import canonical_hash
from aegisrun.harness.events import AgentEvent, EventSource, EventStore, WorkspaceEventStore
from aegisrun.harness.invariants import default_invariants
from aegisrun.harness.projections import project_events
from aegisrun.orchestration.executor import (
    SubtaskContext,
    SubtaskExecutor,
    SubtaskOutcome,
)
from aegisrun.orchestration.models import SAFE_ID, ExecutionPlan, TaskRecord
from aegisrun.sandbox.base import SandboxProvider, SandboxResult
from aegisrun.skills.catalog import SkillPackage, SkillSummary
from aegisrun.workspace.manager import TaskWorkspace, WorkspaceManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    description: str
    handlers: frozenset[str]
    allowed_skills: frozenset[str] = frozenset()
    allowed_tools: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    max_concurrency: int = 1
    max_depth: int = 1
    max_children: int = 64
    network_allowed: bool = False

    def __post_init__(self) -> None:
        if not SAFE_ID.fullmatch(self.name) or self.name in {".", ".."}:
            raise ValueError("agent name must be a safe segment")
        if not self.description.strip() or not self.handlers:
            raise ValueError("agent requires a description and at least one handler")
        if self.max_concurrency < 1 or self.max_children < 1 or self.max_depth < 0:
            raise ValueError("agent runtime limits are invalid")


@dataclass(frozen=True, slots=True)
class AgentContext:
    plan: ExecutionPlan
    task: TaskRecord
    workspace: TaskWorkspace
    dependency_results: dict[str, dict[str, Any]]
    spec: AgentSpec
    profile: AgentProfileSnapshot
    skills: tuple[SkillPackage, ...]
    sandbox: SandboxProvider | None
    events: EventStore
    child_id: str

    async def exec(self, argv: list[str], *, read_only_workspace: bool = False) -> SandboxResult:
        return await SubtaskContext(
            plan=self.plan,
            task=self.task,
            workspace=self.workspace,
            dependency_results=self.dependency_results,
            sandbox=self.sandbox,
        ).exec(argv, read_only_workspace=read_only_workspace)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        return await self.events.append(
            event_type,
            payload,
            source=EventSource("agent", actor_id=self.spec.name),
            parent_session_id=self.plan.id,
            task_id=self.task.id,
        )


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    summary: str
    data: dict[str, Any]


AgentHandler = Callable[[AgentContext], Awaitable[AgentOutcome]]


class SkillResolver(Protocol):
    def list(self) -> tuple[SkillSummary, ...]: ...

    def describe(self, name: str) -> SkillSummary: ...

    def activate(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
    ) -> SkillPackage: ...


@dataclass(slots=True)
class _Registration:
    spec: AgentSpec
    profile: AgentProfileSnapshot
    handlers: dict[str, AgentHandler] = field(default_factory=dict)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, _Registration] = {}

    def register(self, spec: AgentSpec, handlers: dict[str, AgentHandler]) -> Callable[[], None]:
        if spec.name in self._agents:
            raise ValueError(f"duplicate agent: {spec.name}")
        if set(handlers) != set(spec.handlers):
            raise ValueError(f"handlers do not match agent spec: {spec.name}")
        registration = _Registration(spec, AgentProfileSnapshot.from_spec(spec), dict(handlers))
        self._agents[spec.name] = registration
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._agents.get(spec.name) is registration:
                del self._agents[spec.name]
            active = False

        return dispose

    def get(self, name: str) -> _Registration:
        try:
            return self._agents[name]
        except KeyError as error:
            raise ValueError(f"unknown agent: {name}") from error

    def list(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents[name].spec for name in sorted(self._agents))

    def profiles(self) -> tuple[AgentProfileSnapshot, ...]:
        return tuple(self._agents[name].profile for name in sorted(self._agents))


class DelegationLedger:
    def __init__(self, workspaces: WorkspaceManager, plan_id: str) -> None:
        self.workspaces = workspaces
        self.plan_id = plan_id
        self.records: list[dict[str, Any]] = []
        if self.path.is_file():
            try:
                persisted = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("delegation ledger is unreadable") from error
            if not isinstance(persisted, dict):
                raise ValueError("delegation ledger must be a JSON object")
            if persisted.get("plan_id") != plan_id:
                raise ValueError("delegation ledger plan mismatch")
            delegations = persisted.get("delegations", [])
            if not isinstance(delegations, list) or any(
                not isinstance(item, dict) for item in delegations
            ):
                raise ValueError("delegation ledger records are invalid")
            self.records = [dict(item) for item in delegations]
            for record in self.records:
                if record.get("status") == "running":
                    record["status"] = "interrupted"
                    record["error"] = "local runtime was interrupted"
            self.save()

    @property
    def path(self) -> Path:
        return self.workspaces.paths(self.plan_id).state / "delegations.json"

    def start(
        self, task: TaskRecord, spec: AgentSpec, skills: tuple[SkillPackage, ...]
    ) -> dict[str, Any]:
        record = {
            "id": f"{task.id}:{task.attempts}",
            "task_id": task.id,
            "attempt": task.attempts,
            "parent_agent": "lead-agent",
            "agent": spec.name,
            "status": "running",
            "skills": [skill.audit_dict() for skill in skills],
            "error": None,
            "summary": None,
        }
        self.records.append(record)
        self.save()
        return record

    def finish(
        self,
        record: dict[str, Any],
        *,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        record.update({"status": status, "summary": summary, "error": error})
        self.save()

    def save(self) -> None:
        self.workspaces.write_control_json(
            self.path,
            {"version": 1, "plan_id": self.plan_id, "delegations": self.records},
        )


class LocalAgentRuntime:
    """Executes a plan through fixed local subagents without privilege escalation."""

    def __init__(
        self,
        workspaces: WorkspaceManager,
        registry: AgentRegistry,
        skills: SkillResolver,
        *,
        max_concurrency: int = 4,
        max_delegations: int = 64,
        sandbox: SandboxProvider | None = None,
        subagents: SubagentProvider | None = None,
        event_store_factory: Callable[[ExecutionPlan], EventStore] | None = None,
    ) -> None:
        if max_delegations < 1:
            raise ValueError("max_delegations must be positive")
        self.workspaces = workspaces
        self.registry = registry
        self.skills = skills
        self.max_concurrency = max_concurrency
        self.max_delegations = max_delegations
        self.sandbox = sandbox
        self.subagents = subagents or InProcessSubagentProvider()
        self.event_store_factory = event_store_factory

    def validate_plan(self, plan: ExecutionPlan) -> None:
        if sum(task.spec.max_attempts for task in plan.tasks.values()) > self.max_delegations:
            raise ValueError("plan exceeds delegation budget")
        per_agent_delegations: dict[str, int] = {}
        for task in plan.tasks.values():
            registration = self.registry.get(task.spec.agent)
            spec = registration.spec
            if spec.max_depth < 1:
                raise ValueError(f"agent {spec.name} does not allow child delegation")
            per_agent_delegations[spec.name] = (
                per_agent_delegations.get(spec.name, 0) + task.spec.max_attempts
            )
            if per_agent_delegations[spec.name] > spec.max_children:
                raise ValueError(f"agent {spec.name} exceeds its child delegation budget")
            if task.spec.network_allowed and not spec.network_allowed:
                raise ValueError(f"agent {spec.name} is not allowed to use network")
            if task.handler not in spec.handlers:
                raise ValueError(f"handler {task.handler} is not allowed for agent {spec.name}")
            missing_capabilities = set(task.spec.required_capabilities) - spec.capabilities
            if missing_capabilities:
                raise ValueError(
                    f"agent {spec.name} lacks capabilities: {sorted(missing_capabilities)}"
                )
            undeclared_skills = set(task.spec.skills) - spec.allowed_skills
            if undeclared_skills:
                raise ValueError(
                    f"agent {spec.name} cannot load skills: {sorted(undeclared_skills)}"
                )
            for skill_name in task.spec.skills:
                summary = self.skills.describe(skill_name)
                if summary.allowed_agents and spec.name not in summary.allowed_agents:
                    raise ValueError(f"skill {skill_name} is not allowed for agent {spec.name}")
                denied_tools = set(summary.allowed_tools) - spec.allowed_tools
                if denied_tools:
                    raise ValueError(
                        f"skill {skill_name} exceeds {spec.name} tools: {sorted(denied_tools)}"
                    )
                if summary.network_required and not task.spec.network_allowed:
                    raise ValueError(f"skill {skill_name} requires task network access")

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExecutionPlan:
        self.validate_plan(plan)
        events = self._event_store(plan)
        await self._start_session(plan, events)
        ledger = DelegationLedger(self.workspaces, plan.id)
        limiters = {
            registration.spec.name: asyncio.Semaphore(registration.spec.max_concurrency)
            for registration in self.registry._agents.values()
        }
        task_handlers: dict[str, Callable[[SubtaskContext], Awaitable[SubtaskOutcome]]] = {}

        for task_id, task in plan.tasks.items():
            registration = self.registry.get(task.spec.agent)
            handler = registration.handlers[task.handler]

            async def delegated(
                context: SubtaskContext,
                *,
                registration: _Registration = registration,
                handler: AgentHandler = handler,
            ) -> SubtaskOutcome:
                spec = registration.spec
                profile = registration.profile
                async with limiters[spec.name]:
                    activated = tuple(
                        self.skills.activate(
                            name,
                            agent=spec.name,
                            granted_tools=spec.allowed_tools,
                            network_allowed=context.task.spec.network_allowed,
                        )
                        for name in context.task.spec.skills
                    )
                    for skill in activated:
                        await events.append(
                            "skill/loaded",
                            {
                                "name": skill.summary.name,
                                "provider": skill.summary.provider,
                                "manifest_sha256": skill.summary.manifest_sha256,
                                "package_sha256": skill.package_sha256,
                                "model_invocable": skill.summary.model_invocable,
                                "user_invocable": skill.summary.user_invocable,
                            },
                            source=EventSource("runtime", actor_id=spec.name),
                            parent_session_id=context.plan.id,
                            task_id=context.task.id,
                        )
                    self.workspaces.write_json(
                        context.workspace.input / "context.json",
                        {
                            "plan_id": context.plan.id,
                            "goal": context.plan.goal,
                            "plan_context": context.plan.context,
                            "task": context.task.spec.to_dict(),
                            "dependency_results": context.dependency_results,
                            "agent": spec.name,
                            "profile": profile.to_dict(),
                            "skills": [skill.audit_dict() for skill in activated],
                        },
                    )
                    delegation = ledger.start(context.task, spec, activated)
                    child_id = (
                        "child-"
                        + canonical_hash(
                            {
                                "plan": context.plan.id,
                                "task": context.task.id,
                                "attempt": context.task.attempts,
                                "agent": spec.name,
                            }
                        )[:24]
                    )
                    agent_context = AgentContext(
                        plan=context.plan,
                        task=context.task,
                        workspace=context.workspace,
                        dependency_results=context.dependency_results,
                        spec=spec,
                        profile=profile,
                        skills=activated,
                        sandbox=context.sandbox,
                        events=events,
                        child_id=child_id,
                    )

                    async def work() -> SubagentWorkResult:
                        outcome = await handler(agent_context)
                        return SubagentWorkResult(outcome.summary, outcome.data)

                    handle = await self.subagents.start(
                        SubagentStartRequest(
                            child_id=child_id,
                            label=context.task.title,
                            parent_session_id=context.plan.id,
                            task_id=context.task.id,
                            depth=1,
                            max_depth=profile.max_depth,
                            profile=profile,
                            work=work,
                        ),
                        events,
                    )
                    try:
                        outcome = await asyncio.shield(handle.result)
                    except asyncio.CancelledError:
                        handle.cancel("delegation was cancelled or timed out")
                        ledger.finish(
                            delegation,
                            status="cancelled",
                            error="delegation was cancelled or timed out",
                        )
                        raise
                    finally:
                        await handle.dispose()
                    if outcome.stop_reason is SubagentStopReason.CANCELLED:
                        ledger.finish(delegation, status="cancelled", error=outcome.error)
                        raise asyncio.CancelledError(outcome.error or "subagent cancelled")
                    if outcome.stop_reason is not SubagentStopReason.COMPLETED:
                        ledger.finish(delegation, status="failed", error=outcome.error)
                        raise RuntimeError(outcome.error or "subagent failed")
                    summary = outcome.summary or "subagent completed"
                    data = outcome.data or {}
                    ledger.finish(delegation, status="succeeded", summary=summary)
                    return SubtaskOutcome(summary, data)

            task_handlers[task_id] = delegated

        # Task-id lookup lets the same handler name be safely specialized per task.
        executor = SubtaskExecutor(
            self.workspaces,
            max_concurrency=self.max_concurrency,
            sandbox=self.sandbox,
            event_sink=lambda event_type, payload, task_id: events.append(
                event_type,
                payload,
                source=EventSource("runtime", actor_id="lead-agent"),
                task_id=task_id,
            ),
        )
        result = await executor.execute(
            plan, {}, task_handlers=task_handlers, on_progress=on_progress
        )
        await events.append(
            "plan/status",
            {
                "plan_id": plan.id,
                "status": plan.status.value,
                "version": plan.version,
            },
            source=EventSource("runtime", actor_id="lead-agent"),
        )
        plan.context["agent_runtime"] = {
            "mode": "local",
            "event_sourced": True,
            "lead_agent": "lead-agent",
            "delegations": len(ledger.records),
            "ledger": ".state/delegations.json",
            "sandbox_required": any(task.spec.require_isolation for task in plan.tasks.values()),
            "events": ".state/events.jsonl",
            "event_count": len(await events.load()),
        }
        await events.flush()
        self.workspaces.plan_store(plan.id).save(plan)
        if on_progress is not None:
            try:
                on_progress(plan.to_dict())
            except Exception:
                logger.debug("agent runtime progress observer failed", exc_info=True)
        return result

    def _event_store(self, plan: ExecutionPlan) -> EventStore:
        if self.event_store_factory is not None:
            return self.event_store_factory(plan)
        paths = self.workspaces.create_run(plan.id)
        return WorkspaceEventStore(
            paths.state / "events.jsonl",
            run_id=plan.id,
            invariants=default_invariants(),
        )

    async def _start_session(self, plan: ExecutionPlan, events: EventStore) -> None:
        existing = await events.load()
        if existing:
            projection = project_events(existing)
            for child_id, child in projection.subagents.items():
                if child.get("status") == "running":
                    await events.append(
                        "subagent/ended",
                        {
                            "child_id": child_id,
                            "provider": child.get("provider", "unknown"),
                            "stop_reason": "interrupted",
                            "summary": None,
                            "error": "previous process ended before settlement",
                        },
                        source=EventSource("runtime", actor_id="lead-agent"),
                    )
            await events.append(
                "session/resumed",
                {"plan_id": plan.id, "plan_version": plan.version},
                source=EventSource("runtime", actor_id="lead-agent"),
            )
            return
        profiles = [profile.to_dict() for profile in self.registry.profiles()]
        await events.append(
            "session/header",
            {
                "plan_id": plan.id,
                "goal": plan.goal,
                "lead_agent": "lead-agent",
                "delegation_depth": 0,
                "event_schema": 1,
            },
            source=EventSource("runtime", actor_id="lead-agent"),
        )
        await events.append(
            "plan/created",
            {
                "plan_id": plan.id,
                "goal": plan.goal,
                "version": plan.version,
                "tasks": [task.spec.to_dict() for task in plan.tasks.values()],
                "profiles": profiles,
            },
            source=EventSource("runtime", actor_id="lead-agent"),
        )
        entries = [summary.to_dict() for summary in self.skills.list()]
        await events.append(
            "skill/catalog",
            {
                "entries": entries,
                "revision": canonical_hash(entries),
                "complete": True,
            },
            source=EventSource("runtime", actor_id="lead-agent"),
        )
