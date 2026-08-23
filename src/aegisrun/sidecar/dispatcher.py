from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import date, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from aegisrun.agents.investment_conversation import (
    InvestmentConversationStore,
    InvestmentIntentRouter,
    InvestmentThreadState,
)
from aegisrun.agents.investment_runtime import InvestmentAgentRunRequest
from aegisrun.application.requests import InvestmentAgentTaskRequest, ResearchRequest
from aegisrun.application.services import (
    execute_investment_agent,
    execute_macro_research,
    execute_research,
    market_data_provider,
)
from aegisrun.artifacts.html_report import render_investment_html
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.portfolio.models import Position, WatchItem
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.research.deepseek import (
    ModelServiceError,
    deepseek_model_supports_vision,
    normalize_deepseek_model,
    normalize_model_base_url,
    normalize_model_provider,
)
from aegisrun.research.journal import (
    evaluate_research_outcome,
    initial_research_outcome,
    unavailable_research_outcome,
)
from aegisrun.research.vision import OpenAICompatibleVisionClient, VisionConfig
from aegisrun.sidecar.attachments import process_attachments, validate_attachment_inputs
from aegisrun.sidecar.local_state import (
    LocalSettingsStore,
    effective_skill_root,
    effective_workspace,
    user_data_root,
)
from aegisrun.sidecar.protocol import PROTOCOL_VERSION, RpcProtocolError, RpcRequest
from aegisrun.sidecar.runs import RunRegistry
from aegisrun.sidecar.serialization import json_value, research_projection
from aegisrun.sidecar.skill_management import LocalSkillManager
from aegisrun.skills import SkillWorkspace, SkillWorkspacePolicy

CAPABILITIES = (
    "system.health",
    "system.capabilities",
    "system.bootstrap",
    "settings.get",
    "settings.patch",
    "workspace.list",
    "workspace.add",
    "workspace.select",
    "conversation.list",
    "conversation.create",
    "conversation.get",
    "conversation.delete",
    "skills.list",
    "skills.get",
    "skills.save",
    "skills.delete",
    "skills.import_file",
    "skills.root",
    "portfolio.get",
    "portfolio.upsert_position",
    "portfolio.remove_position",
    "portfolio.upsert_watch",
    "portfolio.remove_watch",
    "research.start",
    "research.history",
    "agent.start",
    "macro.start",
    "run.get",
    "run.events",
    "run.list_recent",
    "run.delete",
    "run.cancel",
    "system.shutdown",
)


def _application_version() -> str:
    try:
        return version("equiseek")
    except PackageNotFoundError:
        return "0.0.0-dev"


class SidecarDispatcher:
    def __init__(self, *, runs: RunRegistry | None = None) -> None:
        self.runs = runs or RunRegistry(history_path=user_data_root() / "run-history.json")
        self.settings = LocalSettingsStore()
        self.portfolio = PortfolioRepository(user_data_root() / "portfolio.json")
        self.conversations = InvestmentConversationStore(user_data_root() / "conversations")
        self.shutdown_requested = False

    def _skill_workspace(self) -> SkillWorkspace:
        settings = self.settings.load()
        return SkillWorkspace(
            SkillWorkspacePolicy(
                include_builtin=bool(settings["includeBuiltinSkills"]),
                user_roots=(effective_skill_root(settings),),
            )
        )

    def _skill_list(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self._skill_workspace().list():
            payload = item.to_dict()
            payload["sourceLabel"] = "用户 Skill" if item.provider.startswith("user-") else "内置"
            result.append(json_value(payload))
        return result

    def _skill_manager(self) -> LocalSkillManager:
        return LocalSkillManager(effective_skill_root(self.settings.load()))

    def _workspaces(self) -> list[dict[str, Any]]:
        settings = self.settings.load()
        active = effective_workspace(settings).expanduser().resolve()
        active.mkdir(parents=True, exist_ok=True)
        raw_roots = settings.get("workspaceRoots", [])
        roots = [active]
        if isinstance(raw_roots, list):
            for value in raw_roots:
                try:
                    candidate = Path(str(value)).expanduser().resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if candidate.is_dir() and not candidate.is_symlink() and candidate not in roots:
                    roots.append(candidate)
        return [self._workspace_payload(path, active=path == active) for path in roots]

    @staticmethod
    def _workspace_id(path: Path) -> str:
        return "ws-" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]

    def _workspace_payload(self, path: Path, *, active: bool) -> dict[str, Any]:
        default = user_data_root() / "investment-agent-workspaces"
        return {
            "id": self._workspace_id(path),
            "name": "默认投资工作区" if path == default.resolve() else path.name,
            "path": str(path),
            "active": active,
            "writable": os.access(path, os.R_OK | os.W_OK | os.X_OK),
        }

    def _workspace_path(self, workspace_id: object = "") -> Path:
        entries = self._workspaces()
        selected = str(workspace_id or "").strip()
        item = next(
            (entry for entry in entries if entry["id"] == selected),
            next(entry for entry in entries if entry["active"]),
        )
        return Path(str(item["path"])).resolve(strict=True)

    def _add_workspace(self, value: object) -> dict[str, Any]:
        raw = Path(str(value or "")).expanduser()
        if not raw.is_absolute():
            raise ValueError("workspace path must be absolute")
        path = raw.resolve(strict=True)
        if not path.is_dir() or path.is_symlink():
            raise ValueError("工作区必须是本机真实目录，不能是符号链接")
        if path in {Path("/").resolve(), Path.home().resolve()}:
            raise ValueError("不能把文件系统根目录或整个用户主目录设为 Agent 工作区")
        settings = self.settings.load()
        roots = [str(item) for item in settings.get("workspaceRoots", [])]
        if str(path) not in roots:
            roots.append(str(path))
        self.settings.patch({"workspaceRoots": roots, "workspaceRoot": str(path)})
        return {"items": self._workspaces(), "activeId": self._workspace_id(path)}

    def _select_workspace(self, workspace_id: object) -> dict[str, Any]:
        selected = str(workspace_id or "").strip()
        entry = next((item for item in self._workspaces() if item["id"] == selected), None)
        if entry is None:
            raise ValueError("工作区不存在或已从本机移除")
        self.settings.patch({"workspaceRoot": str(entry["path"])})
        return {"items": self._workspaces(), "activeId": selected}

    async def dispatch(self, request: RpcRequest) -> dict[str, Any]:
        method = request.method
        params = request.params
        try:
            if method == "system.health":
                self._expect_empty(method, params)
                return {
                    "status": "ok",
                    "protocolVersion": PROTOCOL_VERSION,
                    "applicationVersion": _application_version(),
                    "transport": "stdio-ndjson",
                    "activeRuns": len(
                        [item for item in self.runs.list_recent() if item["status"] == "running"]
                    ),
                }
            if method == "system.capabilities":
                self._expect_empty(method, params)
                return {
                    "protocolVersion": PROTOCOL_VERSION,
                    "methods": list(CAPABILITIES),
                    "events": ["run.event"],
                }
            if method == "system.bootstrap":
                self._expect_empty(method, params)
                return {
                    "settings": self.settings.load(),
                    "workspaces": self._workspaces(),
                    "skills": self._skill_list(),
                    "portfolio": self.portfolio.load().to_dict(),
                    "recentRuns": self.runs.list_recent(),
                    "conversations": self.conversations.list_threads(),
                    "runtime": {
                        "mode": "local-sidecar",
                        "database": "SQLite + JSON",
                        "loginRequired": False,
                        "networkDefault": True,
                    },
                }
            if method == "settings.get":
                self._expect_empty(method, params)
                return self.settings.load()
            if method == "settings.patch":
                return self.settings.patch(params)
            if method == "workspace.list":
                self._expect_empty(method, params)
                return {"items": self._workspaces()}
            if method == "workspace.add":
                return self._add_workspace(self._text(params, "path"))
            if method == "workspace.select":
                return self._select_workspace(self._text(params, "workspaceId"))
            if method == "conversation.list":
                self._expect_empty(method, params)
                return {"items": self.conversations.list_threads()}
            if method == "conversation.create":
                self._expect_empty(method, params)
                thread_id = f"chat-{uuid4().hex}"
                return self._conversation_payload(self.conversations.create_thread(thread_id))
            if method == "conversation.get":
                return self._conversation_payload(
                    self.conversations.load_thread(self._text(params, "threadId"))
                )
            if method == "conversation.delete":
                thread_id = self._text(params, "threadId")
                self.conversations.clear_thread(thread_id)
                return {"deleted": thread_id}
            if method == "skills.list":
                self._expect_empty(method, params)
                return {"items": self._skill_list()}
            if method == "skills.get":
                return self._skill_manager().detail(
                    self._text(params, "name"), self._skill_workspace()
                )
            if method == "skills.save":
                name = self._text(params, "name")
                self._skill_manager().save(name, self._text(params, "content"))
                return self._skill_manager().detail(name, self._skill_workspace())
            if method == "skills.delete":
                name = self._text(params, "name")
                self._skill_manager().delete(name, self._skill_workspace())
                return {"deleted": name, "items": self._skill_list()}
            if method == "skills.import_file":
                name = self._skill_manager().import_file(Path(self._text(params, "path")))
                return self._skill_manager().detail(name, self._skill_workspace())
            if method == "skills.root":
                self._expect_empty(method, params)
                return {"path": str(self._skill_manager().ensure_root())}
            if method == "portfolio.get":
                self._expect_empty(method, params)
                return self.portfolio.load().to_dict()
            if method == "portfolio.upsert_position":
                return self.portfolio.upsert_position(Position.from_dict(params)).to_dict()
            if method == "portfolio.remove_position":
                return self.portfolio.remove_position(self._text(params, "symbol")).to_dict()
            if method == "portfolio.upsert_watch":
                return self.portfolio.upsert_watch(WatchItem.from_dict(params)).to_dict()
            if method == "portfolio.remove_watch":
                return self.portfolio.remove_watch(self._text(params, "symbol")).to_dict()
            if method == "research.start":
                return self._start_research(params)
            if method == "research.history":
                return await self._research_history(params)
            if method == "agent.start":
                return self._start_agent(params)
            if method == "macro.start":
                self._expect_empty(method, params)
                return self._start_macro()
            if method == "run.get":
                return self.runs.get(self._text(params, "runId")).view()
            if method == "run.events":
                after_seq = int(params.get("afterSeq", 0))
                return {"items": self.runs.events(self._text(params, "runId"), after_seq)}
            if method == "run.list_recent":
                self._expect_empty(method, params)
                return {"items": self.runs.list_recent()}
            if method == "run.delete":
                run_id = self._text(params, "runId")
                self.runs.delete(run_id)
                return {"deleted": run_id}
            if method == "run.cancel":
                return (await self.runs.cancel(self._text(params, "runId"))).view()
            if method == "system.shutdown":
                self._expect_empty(method, params)
                self.shutdown_requested = True
                await self.runs.shutdown()
                return {"status": "shutting-down"}
        except RpcProtocolError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise RpcProtocolError(-32602, str(error), request_id=request.request_id) from error
        raise RpcProtocolError(
            -32601,
            f"method not found: {request.method}",
            request_id=request.request_id,
        )

    def _start_research(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings.load()
        symbol = self._text(params, "symbol").upper()
        source = str(params.get("source", "demo"))
        if source not in {"demo", "baostock", "tushare"}:
            raise ValueError("source must be demo, baostock or tushare")
        end_date = date.fromisoformat(str(params.get("endDate", date.today().isoformat())))
        start_date = date.fromisoformat(
            str(params.get("startDate", (end_date - timedelta(days=900)).isoformat()))
        )
        adjustment = AdjustmentMode(str(params.get("adjustment", "qfq")))

        async def execute(report: Any) -> dict[str, Any]:
            result = await execute_research(
                ResearchRequest(
                    source=source,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjustment=adjustment,
                    tushare_token=self._optional_secret(params, "tushareToken"),
                    deepseek_api_key=None,
                    use_ai=False,
                    industry=str(params.get("industry", "")),
                    workspace_root=str(effective_workspace(settings)),
                ),
                on_progress=lambda value: report("research.progress", json_value(value)),
            )
            payload = research_projection(result)
            raw_tasks = payload.get("plan", {}).get("tasks", [])
            tasks = [item for item in raw_tasks if isinstance(item, dict)]
            trace = [
                {
                    "stage": "research-task",
                    "title": str(item.get("title", item.get("id", "个股研究步骤"))),
                    "status": str(item.get("status", "unknown")),
                    "summary": str(item.get("summary") or item.get("agent", "")),
                    "agent_name": str(item.get("agent", "research-agent")),
                    "skill_names": [str(skill) for skill in item.get("skills", [])],
                    "depends_on": [str(value) for value in item.get("depends_on", [])],
                }
                for item in tasks
            ]
            raw_gates = payload.get("strategy", {}).get("decision_path", [])
            for gate in raw_gates if isinstance(raw_gates, list) else []:
                if not isinstance(gate, dict):
                    continue
                trace.append(
                    {
                        "stage": "decision-gate",
                        "title": str(gate.get("title", "规则门控")),
                        "status": str(gate.get("status", "unknown")),
                        "summary": str(gate.get("summary", "")),
                        "agent_name": "decision-agent",
                        "skill_names": ["multi-timeframe-macd-wr"],
                    }
                )
            trace.append(
                {
                    "stage": "guardrail",
                    "title": "输出风险与失效条件校验",
                    "status": "succeeded",
                    "summary": str(payload.get("advice", {}).get("invalidation_condition", "")),
                    "agent_name": "compliance-agent",
                    "skill_names": ["investment-output-guardrail"],
                }
            )
            skill_names = sorted(
                {
                    str(skill)
                    for item in trace
                    for skill in item.get("skill_names", [])
                    if str(skill).strip()
                }
            )
            payload["trace"] = trace
            payload["active_skills"] = [
                {"name": name, "provider": "research-pipeline"} for name in skill_names
            ]

            if not result.workspace:
                raise RuntimeError("个股研究没有返回隔离工作区")
            artifact_root = Path(result.workspace) / "artifacts"
            artifact_root.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_root / f"{result.data.symbol}-research-report.html"
            temporary = artifact_root / f".{result.data.symbol}-research-report.html.tmp"
            temporary.write_text(
                render_investment_html(
                    title=f"{result.data.symbol} 完整个股研究报告",
                    goal=f"研究 {result.data.symbol} 的买入条件、证据和失效边界",
                    content=str(payload.get("summary", "")),
                    skills=skill_names,
                    trace=trace,
                    data_source=f"{result.data.source} · {result.data.as_of}",
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(artifact_path)
            payload["artifacts"] = [
                {
                    "name": artifact_path.name,
                    "path": str(artifact_path),
                    "media_type": "text/html",
                    "size_bytes": artifact_path.stat().st_size,
                }
            ]
            return payload

        return self.runs.start("research", execute).view(include_result=False)

    async def _research_history(self, params: dict[str, Any]) -> dict[str, Any]:
        refresh_requested = bool(params.get("refresh", False))
        network_enabled = bool(self.settings.load().get("enableNetwork", True))
        records = list(self.runs.completed("research"))
        for record in records:
            if not isinstance(record.result, dict):
                continue
            if not isinstance(record.result.get("outcome"), dict):
                result = dict(record.result)
                result["outcome"] = initial_research_outcome(result)
                self.runs.update_result(record.run_id, result)

        refreshed = refresh_requested and network_enabled
        if refreshed:
            await self._refresh_research_outcomes(
                records,
                tushare_token=self._optional_secret(params, "tushareToken"),
            )

        return {
            "items": [record.view(include_result=True) for record in reversed(records)],
            "refreshed": refreshed,
        }

    async def _refresh_research_outcomes(
        self,
        records: list[Any],
        *,
        tushare_token: str | None,
    ) -> None:
        groups: dict[tuple[str, str, str], list[Any]] = {}
        for record in records:
            result = record.result
            if not isinstance(result, dict):
                continue
            outcome = result.get("outcome")
            if isinstance(outcome, dict) and not outcome.get("is_real_market_data", True):
                continue
            source = str(result.get("source", ""))
            symbol = str(result.get("symbol", ""))
            adjustment = str(result.get("adjustment", "qfq"))
            if source and symbol:
                groups.setdefault((source, symbol, adjustment), []).append(record)

        today = date.today()
        for (source, symbol, adjustment_value), grouped in groups.items():
            dated: list[tuple[Any, date]] = []
            for record in grouped:
                raw_as_of = str(record.result.get("asOf", ""))
                try:
                    dated.append((record, date.fromisoformat(raw_as_of)))
                except ValueError:
                    self._store_outcome_warning(record, "研究记录缺少有效的数据截止日")
            if not dated:
                continue
            earliest = min(item[1] for item in dated)
            if earliest >= today:
                for record, baseline_date in dated:
                    advice = record.result.get("advice", {})
                    baseline = float(advice.get("current_price", 0) or 0)
                    result = dict(record.result)
                    result["outcome"] = evaluate_research_outcome(
                        result,
                        latest_price=baseline,
                        latest_as_of=baseline_date,
                        trading_days=0,
                    )
                    self.runs.update_result(record.run_id, result)
                continue

            provider = None
            try:
                provider = market_data_provider(
                    source,
                    tushare_token if source == "tushare" else None,
                )
                data = await asyncio.to_thread(
                    provider.fetch_daily,
                    symbol,
                    earliest,
                    today,
                    AdjustmentMode(adjustment_value),
                )
                latest = data.bars[-1]
                for record, baseline_date in dated:
                    trading_days = sum(
                        1 for bar in data.bars if bar.trade_date > baseline_date
                    )
                    result = dict(record.result)
                    result["outcome"] = evaluate_research_outcome(
                        result,
                        latest_price=latest.close,
                        latest_as_of=latest.trade_date,
                        trading_days=trading_days,
                    )
                    self.runs.update_result(record.run_id, result)
            except Exception as error:
                error_message = str(error)
                if tushare_token:
                    error_message = error_message.replace(tushare_token, "[REDACTED]")
                reason = f"最新行情刷新失败：{type(error).__name__}: {error_message[:160]}"
                for record, _ in dated:
                    self._store_outcome_warning(record, reason)
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()

    def _store_outcome_warning(self, record: Any, reason: str) -> None:
        if not isinstance(record.result, dict):
            return
        result = dict(record.result)
        result["outcome"] = unavailable_research_outcome(result, reason)
        self.runs.update_result(record.run_id, result)

    def _start_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        question = self._text(params, "question")
        routed = InvestmentIntentRouter().route(question)
        thread_id = str(params.get("threadId", "desktop-default"))
        attachment_inputs = params.get("attachments", [])
        validated_attachments = validate_attachment_inputs(attachment_inputs)
        thread_state = self.conversations.append(
            thread_id,
            "user",
            question,
            intent=routed.intent,
            attachments=tuple(item for _, item in validated_attachments),
        )
        conversation_summary = self._conversation_context(thread_state)
        memory = self.conversations.load_memory()
        settings = self.settings.load()
        workspace = self._skill_workspace()
        requested_skills = tuple(
            str(value) for value in params.get("skillNames", []) if str(value).strip()
        )
        selection = workspace.select_for_turn(
            question,
            defaults=requested_skills,
            agent="investment-lead-agent",
            granted_tools=frozenset(
                {
                    "market.load",
                    "research.analyze",
                    "portfolio.read",
                    "portfolio.load",
                    "research.screen",
                    "strategy.compose",
                    "backtest.run",
                    "skills.list",
                    "web.search",
                    "macro.verify_official",
                    "artifact.write",
                    "list_files",
                    "read",
                    "write",
                    "edit",
                    "bash",
                }
            ),
            network_allowed=bool(settings["enableNetwork"]),
        )
        source = str(params.get("source", settings["dataSource"]))
        if not bool(settings["enableNetwork"]):
            source = "demo"
        end_date = date.fromisoformat(str(params.get("endDate", date.today().isoformat())))
        deepseek_api_key = self._optional_secret(params, "deepseekApiKey")
        tushare_token = self._optional_secret(params, "tushareToken")
        deepseek_model = normalize_deepseek_model(
            params.get("model", settings.get("deepSeekModel"))
        )
        model_provider = normalize_model_provider(
            params.get("modelProvider", settings.get("modelProvider"))
        )
        model_base_url = normalize_model_base_url(
            settings.get("modelBaseUrl"), model_provider
        )
        selected_workspace = self._workspace_path(params.get("workspaceId"))
        permission = str(
            params.get("workspacePermission", settings.get("agentPermissionMode", "read-only"))
        )
        if permission not in {"read-only", "workspace-write"}:
            raise ValueError("workspacePermission must be read-only or workspace-write")
        if permission == "workspace-write" and not os.access(
            selected_workspace, os.R_OK | os.W_OK | os.X_OK
        ):
            raise ValueError("选定工作区当前不可写")
        workspace_id = self._workspace_id(selected_workspace)

        external_run_id = f"agent-{uuid4().hex}"

        async def execute(report: Any) -> dict[str, Any]:
            vision: OpenAICompatibleVisionClient | None = None
            if (
                bool(settings.get("enableDeepSeek"))
                and deepseek_api_key
                and deepseek_model_supports_vision(deepseek_model)
            ):
                vision = OpenAICompatibleVisionClient(
                    VisionConfig(
                        api_key=deepseek_api_key,
                        base_url=model_base_url,
                        model=deepseek_model,
                    )
                )
            try:
                try:
                    processed = await process_attachments(attachment_inputs, vision=vision)
                except ModelServiceError as error:
                    processed = await process_attachments(attachment_inputs, vision=None)
                    processed = type(processed)(
                        processed.metadata,
                        processed.context,
                        False,
                        (*processed.warnings, f"视觉模型未采用：{error}"),
                    )
            finally:
                if vision is not None:
                    await vision.close()
            result = await execute_investment_agent(
                InvestmentAgentTaskRequest(
                    run=InvestmentAgentRunRequest(
                        question=selection.prompt,
                        intent=routed.intent,
                        thread_id=thread_id,
                        portfolio=self.portfolio.load(),
                        source=source,
                        start_date=end_date - timedelta(days=900),
                        end_date=end_date,
                        adjustment=AdjustmentMode(str(params.get("adjustment", "qfq"))),
                        tushare_token=tushare_token,
                        symbol=routed.symbol,
                        active_skills=selection.packages,
                        skill_selection_mode=(
                            "explicit" if requested_skills or selection.explicit else "auto"
                        ),
                        memory=memory,
                        conversation_summary=conversation_summary,
                        attachment_context=processed.context,
                        attachment_warnings=processed.warnings,
                        working_directory=str(selected_workspace),
                        workspace_permission=cast(Any, permission),
                    ),
                    workspace_root=str(user_data_root() / "agent-runs" / workspace_id),
                    skills=workspace,
                    portfolio_manager=self.portfolio,
                    deepseek_api_key=(
                        deepseek_api_key if bool(settings.get("enableDeepSeek")) else None
                    ),
                    deepseek_model=deepseek_model,
                    model_provider=model_provider,
                    model_base_url=model_base_url,
                ),
                on_progress=lambda value: report("agent.progress", json_value(value)),
            )
            payload = cast(dict[str, Any], json_value(result))
            payload["kind"] = "agent"
            payload["thread_id"] = thread_id
            payload["attachments"] = [
                {
                    "name": item.name,
                    "mime_type": item.mime_type,
                    "size_bytes": item.size_bytes,
                }
                for item in processed.metadata
            ]
            payload["vision"] = {
                "used": processed.vision_used,
                "model": deepseek_model if processed.vision_used else None,
                "warnings": list(processed.warnings),
            }
            payload["model"] = {
                "provider": model_provider,
                "base_url": model_base_url,
                "id": deepseek_model,
                "enabled": bool(settings.get("enableDeepSeek") and deepseek_api_key),
                "vision": deepseek_model_supports_vision(deepseek_model),
            }
            payload["workspace_context"] = {
                "id": workspace_id,
                "path": str(selected_workspace),
                "permission": permission,
                "tools": ["list_files", "read", "bash"]
                + (["write", "edit"] if permission == "workspace-write" else []),
            }
            self.conversations.append(
                thread_id,
                "assistant",
                result.answer,
                run_id=external_run_id,
            )
            return payload

        return self.runs.start("agent", execute, run_id=external_run_id).view(include_result=False)

    def _start_macro(self) -> dict[str, Any]:
        settings = self.settings.load()

        async def execute(report: Any) -> dict[str, Any]:
            report("macro.progress", {"stage": "official-freshness", "network": True})
            result = await execute_macro_research(str(effective_workspace(settings)))
            payload = cast(dict[str, Any], json_value(result))
            # MacroValidity exposes a computed, user-facing status label via
            # ``to_dict``. Generic dataclass serialization intentionally only
            # includes declared fields, so preserve that label explicitly for
            # the desktop evidence gate.
            payload["validity"] = cast(dict[str, Any], json_value(result.validity.to_dict()))
            payload["kind"] = "macro"
            raw_tasks = payload.get("plan", {}).get("tasks", [])
            tasks = [item for item in raw_tasks if isinstance(item, dict)]
            trace = [
                {
                    "stage": "task",
                    "title": str(item.get("title", item.get("id", "宏观研究步骤"))),
                    "status": str(item.get("status", "unknown")),
                    "summary": str(item.get("summary") or item.get("agent", "")),
                    "agent_name": str(item.get("agent", "macro-agent")),
                    "skill_names": [str(skill) for skill in item.get("skills", [])],
                }
                for item in tasks
            ]
            skill_names = sorted(
                {
                    str(skill)
                    for item in tasks
                    for skill in item.get("skills", [])
                    if str(skill).strip()
                }
            )
            payload["trace"] = trace
            payload["active_skills"] = [
                {"name": name, "provider": "builtin"} for name in skill_names
            ]

            artifact_root = Path(result.workspace) / "artifacts"
            artifact_root.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_root / "macro-report.html"
            temporary = artifact_root / ".macro-report.html.tmp"
            snapshot = payload.get("analysis", {}).get("snapshot", {})
            temporary.write_text(
                render_investment_html(
                    title="求衡宏观投资研究报告",
                    goal="核验官方宏观数据时效并形成资本三流、成本转嫁和长期配置结论",
                    content=result.report,
                    skills=skill_names,
                    trace=trace,
                    data_source=str(snapshot.get("version", "官方宏观结构化基线")),
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(artifact_path)
            payload["artifacts"] = [
                {
                    "name": artifact_path.name,
                    "path": str(artifact_path),
                    "media_type": "text/html",
                    "size_bytes": artifact_path.stat().st_size,
                }
            ]
            return payload

        return self.runs.start("macro", execute).view(include_result=False)

    @staticmethod
    def _conversation_payload(state: InvestmentThreadState) -> dict[str, Any]:
        return {
            "threadId": state.thread_id,
            "turns": [
                {
                    "role": turn.role,
                    "content": turn.content,
                    "intent": turn.intent,
                    "runId": turn.run_id,
                    "attachments": [
                        {
                            "name": item.name,
                            "mimeType": item.mime_type,
                            "sizeBytes": item.size_bytes,
                        }
                        for item in turn.attachments
                    ],
                }
                for turn in state.turns
            ],
            "summary": state.summary,
            "compressedTurnCount": state.compressed_turn_count,
            "updatedAt": state.updated_at,
        }

    @staticmethod
    def _conversation_context(state: InvestmentThreadState) -> str:
        recent = "\n".join(
            f"- {'用户' if turn.role == 'user' else 'Agent'}：{turn.content[:500]}"
            for turn in state.turns[-8:]
        )
        return "\n".join(item for item in (state.summary, recent) if item).strip()[-3_500:]

    @staticmethod
    def _expect_empty(method: str, params: dict[str, Any]) -> None:
        if params:
            raise ValueError(f"{method} does not accept params")

    @staticmethod
    def _text(params: dict[str, Any], key: str) -> str:
        value = str(params.get(key, "")).strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    @staticmethod
    def _optional_secret(params: dict[str, Any], key: str) -> str | None:
        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError(f"{key} is invalid")
        return value.strip() or None


_default_dispatcher = SidecarDispatcher()


async def dispatch(request: RpcRequest) -> dict[str, Any]:
    """Compatibility entry point used by unit tests and simple embedders."""

    return await _default_dispatcher.dispatch(request)
