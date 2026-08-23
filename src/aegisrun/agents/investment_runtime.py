from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx

from aegisrun.agents.investment_conversation import InvestmentIntent, InvestmentMemory
from aegisrun.artifacts.html_report import render_investment_html
from aegisrun.core.domain import PolicySnapshot
from aegisrun.harness.events import EventSource, WorkspaceEventStore
from aegisrun.harness.workspace_tools import PersistentWorkspaceShell, WorkspaceFileEditor
from aegisrun.macro.analysis import analyze_macro_snapshot, build_macro_overlay
from aegisrun.macro.freshness import (
    MacroFreshnessVerifier,
    OfficialMacroFreshnessVerifier,
    snapshot_is_verified_current,
)
from aegisrun.macro.providers import default_macro_provider
from aegisrun.marketdata.baostock_provider import BaoStockProvider
from aegisrun.marketdata.cache import MarketDataCache, market_cache_enabled
from aegisrun.marketdata.cached_provider import CachedMarketDataProvider
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.providers import DemoMarketDataProvider, MarketDataProvider
from aegisrun.marketdata.tushare_provider import TushareProvider
from aegisrun.portfolio.analysis import CandidateInput, rank_strategy_candidates
from aegisrun.portfolio.models import PortfolioBook, Position
from aegisrun.portfolio.strategy_dsl import CandidateStrategy, candidate_strategy_from_skill
from aegisrun.research.advisor_chat import (
    AdvisorConversationContext,
    AdvisorEvidence,
    local_advisor_answer,
    local_investment_conversation_answer,
)
from aegisrun.research.deepseek import DeepSeekClient, ModelServiceError
from aegisrun.research.guardrails import InvestmentOutputGuard, UnsafeAdviceError
from aegisrun.research.service import ResearchResult, run_research
from aegisrun.skills import SkillPackage, SkillValidationError, SkillWorkspace
from aegisrun.tools import RiskLevel, ToolPipeline, ToolRegistry, ToolResult, ToolSpec
from aegisrun.workspace import WorkspaceManager

ProgressCallback = Callable[[dict[str, Any]], None]
ActionKind = Literal["tool", "final", "clarify"]
SkillSelectionMode = Literal["auto", "explicit"]
WorkspacePermissionMode = Literal["read-only", "workspace-write"]
_SAFE_TEXT_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.(?:md|txt|json)$")
_SAFE_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.(?:md|txt|json|html)$")
_WEB_TRIGGER = re.compile(r"最新|新闻|公告|联网|搜索|检索|今天|近期")


@dataclass(frozen=True, slots=True)
class InvestmentAgentAction:
    kind: ActionKind
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    reason: str = ""


class InvestmentActionModel(Protocol):
    async def choose_investment_action(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        active_skills: list[dict[str, str]],
        remaining_steps: int,
    ) -> InvestmentAgentAction: ...


@dataclass(frozen=True, slots=True)
class InvestmentAgentRunRequest:
    question: str
    intent: InvestmentIntent
    thread_id: str
    portfolio: PortfolioBook
    source: str
    start_date: date
    end_date: date
    adjustment: AdjustmentMode
    tushare_token: str | None = field(default=None, repr=False)
    web_search_api_key: str | None = field(default=None, repr=False)
    symbol: str | None = None
    evidence: AdvisorEvidence | None = None
    memory: InvestmentMemory | None = None
    conversation_summary: str = ""
    attachment_context: str = ""
    attachment_warnings: tuple[str, ...] = ()
    active_skills: tuple[SkillPackage, ...] = ()
    skill_selection_mode: SkillSelectionMode = "auto"
    working_directory: str = ""
    workspace_permission: WorkspacePermissionMode = "read-only"
    max_steps: int = 8

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("investment agent goal cannot be empty")
        if self.max_steps < 1 or self.max_steps > 20:
            raise ValueError("investment agent max_steps must be between 1 and 20")
        if self.skill_selection_mode not in {"auto", "explicit"}:
            raise ValueError("unknown investment agent skill selection mode")
        if self.workspace_permission not in {"read-only", "workspace-write"}:
            raise ValueError("unknown workspace permission mode")


@dataclass(frozen=True, slots=True)
class InvestmentAgentArtifact:
    name: str
    path: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class InvestmentAgentTraceStep:
    stage: str
    title: str
    status: str
    summary: str
    skill_names: tuple[str, ...] = ()
    tool_name: str = ""
    evidence_path: str = ""
    agent_name: str = ""
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InvestmentAgentRunResult:
    run_id: str
    status: Literal["succeeded", "failed", "needs_input"]
    answer: str
    answer_mode: Literal["local", "deepseek"]
    workspace: str
    artifacts: tuple[InvestmentAgentArtifact, ...]
    active_skills: tuple[dict[str, str], ...]
    tool_calls: tuple[str, ...]
    trace: tuple[InvestmentAgentTraceStep, ...]
    warning: str | None = None


class TavilySearchClient:
    """Small optional search adapter. The key never enters tool events or model context."""

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url="https://api.tavily.com/",
            timeout=30,
            transport=transport,
        )

    async def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        try:
            response = await self._client.post(
                "search",
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"联网搜索返回 HTTP {error.response.status_code}") from error
        except httpx.HTTPError as error:
            raise RuntimeError(f"联网搜索失败：{type(error).__name__}") from error
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(raw_results, list):
            raise RuntimeError("联网搜索返回结构无效")
        results: list[dict[str, str]] = []
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))[:2_000]
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            results.append(
                {
                    "title": str(item.get("title", "未命名来源"))[:240],
                    "url": url,
                    "content": " ".join(str(item.get("content", "")).split())[:1_200],
                }
            )
        return results

    async def close(self) -> None:
        await self._client.aclose()


class PublicWebSearchClient:
    """No-account public web search for the default desktop experience.

    DuckDuckGo's no-JavaScript page returns titles, source URLs and short snippets. It is
    intentionally used as discovery evidence only; downstream answers keep links and
    tell the user to inspect publication time and the original page.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=30,
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": "EquiSeek/0.2 public-research-client"},
        )

    async def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        try:
            response = await self._client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kl": "cn-zh"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"公开搜索返回 HTTP {error.response.status_code}") from error
        except httpx.HTTPError as error:
            raise RuntimeError(f"公开搜索失败：{type(error).__name__}") from error
        if len(response.content) > 2 * 1024 * 1024:
            raise RuntimeError("公开搜索返回内容超过 2 MiB 安全上限")
        results: list[dict[str, str]] = []
        anchors = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            response.text,
            re.I | re.S,
        )
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
            response.text,
            re.I | re.S,
        )
        for index, (raw_url, raw_title) in enumerate(anchors[:max_results]):
            url = unescape(raw_url).strip()[:2_000]
            redirect = urlparse(url)
            if redirect.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
                url = parse_qs(redirect.query).get("uddg", [""])[0][:2_000]
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            title = unescape(re.sub(r"<[^>]+>", " ", raw_title))
            raw_description = snippets[index] if index < len(snippets) else ""
            description = unescape(re.sub(r"<[^>]+>", " ", raw_description))
            results.append(
                {
                    "title": " ".join((title or "未命名来源").split())[:240],
                    "url": url,
                    "content": " ".join(description.split())[:1_200],
                }
            )
        return results

    async def close(self) -> None:
        await self._client.aclose()


class SearchClient(Protocol):
    async def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]: ...

    async def close(self) -> None: ...


class PortfolioManager(Protocol):
    def load(self) -> PortfolioBook: ...

    def upsert_position(self, position: Position) -> PortfolioBook: ...

    def remove_position(self, symbol: str) -> PortfolioBook: ...


class InvestmentAgentRuntime:
    """A bounded lead-agent loop for investment research.

    The model may select only registered tools. Every call is schema-validated,
    policy-checked, event-paired and scoped to one run workspace. When no model is
    configured, a deterministic planner exercises the same tool boundary.
    """

    def __init__(
        self,
        workspace_root: Path,
        skills: SkillWorkspace,
        *,
        model: InvestmentActionModel | None = None,
        web_transport: httpx.AsyncBaseTransport | None = None,
        macro_verifier: MacroFreshnessVerifier | None = None,
        portfolio_manager: PortfolioManager | None = None,
    ) -> None:
        self.workspaces = WorkspaceManager(workspace_root)
        self.skills = skills
        self.model = model
        self.web_transport = web_transport
        self.macro_verifier = macro_verifier or OfficialMacroFreshnessVerifier()
        self.portfolio_manager = portfolio_manager

    async def run(
        self,
        request: InvestmentAgentRunRequest,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> InvestmentAgentRunResult:
        run_id = f"investment-{uuid4().hex}"
        paths = self.workspaces.create_run(run_id)
        events = WorkspaceEventStore(paths.state / "events.jsonl", run_id=run_id, session_id=run_id)
        active = {package.summary.name: package for package in request.active_skills}
        observations: list[dict[str, Any]] = []
        tool_calls: list[str] = []
        state: dict[str, Any] = {
            "version": 1,
            "run_id": run_id,
            "thread_id": request.thread_id,
            "goal": request.question,
            "intent": request.intent,
            "status": "running",
            "steps": [],
            "active_skills": [self._skill_reference(item) for item in active.values()],
            "skill_selection_mode": request.skill_selection_mode,
            "working_directory": request.working_directory,
            "workspace_permission": request.workspace_permission,
        }
        self._save_state(paths.state / "investment-run.json", state)
        await events.append(
            "session/header",
            {
                "thread_id": request.thread_id,
                "goal": request.question,
                "runtime": "investment-lead-agent",
                "workspace": str(paths.root),
            },
            source=EventSource("runtime", actor_id="investment-lead-agent"),
        )
        self._notify(
            on_progress,
            {
                "kind": "run-started",
                "run_id": run_id,
                "goal": request.question,
                "workspace": str(paths.root),
            },
        )
        working_directory = await asyncio.to_thread(
            lambda: (
                Path(request.working_directory).expanduser().resolve(strict=True)
                if request.working_directory
                else paths.shared
            )
        )
        if not working_directory.is_dir() or working_directory.is_symlink():
            raise ValueError("求衡投研助手 工作区必须是真实的本地目录")
        registry, search, shell = self._build_tools(
            request,
            paths.root,
            working_directory,
            active,
        )
        allowed_tool_names = list(self._tool_names(registry))
        if request.workspace_permission == "read-only":
            allowed_tool_names = [
                name for name in allowed_tool_names if name not in {"write", "edit"}
            ]
        policy = PolicySnapshot(
            allowed_tools=tuple(
                spec.name
                for spec in registry.visible_specs(
                    PolicySnapshot(allowed_tools=tuple(allowed_tool_names))
                )
            ),
            approval_required=(),
            readable_prefixes=(str(paths.root),),
            writable_prefixes=(str(paths.artifacts),),
            network_allowed=request.source != "demo" or bool(request.web_search_api_key),
        )
        pipeline = ToolPipeline(registry, events=events)
        warning: str | None = None
        status: Literal["succeeded", "failed", "needs_input"] = "succeeded"
        answer_mode: Literal["local", "deepseek"] = "local"
        final_answer = ""
        trace: tuple[InvestmentAgentTraceStep, ...] = ()
        try:
            for step_index in range(1, request.max_steps + 1):
                action = await self._next_action(
                    request,
                    registry,
                    active,
                    observations,
                    request.max_steps - step_index + 1,
                    tuple(allowed_tool_names),
                )
                if action.kind == "final":
                    final_answer = action.content.strip()
                    answer_mode = "deepseek" if self.model is not None else "local"
                    break
                if action.kind == "clarify":
                    final_answer = action.content.strip() or "请补充完成研究所需的信息。"
                    answer_mode = "deepseek" if self.model is not None else "local"
                    status = "needs_input"
                    break
                if not action.tool_name:
                    raise RuntimeError("agent tool action did not name a tool")
                step = {
                    "index": step_index,
                    "title": self._tool_title(action.tool_name),
                    "tool": action.tool_name,
                    "status": "running",
                    "reason": action.reason,
                }
                state["steps"].append(step)
                self._save_state(paths.state / "investment-run.json", state)
                self._notify(
                    on_progress,
                    {
                        "kind": "step-started",
                        "run_id": run_id,
                        "index": step_index,
                        "total": request.max_steps,
                        **step,
                    },
                )
                try:
                    result = await pipeline.execute(
                        action.tool_name,
                        action.arguments,
                        policy,
                        agent_id="investment-lead-agent",
                        task_id=f"step-{step_index}",
                    )
                except Exception as error:
                    step["status"] = "failed"
                    step["detail"] = f"{type(error).__name__}: {error}"[:500]
                    observations.append(
                        {
                            "tool": action.tool_name,
                            "ok": False,
                            "error": step["detail"],
                        }
                    )
                else:
                    step["status"] = "succeeded"
                    step["detail"] = result.summary[:500]
                    observations.append(
                        {
                            "tool": action.tool_name,
                            "ok": True,
                            "summary": result.summary,
                            "data": result.data,
                            "artifact_id": result.artifact_id,
                        }
                    )
                tool_calls.append(action.tool_name)
                state["active_skills"] = [self._skill_reference(item) for item in active.values()]
                self._save_state(paths.state / "investment-run.json", state)
                self._notify(
                    on_progress,
                    {"kind": "step-ended", "run_id": run_id, **step},
                )
            else:
                warning = f"达到本轮 {request.max_steps} 步上限，已基于现有证据收束回答"

            if not final_answer:
                final_answer = self._local_synthesis(request, observations, active)
            try:
                InvestmentOutputGuard().ensure_safe(final_answer)
            except UnsafeAdviceError:
                warning = "模型输出未通过投资护栏，已改用本地证据摘要"
                final_answer = self._local_synthesis(request, observations, active)
                answer_mode = "local"
                try:
                    InvestmentOutputGuard().ensure_safe(final_answer)
                except UnsafeAdviceError:
                    final_answer = (
                        "## Skill 输出已被安全门阻止\n\n"
                        "本轮 Skill 含有收益保证、绝对涨跌或未经授权交易执行表达，"
                        "因此未将其作为投资结论。请修订 Skill 后重试。"
                    )
            trace = self._build_trace(
                request,
                state,
                observations,
                active,
                answer_mode=answer_mode,
            )
            trace = (
                *trace,
                InvestmentAgentTraceStep(
                    "artifact",
                    "生成 HTML 研究成果",
                    "succeeded",
                    "平台安全渲染器将结论、Skill 与可审阅执行链写入本地静态 HTML；"
                    "不包含脚本或远程资源。",
                    ("html-research-report",),
                    "workspace.render_html",
                    agent_name="artifact-agent",
                ),
            )
            trace_path = self._write_artifact(
                paths.artifacts,
                "investment-agent-trace.md",
                self._trace_markdown(request, trace),
            )
            report_path = self._write_artifact(
                paths.artifacts,
                "investment-agent-report.md",
                self._report_markdown(request, final_answer, observations, active, trace),
            )
            html_path = self._write_artifact(
                paths.artifacts,
                "investment-agent-report.html",
                render_investment_html(
                    title=(
                        f"{request.symbol} 投资研究"
                        if request.symbol
                        else "求衡投研助手研究成果"
                    ),
                    goal=request.question,
                    content=final_answer,
                    skills=tuple(
                        dict.fromkeys(
                            skill_name for step in trace for skill_name in step.skill_names
                        )
                    ),
                    trace=tuple(asdict(step) for step in trace),
                    data_source=request.source,
                ),
            )
            state["status"] = status
            state["answer_mode"] = answer_mode
            state["report"] = report_path.name
            state["html_report"] = html_path.name
            state["trace"] = trace_path.name
        except Exception as error:
            status = "failed"
            final_answer = (
                "## 本轮 Agent 运行失败\n\n"
                f"{type(error).__name__}: {error}\n\n"
                "已保留本轮工作区和执行记录，可在修正配置后重新发起。"
            )
            warning = str(error)[:500]
            state["status"] = "failed"
            state["error"] = f"{type(error).__name__}: {error}"[:2_000]
            trace = self._build_trace(
                request,
                state,
                observations,
                active,
                answer_mode=answer_mode,
                failure=state["error"],
            )
            try:
                trace_path = self._write_artifact(
                    paths.artifacts,
                    "investment-agent-trace.md",
                    self._trace_markdown(request, trace),
                )
                state["trace"] = trace_path.name
            except (OSError, ValueError):
                pass
        finally:
            if search is not None:
                await search.close()
            await shell.close()
            self._save_state(paths.state / "investment-run.json", state)
            await events.append(
                "assistant/message",
                {
                    "message_id": f"answer-{uuid4().hex}",
                    "content": [{"type": "text", "text": final_answer}],
                    "status": status,
                },
                source=EventSource("agent", actor_id="investment-lead-agent"),
            )
            await events.flush()

        artifacts = self._artifacts(paths.artifacts)
        self._notify(
            on_progress,
            {
                "kind": "run-ended",
                "run_id": run_id,
                "status": status,
                "workspace": str(paths.root),
                "artifacts": [asdict(item) for item in artifacts],
            },
        )
        return InvestmentAgentRunResult(
            run_id=run_id,
            status=status,
            answer=final_answer,
            answer_mode=answer_mode,
            workspace=str(paths.root),
            artifacts=artifacts,
            active_skills=tuple(self._skill_reference(item) for item in active.values()),
            tool_calls=tuple(tool_calls),
            trace=trace,
            warning=warning,
        )

    def _build_tools(
        self,
        request: InvestmentAgentRunRequest,
        run_root: Path,
        working_directory: Path,
        active: dict[str, SkillPackage],
    ) -> tuple[ToolRegistry, SearchClient | None, PersistentWorkspaceShell]:
        registry = ToolRegistry()
        writable = request.workspace_permission == "workspace-write"
        editor = WorkspaceFileEditor(working_directory, writable=writable)
        shell = PersistentWorkspaceShell(
            working_directory,
            writable=writable,
            network_allowed=request.source != "demo" or bool(request.web_search_api_key),
        )
        search = (
            TavilySearchClient(request.web_search_api_key, transport=self.web_transport)
            if request.web_search_api_key
            else PublicWebSearchClient(transport=self.web_transport)
            if request.source != "demo"
            else None
        )
        macro_cache: dict[str, Any] = {}

        async def load_macro_snapshot() -> Any:
            """Load once per Agent run; online runs use the latest complete official snapshot."""
            if "snapshot" not in macro_cache:
                provider = default_macro_provider(live=request.source != "demo")
                macro_cache["snapshot"] = await asyncio.to_thread(provider.load)
            return macro_cache["snapshot"]

        async def list_skills(_: dict[str, Any]) -> ToolResult:
            items = [
                {
                    "name": item.name,
                    "description": item.description,
                    "provider": item.provider,
                    "version": item.version,
                    "network_required": item.network_required,
                    "model_invocable": item.model_invocable,
                }
                for item in self.skills.list()
            ]
            return ToolResult(f"发现 {len(items)} 个可用 Skill", {"skills": items})

        async def activate_skill(arguments: dict[str, Any]) -> ToolResult:
            name = str(arguments["name"])
            summary = next((item for item in self.skills.list() if item.name == name), None)
            if summary is None:
                raise SkillValidationError(f"unknown skill: {name}")
            agent = summary.allowed_agents[0] if summary.allowed_agents else "advice-agent"
            granted = frozenset(self._tool_names(registry)) | frozenset(
                {"market-data-read", "deepseek-chat"}
            )
            package = self.skills.activate(
                name,
                agent=agent,
                granted_tools=granted,
                network_allowed=request.source != "demo" or search is not None,
            )
            active[name] = package
            return ToolResult(
                f"已按需加载 Skill：{name}（{package.summary.provider}）",
                {
                    "skill": self._skill_reference(package),
                    "instructions": package.instructions[:12_000],
                    "resources": sorted(package.resources),
                },
            )

        async def portfolio_snapshot(_: dict[str, Any]) -> ToolResult:
            items: list[dict[str, str]] = []
            positions = {item.symbol: item for item in request.portfolio.positions}
            for symbol in request.portfolio.symbols():
                position = positions.get(symbol)
                watch = next(
                    (item for item in request.portfolio.watchlist if item.symbol == symbol), None
                )
                items.append(
                    {
                        "symbol": symbol,
                        "name": (position.name if position else watch.name if watch else ""),
                        "industry": (
                            position.industry if position else watch.industry if watch else ""
                        ),
                        "kind": "position" if position else "watchlist",
                    }
                )
            return ToolResult(
                f"读取 {len(items)} 个本地持仓/自选标的（已排除数量、成本与备注）",
                {"securities": items},
            )

        async def portfolio_list(_: dict[str, Any]) -> ToolResult:
            book = self.portfolio_manager.load() if self.portfolio_manager else request.portfolio
            return ToolResult(
                f"读取本机 {len(book.positions)} 条持仓和 {len(book.watchlist)} 条自选",
                {
                    "positions": [item.to_dict() for item in book.positions],
                    "watchlist": [item.to_dict() for item in book.watchlist],
                    "local_only": True,
                },
            )

        async def portfolio_upsert(arguments: dict[str, Any]) -> ToolResult:
            if self.portfolio_manager is None:
                raise ValueError("当前运行时没有本地持仓写入权限")
            symbol = str(arguments["symbol"])
            current = self.portfolio_manager.load().position(symbol)
            quantity = arguments.get("quantity", current.quantity if current else None)
            cost_price = arguments.get("cost_price", current.cost_price if current else None)
            if quantity is None or cost_price is None:
                raise ValueError("新增持仓需要同时提供数量和成本价")
            position = Position(
                symbol=symbol,
                quantity=float(quantity),
                cost_price=float(cost_price),
                name=str(arguments.get("name", current.name if current else "")),
                opened_on=current.opened_on if current else None,
                notes=current.notes if current else "",
                industry=str(arguments.get("industry", current.industry if current else "")),
            )
            book = self.portfolio_manager.upsert_position(position)
            return ToolResult(
                f"已在本机{'更新' if current else '添加'}持仓 {position.symbol}",
                {
                    "action": "updated" if current else "added",
                    "position": position.to_dict(),
                    "position_count": len(book.positions),
                    "local_only": True,
                },
            )

        async def portfolio_remove(arguments: dict[str, Any]) -> ToolResult:
            if self.portfolio_manager is None:
                raise ValueError("当前运行时没有本地持仓写入权限")
            symbol = str(arguments["symbol"])
            current = self.portfolio_manager.load().position(symbol)
            if current is None:
                raise ValueError(f"本地持仓中不存在 {symbol}")
            book = self.portfolio_manager.remove_position(symbol)
            return ToolResult(
                f"已从本机持仓删除 {current.symbol}",
                {
                    "action": "removed",
                    "symbol": current.symbol,
                    "position_count": len(book.positions),
                    "local_only": True,
                },
            )

        async def current_evidence(_: dict[str, Any]) -> ToolResult:
            if request.evidence is None:
                raise ValueError("当前线程没有证券分析证据")
            return ToolResult(
                f"读取 {request.evidence.symbol} 截至 {request.evidence.as_of} 的确定性证据",
                request.evidence.to_prompt_payload(),
            )

        async def macro_snapshot(_: dict[str, Any]) -> ToolResult:
            snapshot = await load_macro_snapshot()
            analysis = analyze_macro_snapshot(snapshot)
            validity = await self.macro_verifier.verify(snapshot, today=date.today())
            view = analysis.investment_view
            return ToolResult(
                f"加载并核验宏观快照 {analysis.snapshot.version}：{validity.status_label}",
                {
                    "version": analysis.snapshot.version,
                    "as_of": analysis.snapshot.as_of.isoformat(),
                    "realtime": request.source != "demo",
                    "validity": validity.to_dict(),
                    "risk_appetite": (
                        view.risk_appetite_label if validity.current_decision_allowed else None
                    ),
                    "equity_exposure": (
                        view.equity_exposure if validity.current_decision_allowed else None
                    ),
                    "decision_summary": (
                        list(view.decision_summary) if validity.current_decision_allowed else []
                    ),
                    "historical_model_output": {
                        "risk_appetite": view.risk_appetite_label,
                        "equity_exposure": view.equity_exposure,
                        "decision_summary": list(view.decision_summary),
                    },
                    "analysis": analysis.to_dict(),
                    "limitations": list(analysis.limitations),
                    "research_trace": [
                        {
                            "stage": "macro-data",
                            "title": "加载并校验官方宏观快照",
                            "status": "succeeded",
                            "summary": (
                                f"{len(snapshot.metrics)} 项结构化指标，截止 {snapshot.as_of}"
                            ),
                            "skills": ["macro-official-data"],
                            "agent": "macro-data-agent",
                        },
                        {
                            "stage": "freshness-gate",
                            "title": "联网核验四个官方发布页",
                            "status": "succeeded",
                            "summary": validity.reason,
                            "skills": ["macro-official-freshness"],
                            "agent": "macro-freshness-agent",
                        },
                        {
                            "stage": "capital-flow",
                            "title": "计算资本流量、流向、流速与实体传导",
                            "status": "succeeded",
                            "summary": analysis.capital_flow.transmission_label,
                            "skills": ["capital-three-flows"],
                            "agent": "capital-flow-agent",
                        },
                        {
                            "stage": "cost-transfer",
                            "title": "计算成本转嫁压力链",
                            "status": "succeeded",
                            "summary": analysis.cost_transfer.pressure_label,
                            "skills": ["cost-transfer-lens"],
                            "agent": "cost-transfer-agent",
                        },
                        {
                            "stage": "macro-synthesis",
                            "title": "合成长期配置与行业判断",
                            "status": "succeeded",
                            "summary": analysis.investment_view.equity_exposure,
                            "skills": ["macro-investment-synthesis"],
                            "agent": "macro-synthesis-agent",
                        },
                    ],
                },
            )

        async def analyze_security(arguments: dict[str, Any]) -> ToolResult:
            symbol = str(arguments["symbol"])
            position = request.portfolio.position(symbol)
            industry = (
                position.industry
                if position is not None
                else self._industry(request.portfolio, symbol)
            )
            overlay = None
            if industry:
                snapshot = await load_macro_snapshot()
                if snapshot_is_verified_current(snapshot, reference_date=request.end_date):
                    overlay = build_macro_overlay(industry, analyze_macro_snapshot(snapshot))
            provider = self._provider(request.source, request.tushare_token)
            try:
                result = await run_research(
                    provider,
                    symbol,
                    request.start_date,
                    request.end_date,
                    request.adjustment,
                    position=position,
                    macro_overlay=overlay,
                )
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            return ToolResult(
                f"完成 {result.data.symbol} 研究：{result.investment_advice.action_label}，"
                f"置信度 {result.investment_advice.confidence}/100",
                self._research_payload(result),
            )

        async def screen_candidates(_: dict[str, Any]) -> ToolResult:
            symbols = request.portfolio.symbols()[:50]
            if not symbols:
                raise ValueError("本地候选池为空，请先添加持仓或自选")
            strategy = self._active_strategy(tuple(active.values()))
            positions = {item.symbol: item for item in request.portfolio.positions}
            items: list[CandidateInput] = []
            failures: dict[str, str] = {}
            for symbol in symbols:
                provider = self._provider(request.source, request.tushare_token)
                position = positions.get(symbol)
                industry = self._industry(request.portfolio, symbol)
                try:
                    overlay = None
                    if industry:
                        snapshot = await load_macro_snapshot()
                        if snapshot_is_verified_current(snapshot, reference_date=request.end_date):
                            overlay = build_macro_overlay(
                                industry, analyze_macro_snapshot(snapshot)
                            )
                    result = await run_research(
                        provider,
                        symbol,
                        request.start_date,
                        request.end_date,
                        request.adjustment,
                        position=position,
                        macro_overlay=overlay,
                    )
                    name = (
                        position.name
                        if position is not None
                        else self._name(request.portfolio, symbol)
                    )
                    items.append(
                        CandidateInput(
                            symbol,
                            name,
                            result.strategy,
                            result.investment_advice,
                            industry,
                        )
                    )
                except Exception as error:
                    failures[symbol] = str(error)[:300]
                finally:
                    close = getattr(provider, "close", None)
                    if callable(close):
                        close()
            ranked = rank_strategy_candidates(items, strategy)
            return ToolResult(
                f"扫描 {len(symbols)} 只标的，得到 {len(ranked)} 个候选，失败 {len(failures)} 个",
                {
                    "candidates": [asdict(item) for item in ranked],
                    "failures": failures,
                    "strategy": strategy.name if strategy is not None else "platform-default",
                },
            )

        async def web_search(arguments: dict[str, Any]) -> ToolResult:
            if search is None:
                raise ValueError("当前处于离线演示模式，无法执行实时联网搜索")
            query = " ".join(str(arguments["query"]).split())[:500]
            results = await search.search(query, max_results=int(arguments.get("max_results", 5)))
            return ToolResult(
                f"联网搜索返回 {len(results)} 条可追溯来源",
                {
                    "query": query,
                    "searched_at_runtime": True,
                    "results": results,
                    "research_trace": [
                        {
                            "stage": "web-evidence",
                            "title": "联网检索最新公开来源",
                            "status": "succeeded",
                            "summary": f"返回 {len(results)} 个原始页面链接",
                            "skills": ["public-web-search"],
                            "agent": "web-research-agent",
                        }
                    ],
                },
            )

        artifacts = run_root / "artifacts"

        async def write_report(arguments: dict[str, Any]) -> ToolResult:
            name = str(arguments["name"])
            if not _SAFE_TEXT_ARTIFACT.fullmatch(name):
                raise ValueError("文本写入只允许安全的 .md/.txt/.json 文件")
            content = str(arguments["content"])
            path = self._write_artifact(artifacts, name, content)
            return ToolResult(
                f"已写入工作区成果 {path.name}",
                {"name": path.name, "size_bytes": path.stat().st_size},
                artifact_id=path.name,
            )

        async def render_html(arguments: dict[str, Any]) -> ToolResult:
            name = str(arguments["name"])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.html", name):
                raise ValueError("HTML 成果文件名必须是安全的 .html 文件")
            content = render_investment_html(
                title=str(arguments["title"])[:200],
                goal=request.question,
                content=str(arguments["content"]),
                skills=tuple(active),
                data_source=request.source,
            )
            path = self._write_artifact(artifacts, name, content)
            return ToolResult(
                f"已安全渲染 HTML 成果 {path.name}",
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "security": "escaped-no-script-no-remote-assets",
                },
                artifact_id=path.name,
            )

        async def list_files(_: dict[str, Any]) -> ToolResult:
            files = [asdict(item) for item in self._artifacts(artifacts)]
            return ToolResult(f"工作区当前有 {len(files)} 个成果文件", {"files": files})

        async def read_file(arguments: dict[str, Any]) -> ToolResult:
            name = str(arguments["name"])
            if not _SAFE_TEXT_ARTIFACT.fullmatch(name):
                raise ValueError("文本读取只允许 .md/.txt/.json 成果")
            path = self._artifact_path(artifacts, name)
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"工作区成果不存在：{name}")
            data = path.read_bytes()
            if len(data) > 256 * 1024:
                raise ValueError("工作区成果超过单次读取上限")
            return ToolResult(
                f"已读取工作区成果 {name}",
                {"name": name, "content": data.decode("utf-8")},
                artifact_id=name,
            )

        async def workspace_list_files(arguments: dict[str, Any]) -> ToolResult:
            value = editor.list(
                arguments.get("path", "."),
                recursive=bool(arguments.get("recursive", False)),
            )
            items = value["items"]
            assert isinstance(items, list)
            return ToolResult(f"已列出工作区 {len(items)} 个项目", value)

        async def workspace_read_file(arguments: dict[str, Any]) -> ToolResult:
            value = editor.read(
                arguments["file_path"],
                offset=int(arguments.get("offset", 1)),
                limit=int(arguments.get("limit", 400)),
            )
            lines = value["lines"]
            assert isinstance(lines, list)
            return ToolResult(f"已读取工作区文件 {value['path']} 的 {len(lines)} 行", value)

        async def workspace_write_file(arguments: dict[str, Any]) -> ToolResult:
            value = editor.write(arguments["file_path"], arguments["content"])
            operation = "更新" if value["operation"] == "update" else "创建"
            return ToolResult(
                f"已{operation}工作区文件 {value['path']}",
                value,
                artifact_id=str(value["path"]),
            )

        async def workspace_edit_file(arguments: dict[str, Any]) -> ToolResult:
            value = editor.edit(
                arguments["file_path"],
                arguments["old_string"],
                arguments["new_string"],
                replace_all=bool(arguments.get("replace_all", False)),
            )
            return ToolResult(
                f"已编辑工作区文件 {value['path']} 并替换 {value['replacements']} 处",
                value,
                artifact_id=str(value["path"]),
            )

        async def run_bash(arguments: dict[str, Any]) -> ToolResult:
            value = await shell.run(
                arguments["command"],
                timeout_seconds=int(arguments.get("timeout_seconds", 30)),
            )
            return ToolResult(
                f"持久 Shell 执行完成，退出码 {value.exit_code}",
                asdict(value),
            )

        self._register(registry, "skills.list", "列出可用内置与用户 Skill", {}, list_skills)
        self._register(
            registry,
            "skills.activate",
            "按需加载一个 Skill 的完整说明；用户同名 Skill 优先",
            {"name": {"type": "string", "minLength": 1, "maxLength": 80}},
            activate_skill,
        )
        self._register(
            registry, "portfolio.snapshot", "读取脱敏后的本地持仓与自选池", {}, portfolio_snapshot
        )
        self._register(registry, "portfolio.list", "读取本机完整持仓和自选", {}, portfolio_list)
        self._register(
            registry,
            "portfolio.upsert",
            "在本机添加或更新一条持仓；不连接券商",
            {
                "symbol": {"type": "string", "pattern": "^[0-9]{6}(?:\\.(?:SH|SZ|BJ))?$"},
                "quantity": {"type": "number", "exclusiveMinimum": 0},
                "cost_price": {"type": "number", "exclusiveMinimum": 0},
                "name": {"type": "string", "maxLength": 40},
                "industry": {"type": "string", "maxLength": 80},
            },
            portfolio_upsert,
            risk=RiskLevel.MEDIUM,
            side_effect=True,
            required=("symbol",),
        )
        self._register(
            registry,
            "portfolio.remove",
            "从本机持仓删除一条记录；不执行卖出交易",
            {"symbol": {"type": "string", "pattern": "^[0-9]{6}(?:\\.(?:SH|SZ|BJ))?$"}},
            portfolio_remove,
            risk=RiskLevel.MEDIUM,
            side_effect=True,
        )
        self._register(
            registry, "evidence.current", "读取当前证券的确定性分析证据", {}, current_evidence
        )
        self._register(
            registry,
            "macro.snapshot",
            "读取结构化宏观基线并联网核验国家统计局等官方发布页的时效",
            {},
            macro_snapshot,
        )
        self._register(
            registry,
            "market.analyze",
            "获取历史行情并运行多周期指标、共振与投资规则",
            {"symbol": {"type": "string", "pattern": "^[0-9]{6}(?:\\.(?:SH|SZ|BJ))?$"}},
            analyze_security,
            risk=RiskLevel.MEDIUM,
        )
        self._register(
            registry,
            "research.screen",
            "扫描本地候选池并按激活策略过滤排序",
            {},
            screen_candidates,
            risk=RiskLevel.MEDIUM,
        )
        self._register(
            registry,
            "web.search",
            "联网查询最新公开网页并返回可点击的来源链接；高级用户可选 Tavily",
            {
                "query": {"type": "string", "minLength": 2, "maxLength": 500},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            web_search,
            risk=RiskLevel.MEDIUM,
        )
        self._register(
            registry,
            "workspace.write",
            "在本轮隔离工作区写入 Markdown、文本或 JSON 成果",
            {
                "name": {"type": "string", "pattern": _SAFE_TEXT_ARTIFACT.pattern},
                "content": {"type": "string", "maxLength": 100_000},
            },
            write_report,
            side_effect=True,
        )
        self._register(
            registry,
            "workspace.render_html",
            "把纯文本或 Markdown 子集安全渲染为无脚本、无远程资源的本地 HTML 成果",
            {
                "name": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.html$",
                },
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "content": {"type": "string", "maxLength": 100_000},
            },
            render_html,
            side_effect=True,
        )
        self._register(registry, "workspace.list", "列出本轮工作区成果", {}, list_files)
        self._register(
            registry,
            "workspace.read",
            "读取本轮工作区中的一个文本成果",
            {"name": {"type": "string", "pattern": _SAFE_TEXT_ARTIFACT.pattern}},
            read_file,
        )
        self._register(
            registry,
            "list_files",
            "列出用户选定工作区内的文件；不跟随符号链接，最多返回 200 项",
            {
                "path": {"type": "string", "maxLength": 500},
                "recursive": {"type": "boolean"},
            },
            workspace_list_files,
            required=(),
        )
        self._register(
            registry,
            "read",
            "分页读取用户选定工作区内的 UTF-8 文本，为后续安全编辑建立版本观察",
            {
                "file_path": {"type": "string", "minLength": 1, "maxLength": 500},
                "offset": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            workspace_read_file,
            required=("file_path",),
        )
        self._register(
            registry,
            "write",
            "在用户选定工作区创建或覆盖 UTF-8 文本；覆盖已有文件前必须先 read 且版本未变",
            {
                "file_path": {"type": "string", "minLength": 1, "maxLength": 500},
                "content": {"type": "string", "maxLength": 262144},
            },
            workspace_write_file,
            risk=RiskLevel.MEDIUM,
            side_effect=True,
            required=("file_path", "content"),
        )
        self._register(
            registry,
            "edit",
            "在用户选定工作区中做精确文本替换；要求先 read 且文件版本未变",
            {
                "file_path": {"type": "string", "minLength": 1, "maxLength": 500},
                "old_string": {"type": "string", "minLength": 1, "maxLength": 131072},
                "new_string": {"type": "string", "maxLength": 131072},
                "replace_all": {"type": "boolean"},
            },
            workspace_edit_file,
            risk=RiskLevel.MEDIUM,
            side_effect=True,
            required=("file_path", "old_string", "new_string"),
        )
        self._register(
            registry,
            "bash",
            "在本轮持久 Bash 中执行命令；会话保留 cwd 和环境，"
            "macOS Seatbelt 将文件效果限制到用户选定工作区",
            {
                "command": {"type": "string", "minLength": 1, "maxLength": 8000},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            run_bash,
            risk=RiskLevel.HIGH,
            side_effect=True,
            required=("command",),
        )
        return registry, search, shell

    async def _next_action(
        self,
        request: InvestmentAgentRunRequest,
        registry: ToolRegistry,
        active: dict[str, SkillPackage],
        observations: list[dict[str, Any]],
        remaining_steps: int,
        allowed_tool_names: tuple[str, ...],
    ) -> InvestmentAgentAction:
        # Portfolio mutations are always parsed and executed locally. Quantities and
        # cost prices never enter a remote planner observation.
        if self.model is None or request.intent == "manage_portfolio":
            return self._deterministic_action(request, observations, active)
        tools = [
            self._model_tool(spec)
            for spec in registry.visible_specs(
                PolicySnapshot(allowed_tools=allowed_tool_names)
            )
        ]
        context = {
            "intent": request.intent,
            "symbol": request.symbol,
            "data_source": request.source,
            "date_range": [request.start_date.isoformat(), request.end_date.isoformat()],
            "conversation_summary": request.conversation_summary[:3_500],
            "attachment_context": request.attachment_context[:24_000],
            "investment_preferences": request.memory.prompt_payload() if request.memory else {},
            "web_search_available": request.source != "demo" or bool(request.web_search_api_key),
            "evidence_available": request.evidence is not None,
            "skill_selection_mode": request.skill_selection_mode,
            "workspace_policy": (
                f"selected local workspace; {request.workspace_permission}; "
                "persistent shell and file editor are sandboxed to that workspace"
            ),
        }
        try:
            action = await self.model.choose_investment_action(
                goal=request.question,
                context=context,
                tools=tools,
                observations=observations[-8:],
                active_skills=[
                    {
                        **self._skill_reference(item),
                        "instructions": item.instructions[:8_000],
                    }
                    for item in active.values()
                ],
                remaining_steps=remaining_steps,
            )
            if (
                request.skill_selection_mode == "explicit"
                and action.tool_name == "skills.activate"
                and str(action.arguments.get("name", "")) not in active
            ):
                return self._deterministic_action(request, observations, active)
            return action
        except ModelServiceError:
            return self._deterministic_action(request, observations, active)

    def _deterministic_action(
        self,
        request: InvestmentAgentRunRequest,
        observations: Sequence[dict[str, Any]],
        active: dict[str, SkillPackage],
    ) -> InvestmentAgentAction:
        called = [str(item.get("tool")) for item in observations]
        if request.intent == "manage_portfolio":
            return self._portfolio_action(request, called, observations, active)
        desired_skill = (
            "macro-investment-synthesis"
            if any(word in request.question for word in ("宏观", "资本三流", "成本转嫁"))
            else "investment-decision-engine"
            if request.intent in {"screen_candidates", "design_strategy", "analyze_security"}
            else None
        )
        if (
            desired_skill is not None
            and request.skill_selection_mode == "auto"
            and desired_skill not in active
            and "skills.activate" not in called
        ):
            return InvestmentAgentAction(
                "tool",
                "skills.activate",
                {"name": desired_skill},
                reason="按任务需要渐进加载 Skill",
            )
        if request.intent == "manage_skills" and "skills.list" not in called:
            return InvestmentAgentAction("tool", "skills.list", reason="展示可替换 Skill")
        if request.intent == "screen_candidates":
            if "portfolio.snapshot" not in called:
                return InvestmentAgentAction("tool", "portfolio.snapshot", reason="读取候选范围")
            if "research.screen" not in called:
                return InvestmentAgentAction("tool", "research.screen", reason="执行候选筛选")
        if request.intent == "analyze_security" and request.symbol:
            if "market.analyze" not in called:
                return InvestmentAgentAction(
                    "tool", "market.analyze", {"symbol": request.symbol}, reason="获取证券证据"
                )
        if request.intent == "explain_holding" and request.evidence is not None:
            if "evidence.current" not in called:
                return InvestmentAgentAction("tool", "evidence.current", reason="读取持仓证据")
        if any(word in request.question for word in ("宏观", "资本三流", "成本转嫁")):
            if "macro.snapshot" not in called:
                return InvestmentAgentAction("tool", "macro.snapshot", reason="核对宏观数据口径")
        if _WEB_TRIGGER.search(request.question) and "web.search" not in called:
            return InvestmentAgentAction(
                "tool",
                "web.search",
                {"query": request.question, "max_results": 5},
                reason="查询最新公开来源",
            )
        return InvestmentAgentAction(
            "final", content=self._local_synthesis(request, observations, active)
        )

    def _portfolio_action(
        self,
        request: InvestmentAgentRunRequest,
        called: Sequence[str],
        observations: Sequence[dict[str, Any]],
        active: dict[str, SkillPackage],
    ) -> InvestmentAgentAction:
        question = request.question
        remove = any(word in question for word in ("删除", "移除"))
        mutate = remove or any(
            word in question for word in ("添加", "新增", "记录", "修改", "更新", "调整")
        )
        if not mutate:
            if "portfolio.list" not in called:
                return InvestmentAgentAction("tool", "portfolio.list", reason="读取本地持仓")
            return InvestmentAgentAction(
                "final", content=self._local_synthesis(request, observations, active)
            )
        if not request.symbol:
            return InvestmentAgentAction(
                "clarify",
                content=(
                    "请在持仓调整指令中写明证券代码，例如 `添加持仓 600050.SH 1000 股，成本 4.20`。"
                ),
            )
        if remove:
            if "portfolio.remove" not in called:
                return InvestmentAgentAction(
                    "tool",
                    "portfolio.remove",
                    {"symbol": request.symbol},
                    reason="按用户明确指令删除本地持仓记录",
                )
            return InvestmentAgentAction(
                "final", content=self._local_synthesis(request, observations, active)
            )
        arguments: dict[str, Any] = {"symbol": request.symbol}
        quantity = self._number_after(
            question,
            r"(?:数量\s*(?:为|是|=)?\s*)?(\d+(?:\.\d+)?)\s*(股|份|手)",
        )
        if quantity is not None:
            arguments["quantity"] = quantity[0] * (100 if quantity[1] == "手" else 1)
        elif plain_quantity := re.search(r"数量\s*(?:为|是|=|到)?\s*(\d+(?:\.\d+)?)", question):
            arguments["quantity"] = float(plain_quantity.group(1))
        cost = re.search(r"(?:成本(?:价)?|均价)\s*(?:为|是|=|到)?\s*(\d+(?:\.\d+)?)", question)
        if cost:
            arguments["cost_price"] = float(cost.group(1))
        for key, label in (("name", "名称"), ("industry", "行业")):
            match = re.search(rf"{label}\s*(?:为|是|=)?\s*([^，,。；;\s]{{1,40}})", question)
            if match:
                arguments[key] = match.group(1)
        current = (
            self.portfolio_manager.load().position(request.symbol)
            if self.portfolio_manager is not None
            else request.portfolio.position(request.symbol)
        )
        if current is None and not {"quantity", "cost_price"}.issubset(arguments):
            return InvestmentAgentAction(
                "clarify",
                content=(
                    "新增持仓需要数量和成本价。请完整输入，例如 "
                    f"`添加持仓 {request.symbol} 1000 股，成本 4.20`。"
                ),
            )
        if "portfolio.upsert" not in called:
            return InvestmentAgentAction(
                "tool",
                "portfolio.upsert",
                arguments,
                reason="按用户明确字段写入本地持仓",
            )
        return InvestmentAgentAction(
            "final", content=self._local_synthesis(request, observations, active)
        )

    @staticmethod
    def _number_after(text: str, pattern: str) -> tuple[float, str] | None:
        match = re.search(pattern, text)
        return (float(match.group(1)), match.group(2)) if match else None

    def _local_synthesis(
        self,
        request: InvestmentAgentRunRequest,
        observations: Sequence[dict[str, Any]],
        active: dict[str, SkillPackage],
    ) -> str:
        by_tool = {str(item.get("tool")): item for item in observations}
        portfolio_change = by_tool.get("portfolio.upsert") or by_tool.get("portfolio.remove")
        if portfolio_change:
            if not portfolio_change.get("ok"):
                return (
                    "## 本地持仓未调整\n\n"
                    f"{portfolio_change.get('error', '持仓指令校验失败')}\n\n"
                    "没有连接券商，也没有执行任何交易。"
                )
            data = portfolio_change.get("data", {})
            position = data.get("position", {}) if isinstance(data, dict) else {}
            if isinstance(data, dict) and data.get("action") == "removed":
                return (
                    "## 已删除本地持仓记录\n\n"
                    f"- 证券：`{data.get('symbol', request.symbol or '—')}`\n"
                    f"- 当前本地持仓数：{data.get('position_count', '—')}\n"
                    "- 影响范围：仅修改本机持仓 JSON；没有连接券商，也没有执行卖出交易。"
                )
            return (
                f"## 已{'更新' if data.get('action') == 'updated' else '添加'}本地持仓\n\n"
                "| 字段 | 当前记录 |\n| --- | --- |\n"
                f"| 证券 | `{position.get('symbol', request.symbol or '—')}` |\n"
                f"| 名称 | {position.get('name') or '未填写'} |\n"
                f"| 数量 | {position.get('quantity', '—')} |\n"
                f"| 成本价 | {position.get('cost_price', '—')} |\n"
                f"| 行业 | {position.get('industry') or '未分类'} |\n\n"
                "记录只保存在本机；没有连接券商，也没有自动下单。"
            )
        portfolio = by_tool.get("portfolio.list")
        if portfolio and portfolio.get("ok"):
            data = portfolio.get("data", {})
            positions = data.get("positions", []) if isinstance(data, dict) else []
            rows = [
                f"| `{item.get('symbol', '—')}` | {item.get('name') or '—'} | "
                f"{item.get('quantity', '—')} | {item.get('cost_price', '—')} | "
                f"{item.get('industry') or '未分类'} |"
                for item in positions
                if isinstance(item, dict)
            ]
            return (
                "## 本地持仓\n\n"
                "| 证券 | 名称 | 数量 | 成本价 | 行业 |\n"
                "| --- | --- | ---: | ---: | --- |\n"
                + ("\n".join(rows) if rows else "| — | 当前没有持仓 | — | — | — |")
                + "\n\n以上信息来自本机持仓文件，没有发送给远程模型。"
            )
        evidence = by_tool.get("evidence.current")
        if evidence and evidence.get("ok") and request.evidence is not None:
            return local_advisor_answer(request.evidence, request.question)
        research = by_tool.get("market.analyze")
        if research and research.get("ok"):
            data = research.get("data", {})
            if not isinstance(data, dict):
                data = {}
            snapshot = data.get("snapshot", {})
            advice = data.get("advice", {})
            strategy = data.get("strategy", {})
            market = data.get("market_context", {})
            if not isinstance(snapshot, dict):
                snapshot = {}
            if not isinstance(advice, dict):
                advice = {}
            if not isinstance(strategy, dict):
                strategy = {}
            if not isinstance(market, dict):
                market = {}
            raw_forecasts = data.get("forecast", []) if isinstance(data, dict) else []
            forecast_lines = [
                f"| {item.get('trading_days')} 日 | "
                f"{item.get('price_range_low'):.2f}–{item.get('price_range_high'):.2f} | "
                f"{item.get('direction_label', item.get('direction', '未知'))} | "
                f"{item.get('basis_label', '规则情景，不是统计胜率')} |"
                for item in raw_forecasts
                if isinstance(item, dict)
                and isinstance(item.get("price_range_low"), (int, float))
                and isinstance(item.get("price_range_high"), (int, float))
            ]
            indicators = snapshot.get("indicators", {})
            if not isinstance(indicators, dict):
                indicators = {}
            indicator_rows = [
                ("趋势", "MA5 / MA20 / MA60", ("MA5", "MA20", "MA60")),
                ("MACD", "DIF / DEA / 柱", ("DIF", "DEA", "MACD")),
                ("动量", "RSI6 / RSI12 / RSI24", ("RSI6", "RSI12", "RSI24")),
                ("波动", "ATR20 / BOLL中 / BOLL上", ("ATR20", "BOLL_MID", "BOLL_UPPER")),
                ("时机", "WR6 / WR10", ("WR6", "WR10")),
            ]
            indicator_lines = [
                f"| {group} | {labels} | "
                + " / ".join(
                    "—" if indicators.get(key) is None else str(indicators.get(key)) for key in keys
                )
                + " |"
                for group, labels, keys in indicator_rows
            ]
            macd = strategy.get("macd", {})
            wr = strategy.get("wr", {})
            if not isinstance(macd, dict):
                macd = {}
            if not isinstance(wr, dict):
                wr = {}
            timeframe_lines: list[str] = []
            for key, label in (("monthly", "月线"), ("weekly", "周线"), ("daily", "日线")):
                macd_item = macd.get(key, {})
                wr_item = wr.get(key, {})
                if isinstance(macd_item, dict) and isinstance(wr_item, dict):
                    timeframe_lines.append(
                        f"| {label} | {macd_item.get('as_of', '—')} | "
                        f"{macd_item.get('phase_label', '—')} | "
                        f"{wr_item.get('value', '—')} / {wr_item.get('zone_label', '—')} |"
                    )
            decision_lines = [
                f"{index}. **{item.get('title', '规则门控')}** — "
                f"{item.get('status_label', item.get('status', '—'))}："
                f"{item.get('summary', '')}"
                for index, item in enumerate(strategy.get("decision_path", []), start=1)
                if isinstance(item, dict)
            ]
            action_low = advice.get("action_zone_low")
            action_high = advice.get("action_zone_high")
            action_zone = (
                f"{float(action_low):.2f}–{float(action_high):.2f}"
                if isinstance(action_low, (int, float)) and isinstance(action_high, (int, float))
                else "尚未形成可执行区间"
            )
            evidence_lines = "\n".join(
                f"- {item}" for item in advice.get("evidence", []) if str(item).strip()
            )
            thesis_lines = "\n".join(
                f"- {item}" for item in advice.get("thesis", []) if str(item).strip()
            )
            risk_lines = "\n".join(
                f"- {item}" for item in advice.get("risk_controls", []) if str(item).strip()
            )
            limitation_lines = "\n".join(
                f"- {item}" for item in advice.get("limitations", []) if str(item).strip()
            )
            source_line = (
                f"- **来源：** {data.get('source', '未知')}"
                f"（{snapshot.get('source_kind', '未知口径')}）\n"
            )
            date_line = (
                f"- **截止：** {data.get('as_of', '未知')} · "
                f"{snapshot.get('bars', '—')} 根日 K · {data.get('adjustment', '—')}\n"
            )
            formula_line = (
                f"- **公式：** `{snapshot.get('formula_version', '—')}` · "
                f"缓存 {snapshot.get('cache_status', '—')}\n\n"
            )
            confidence_line = (
                f"| 置信度 | {data.get('confidence', '—')}/100"
                f"（技术 {advice.get('technical_confidence', '—')}；"
                f"市场 {advice.get('market_confidence_adjustment', 0):+}；"
                f"宏观 {advice.get('macro_confidence_adjustment', 0):+}） |\n"
            )
            timeframe_header = (
                "\n\n### 月 / 周 / 日多周期\n\n"
                "| 周期 | 已完成周期截止 | MACD 状态 | WR / 区域 |\n"
                "| --- | --- | --- | --- |\n"
            )
            forecast_header = (
                "### 规则情景区间\n\n| 周期 | 价格区间 | 方向 | 口径 |\n| --- | --- | --- | --- |\n"
            )
            current_price = advice.get("current_price", snapshot.get("latest_close", "—"))
            return (
                f"## {data.get('symbol', request.symbol or '证券')} 完整投资研究\n\n"
                "### 数据血缘\n\n" + source_line + date_line + formula_line + "### 当前决策\n\n"
                "| 项目 | 结果 |\n| --- | --- |\n"
                f"| 动作 | **{data.get('action', '未知')}** |\n"
                + confidence_line
                + f"| 当前价格 | {current_price} |\n"
                f"| 执行区间 | {action_zone} |\n"
                f"| 多周期方向 | {data.get('direction', '未提供')} |\n"
                f"| WR 时机 | {data.get('timing', '未提供')} |\n"
                f"| 市场门控 | {data.get('market_priority', '未提供')} |\n\n"
                "### 为什么是这个结论\n\n"
                + (thesis_lines or "- 当前没有返回研究论点。")
                + "\n\n#### 可核验证据\n\n"
                + (evidence_lines or "- 当前没有返回独立证据。")
                + "\n\n### 关键指标\n\n| 分组 | 指标 | 当前值 |\n| --- | --- | ---: |\n"
                + "\n".join(indicator_lines)
                + timeframe_header
                + ("\n".join(timeframe_lines) or "| — | — | 暂无 | 暂无 |")
                + "\n\n### 五步决策门控\n\n"
                + ("\n".join(decision_lines) or "1. 当前没有返回门控明细。")
                + "\n\n### 触发与失效条件\n\n"
                f"> {data.get('invalidation_condition', '请查看成果报告')}\n\n"
                + (forecast_header + "\n".join(forecast_lines) + "\n\n" if forecast_lines else "")
                + "### 风险控制\n\n"
                + (risk_lines or "- 依据个人风险预算控制单笔暴露。")
                + "\n\n### 方法边界\n\n"
                + (limitation_lines or "- 历史规则信号不保证未来收益。")
                + "\n\n完整分工、实际 Skill、工具调用和证据文件见右侧运行过程；"
                + "HTML 成果已保存到本轮隔离工作区。"
            )
        screen = by_tool.get("research.screen")
        if screen and not screen.get("ok"):
            return (
                "## 候选池筛选未完成\n\n"
                f"{screen.get('error', '候选池研究工具执行失败')}。\n\n"
                "请检查本地持仓/自选池、行情源和当前 Skill 的 `strategy.json` 后重试。"
            )
        if screen and screen.get("ok"):
            data = screen.get("data", {})
            candidates = data.get("candidates", []) if isinstance(data, dict) else []
            lines = [
                f"{index}. **{item.get('symbol')}** {item.get('name', '')} · "
                f"{item.get('action_label') or item.get('timing_label')} · "
                f"置信度 {item.get('confidence', 0)}/100"
                for index, item in enumerate(candidates[:10], start=1)
                if isinstance(item, dict)
            ]
            return (
                "## 候选池筛选完成\n\n"
                + ("\n".join(lines) if lines else "当前策略条件下没有通过平台风险门的候选。")
                + f"\n\n筛选策略：`{data.get('strategy', 'platform-default')}`。"
                "结果来自历史数据和规则过滤，不代表未来收益。"
            )
        macro = by_tool.get("macro.snapshot")
        if macro and macro.get("ok"):
            data = macro.get("data", {})
            if not isinstance(data, dict):
                data = {}
            validity = data.get("validity", {})
            if not isinstance(validity, dict):
                validity = {}
            analysis = data.get("analysis", {})
            if not isinstance(analysis, dict):
                analysis = {}
            flow = analysis.get("capital_flow", {})
            transfer = analysis.get("cost_transfer", {})
            view = analysis.get("investment_view", {})
            if not isinstance(flow, dict):
                flow = {}
            if not isinstance(transfer, dict):
                transfer = {}
            if not isinstance(view, dict):
                view = {}
            conclusions = view.get("decision_summary", [])
            if not isinstance(conclusions, (list, tuple)):
                conclusions = []
            plans = view.get("allocation_plans", [])
            if not isinstance(plans, (list, tuple)):
                plans = []
            default_profile = view.get("default_allocation_profile")
            allocation = next(
                (
                    item
                    for item in plans
                    if isinstance(item, dict) and item.get("profile") == default_profile
                ),
                {},
            )
            allocation_targets = (
                allocation.get("targets", []) if isinstance(allocation, dict) else []
            )
            allocation_lines = [
                f"| {item.get('label', '—')} | {item.get('target_pct', '—')}% | "
                f"{item.get('minimum_pct', '—')}%–{item.get('maximum_pct', '—')}% | "
                f"{item.get('action_label', '—')} | {item.get('vehicles', '—')} |"
                for item in allocation_targets
                if isinstance(item, dict)
            ]
            checks = validity.get("source_checks", [])
            source_lines = [
                f"- {item.get('name', '官方来源')}："
                f"{'已核验' if item.get('status') == 'succeeded' else '核验失败'}"
                f" · 最近页面日期 {item.get('latest_published_on') or '未识别'}"
                f" · {item.get('url', '')}"
                for item in checks
                if isinstance(item, dict)
            ]
            web_observation = by_tool.get("web.search", {})
            web_data = (
                web_observation.get("data", {})
                if isinstance(web_observation, dict) and web_observation.get("ok")
                else {}
            )
            latest_results = web_data.get("results", []) if isinstance(web_data, dict) else []
            latest_lines = [
                f"- [{item.get('title', '公开来源')}]({item.get('url', '')})："
                f"{item.get('content', '')}"
                for item in latest_results[:5]
                if isinstance(item, dict)
            ]
            sector_lines = [
                f"| {item.get('sector', '—')} | {item.get('stance_label', '—')} | "
                f"{item.get('confidence', '—')}/100 | {item.get('rationale', '—')} | "
                f"{item.get('confirmation', '—')} | {item.get('risk', '—')} |"
                for item in view.get("sectors", [])
                if isinstance(item, dict)
            ]
            decision_allowed = bool(validity.get("current_decision_allowed", False))
            title = (
                "## 宏观官方发布核验通过" if decision_allowed else "## 宏观时效门禁：当前结论不可用"
            )
            boundary = (
                "当前允许使用下方结构化宏观结论。"
                if decision_allowed
                else "系统已阻止旧结论影响当前仓位、行业偏配和个股置信度；"
                "以下评分和配置只用于解释旧模型，不代表今天应执行。"
            )
            score_values = (
                ("资本流量", flow.get("volume_score", "—"), flow.get("volume_label", "—")),
                (
                    "资本流向",
                    flow.get("direction_score", "—"),
                    flow.get("direction_label", "—"),
                ),
                ("资本流速", flow.get("speed_score", "—"), flow.get("speed_label", "—")),
                (
                    "实体传导",
                    flow.get("transmission_score", "—"),
                    flow.get("transmission_label", "—"),
                ),
                (
                    "成本转嫁压力",
                    transfer.get("pressure_score", "—"),
                    transfer.get("pressure_label", "—"),
                ),
                (
                    "权益风险偏好",
                    view.get("risk_appetite_score", "—"),
                    view.get("risk_appetite_label", "—"),
                ),
            )
            score_lines = "\n".join(
                f"| {label} | {score} | {summary} |" for label, score, summary in score_values
            )
            allocation_label = (
                str(allocation.get("label", "—")) if isinstance(allocation, dict) else "—"
            )
            return (
                title + "\n\n### 数据与时效门禁\n\n"
                f"- 结构化基线：`{data.get('version', '未知')}`\n"
                f"- 数据截止：**{data.get('as_of', '未知')}**\n"
                f"- 门禁状态：**{validity.get('status_label', '未通过')}**\n"
                f"- 判断依据：{validity.get('reason', '联网核验不足')}\n\n"
                f"> {boundary}\n\n"
                "### 六项宏观仪表盘\n\n"
                "| 维度 | 分数 | 判断 |\n| --- | ---: | --- |\n" + score_lines + "\n\n"
                "### 配置含义\n\n"
                f"**权益暴露：** {view.get('equity_exposure', '未知')}\n\n"
                + ("\n".join(f"- {item}" for item in conclusions) or "- 暂无配置摘要")
                + "\n\n### 长期配置基线\n\n"
                f"风险画像：**{allocation_label}**。"
                "当前门禁未通过时，本表仅作历史模型回放。\n\n"
                "| 资产桶 | 目标 | 允许区间 | 动作 | 实现方式 |\n"
                "| --- | ---: | --- | --- | --- |\n"
                + ("\n".join(allocation_lines) or "| — | — | — | — | 暂无 |")
                + "\n\n### 行业配置与确认条件\n\n"
                "| 行业 | 建议 | 置信度 | 逻辑 | 确认条件 | 主要风险 |\n"
                "| --- | --- | ---: | --- | --- | --- |\n"
                + ("\n".join(sector_lines) or "| — | — | — | 暂无 | 暂无 | 暂无 |")
                + "\n\n### 官方发布页核验记录\n\n"
                + ("\n".join(source_lines) if source_lines else "- 暂无可用核验记录")
                + (
                    "\n\n### 同轮最新公开信息检索\n\n" + "\n".join(latest_lines)
                    if latest_lines
                    else ""
                )
                + "\n\n### 数据边界\n\n"
                "- 联网核验的是官方发布页日期，不把网页片段冒充结构化指标。\n"
                "- 发现基线之后的新发布、快照超龄或核验不足时，当前建议立即失效。\n"
                "- 完整资本三流路径、成本转嫁链、长期配置和 32 项结构化指标可在“宏观研究”页面审阅。"
            )
        web = by_tool.get("web.search")
        if web:
            if not web.get("ok"):
                return (
                    "## 暂时无法执行实时联网研究\n\n"
                    f"{web.get('error', '公开搜索服务暂时不可用')}。"
                    "请检查网络后重试，或提供具体公开来源。"
                    "系统不会把模型记忆冒充实时搜索结果。"
                )
            data = web.get("data", {})
            results = data.get("results", []) if isinstance(data, dict) else []
            lines = [
                f"- [{item.get('title', '来源')}]({item.get('url', '')})：{item.get('content', '')}"
                for item in results
                if isinstance(item, dict)
            ]
            return (
                "## 联网研究结果\n\n"
                + ("\n".join(lines) if lines else "没有检索到可用公开来源。")
                + "\n\n以上内容需结合原始页面发布时间和证券历史证据复核。"
            )
        selected_for_execution = [
            package for package in active.values() if package.summary.name != "html-research-report"
        ]
        if (
            request.skill_selection_mode == "explicit"
            and selected_for_execution
            and any(
                phrase in request.question
                for phrase in ("使用", "采用", "按照", "基于", "根据", "用我", "选择的 Skill")
            )
        ):
            sections: list[str] = []
            for package in selected_for_execution:
                instruction_lines: list[str] = []
                for raw_line in package.instructions.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s*)", "", line).strip()
                    if line:
                        instruction_lines.append(f"- {line[:500]}")
                    if len(instruction_lines) >= 12:
                        break
                sections.append(
                    f"### `{package.summary.name}`\n\n"
                    f"来源：{package.summary.provider} · v{package.summary.version}\n\n"
                    + ("\n".join(instruction_lines) or "- 该 Skill 没有可展示的正文规则。")
                )
            return (
                "## 已按本轮选择的 Skill 读取规则\n\n"
                + "\n\n".join(sections)
                + "\n\n这些规则已进入本轮 Trace；平台只把它们作为研究约束，"
                "不会执行任意代码、不连接券商、不自动下单。"
            )
        if request.intent == "manage_skills":
            listing = by_tool.get("skills.list", {}).get("data", {})
            skills = listing.get("skills", []) if isinstance(listing, dict) else []
            return "## 当前可用 Skill\n\n" + "\n".join(
                f"- `{item.get('name')}` · {item.get('provider')} · {item.get('description')}"
                for item in skills
                if isinstance(item, dict)
            )
        context = AdvisorConversationContext(
            summary=request.conversation_summary,
            memory=request.memory,
            active_skills=tuple(active.values()),
        )
        answer = local_investment_conversation_answer(request.question, request.intent, context)
        if request.attachment_context:
            answer += (
                "\n\n## 已读取的附件上下文\n\n"
                + request.attachment_context[:12_000]
                + "\n\n> 附件提取内容仅作为本轮研究上下文，关键数字请回看原文件。"
            )
        if request.attachment_warnings:
            answer += "\n\n## 附件提示\n\n" + "\n".join(
                f"- {item}" for item in request.attachment_warnings
            )
        return answer

    @staticmethod
    def _register(
        registry: ToolRegistry,
        name: str,
        description: str,
        properties: dict[str, Any],
        handler: Any,
        *,
        risk: RiskLevel = RiskLevel.LOW,
        side_effect: bool = False,
        required: tuple[str, ...] | None = None,
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                version="1.0.0",
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": list(required)
                    if required is not None
                    else [key for key in properties if key not in {"max_results"}],
                    "additionalProperties": False,
                },
                risk=risk,
                side_effect=side_effect,
                timeout_seconds=180,
                concurrency_safe=not side_effect and risk is RiskLevel.LOW,
            ),
            handler,
        )

    @staticmethod
    def _provider(source: str, tushare_token: str | None) -> MarketDataProvider:
        if source == "demo":
            return DemoMarketDataProvider()
        if source == "tushare":
            if not tushare_token:
                raise ValueError("尚未配置 Tushare Token")
            provider: MarketDataProvider = TushareProvider(tushare_token)
        else:
            provider = BaoStockProvider()
        return (
            CachedMarketDataProvider(provider, MarketDataCache())
            if market_cache_enabled()
            else provider
        )

    @staticmethod
    def _research_payload(result: ResearchResult) -> dict[str, Any]:
        nested_trace: list[dict[str, Any]] = [
            {
                "stage": "evidence",
                "title": "行情数据血缘",
                "status": "succeeded",
                "summary": (
                    f"{result.data.source} · 截止 {result.data.as_of.isoformat()} · "
                    f"{result.data.adjustment.value} · {len(result.data.bars)} 根日 K"
                ),
                "skills": ["a-share-market-data"],
                "agent": "market-data-agent",
                "evidence_path": "",
            },
            {
                "stage": "calculation",
                "title": "技术指标公式",
                "status": "succeeded",
                "summary": (
                    f"公式版本 {result.indicators.version}；本地计算 "
                    "MA、MACD、KDJ、RSI、ATR、BOLL、WR"
                ),
                "skills": ["technical-indicators"],
                "agent": "indicator-agent",
                "depends_on": ["market-data"],
                "evidence_path": "",
            },
        ]
        plan_tasks = result.plan.get("tasks", []) if isinstance(result.plan, dict) else []
        if isinstance(plan_tasks, list):
            for task in plan_tasks:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("id", ""))
                evidence_path = ""
                if result.workspace and task_id:
                    candidate = (
                        Path(result.workspace) / "tasks" / task_id / "output" / "result.json"
                    )
                    if candidate.is_file():
                        evidence_path = str(candidate)
                raw_skills = task.get("skills", [])
                skills = [str(item) for item in raw_skills] if isinstance(raw_skills, list) else []
                nested_trace.append(
                    {
                        "stage": "research-task",
                        "title": str(task.get("title") or task_id or "研究任务"),
                        "status": str(task.get("status", "unknown")),
                        "summary": str(task.get("summary") or task.get("error") or ""),
                        "skills": skills,
                        "agent": str(task.get("agent", "")),
                        "depends_on": list(task.get("depends_on", []))
                        if isinstance(task.get("depends_on", []), list)
                        else [],
                        "evidence_path": evidence_path,
                    }
                )
        for gate in result.strategy.decision_path:
            gate_evidence = "；".join(gate.evidence[:3])
            nested_trace.append(
                {
                    "stage": "decision-gate",
                    "title": f"规则门控 · {gate.title}",
                    "status": gate.status,
                    "summary": f"{gate.status_label}：{gate.summary}"
                    + (f"；证据：{gate_evidence}" if gate_evidence else ""),
                    "skills": ["multi-timeframe-macd-wr"],
                    "evidence_path": "",
                }
            )
        nested_trace.append(
            {
                "stage": "decision",
                "title": "投资动作映射",
                "status": "succeeded",
                "summary": (
                    f"{result.investment_advice.action_label} · "
                    f"置信度 {result.investment_advice.confidence}/100 · "
                    f"候选分 {result.strategy.candidate_score}/100 · "
                    f"{result.strategy.direction_label} / {result.strategy.timing.label}"
                ),
                "skills": ["investment-decision-engine"],
                "evidence_path": "",
            }
        )
        nested_trace.append(
            {
                "stage": "trigger",
                "title": "买入触发与失效条件",
                "status": "succeeded",
                "summary": result.investment_advice.invalidation_condition,
                "skills": ["investment-decision-engine"],
                "evidence_path": "",
            }
        )
        scenario_ranges = "；".join(
            f"{item.trading_days} 日 {item.price_range_low:.2f}–{item.price_range_high:.2f} "
            f"({item.direction_label})"
            for item in result.investment_advice.forecasts
            if item.price_range_low is not None and item.price_range_high is not None
        )
        nested_trace.append(
            {
                "stage": "scenario",
                "title": "价格情景区间",
                "status": "succeeded",
                "summary": (scenario_ranges or "当前没有可用区间")
                + "；区间由历史波动和规则情景生成，不是买点或收益概率。",
                "skills": ["investment-decision-engine"],
                "evidence_path": "",
            }
        )
        return {
            "symbol": result.data.symbol,
            "source": result.data.source,
            "as_of": result.data.as_of.isoformat(),
            "adjustment": result.data.adjustment.value,
            "action": result.investment_advice.action_label,
            "confidence": result.investment_advice.confidence,
            "direction": result.strategy.direction_label,
            "timing": result.strategy.timing.label,
            "market_priority": result.market_context.priority_label,
            "invalidation_condition": result.investment_advice.invalidation_condition,
            "forecast": [asdict(item) for item in result.investment_advice.forecasts],
            "snapshot": result.snapshot.to_prompt_payload(),
            "advice": result.investment_advice.to_dict(),
            "strategy": result.strategy.to_dict(),
            "market_context": result.market_context.to_dict(),
            "warnings": list(result.data.warnings),
            "workspace": result.workspace,
            "research_trace": nested_trace,
        }

    @staticmethod
    def _active_strategy(packages: Sequence[SkillPackage]) -> CandidateStrategy | None:
        for package in packages:
            if "strategy.json" in package.resources:
                return candidate_strategy_from_skill(package)
        return None

    @staticmethod
    def _industry(portfolio: PortfolioBook, symbol: str) -> str:
        position = next((item for item in portfolio.positions if item.symbol == symbol), None)
        if position is not None:
            return position.industry
        watch = next((item for item in portfolio.watchlist if item.symbol == symbol), None)
        return watch.industry if watch is not None else ""

    @staticmethod
    def _name(portfolio: PortfolioBook, symbol: str) -> str:
        position = next((item for item in portfolio.positions if item.symbol == symbol), None)
        if position is not None:
            return position.name
        watch = next((item for item in portfolio.watchlist if item.symbol == symbol), None)
        return watch.name if watch is not None else ""

    @staticmethod
    def _model_tool(spec: ToolSpec) -> dict[str, Any]:
        return {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "risk": spec.risk.value,
            "side_effect": spec.side_effect,
        }

    @staticmethod
    def _skill_reference(package: SkillPackage) -> dict[str, str]:
        return {
            "name": package.summary.name,
            "provider": package.summary.provider,
            "version": package.summary.version,
            "manifest_sha256": package.summary.manifest_sha256,
        }

    @staticmethod
    def _tool_title(name: str) -> str:
        return {
            "skills.list": "发现可用 Skill",
            "skills.activate": "加载 Skill",
            "portfolio.snapshot": "读取候选范围",
            "portfolio.list": "读取本地持仓",
            "portfolio.upsert": "添加或更新本地持仓",
            "portfolio.remove": "删除本地持仓记录",
            "evidence.current": "读取当前证据",
            "macro.snapshot": "读取宏观快照",
            "market.analyze": "运行证券研究",
            "research.screen": "扫描候选池",
            "web.search": "联网搜索公开来源",
            "workspace.write": "写入研究成果",
            "workspace.list": "列出研究成果",
            "workspace.read": "读取研究成果",
            "list_files": "列出工作区文件",
            "read": "读取工作区文件",
            "write": "写入工作区文件",
            "edit": "编辑工作区文件",
            "bash": "在持久 Shell 中执行",
        }.get(name, name)

    @staticmethod
    def _tool_names(registry: ToolRegistry) -> tuple[str, ...]:
        return tuple(registry._tools)  # noqa: SLF001 - runtime owns this private registry

    def _save_state(self, path: Path, state: dict[str, Any]) -> None:
        self.workspaces.write_control_json(path, state)

    def _write_artifact(self, root: Path, name: str, content: str) -> Path:
        path = self._artifact_path(root, name)
        encoded = content.encode("utf-8")
        if len(encoded) > 256 * 1024:
            raise ValueError("单个工作区文本成果不能超过 256 KiB")
        root.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.is_symlink():
            raise ValueError("工作区成果不能是符号链接")
        temporary = root / f".{name}.{uuid4().hex}.tmp"
        temporary.write_bytes(encoded)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        self.workspaces.ensure_within_quota(root.parent.name)
        return path

    @staticmethod
    def _artifact_path(root: Path, name: str) -> Path:
        if not _SAFE_ARTIFACT.fullmatch(name):
            raise ValueError("成果文件名只允许安全的 .md/.txt/.json/.html 文件")
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("成果文件路径超出本轮工作区")
        return path

    @staticmethod
    def _artifacts(root: Path) -> tuple[InvestmentAgentArtifact, ...]:
        if not root.is_dir():
            return ()
        items: list[InvestmentAgentArtifact] = []
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.is_symlink() or not _SAFE_ARTIFACT.fullmatch(path.name):
                continue
            media_type = {
                ".json": "application/json",
                ".html": "text/html",
                ".txt": "text/plain",
            }.get(path.suffix, "text/markdown")
            items.append(
                InvestmentAgentArtifact(path.name, str(path), media_type, path.stat().st_size)
            )
        return tuple(items)

    @classmethod
    def _build_trace(
        cls,
        request: InvestmentAgentRunRequest,
        state: dict[str, Any],
        observations: Sequence[dict[str, Any]],
        active: dict[str, SkillPackage],
        *,
        answer_mode: str,
        failure: str = "",
    ) -> tuple[InvestmentAgentTraceStep, ...]:
        trace: list[InvestmentAgentTraceStep] = [
            InvestmentAgentTraceStep(
                "goal",
                "理解研究目标",
                "succeeded",
                f"意图 {request.intent}"
                + (f" · 证券 {request.symbol}" if request.symbol else "")
                + f" · 数据区间 {request.start_date} 至 {request.end_date}",
            )
        ]
        if request.attachment_context or request.attachment_warnings:
            trace.append(
                InvestmentAgentTraceStep(
                    "attachment",
                    "读取用户附件上下文",
                    "succeeded" if request.attachment_context else "skipped",
                    (
                        f"已提取 {len(request.attachment_context)} 个字符；"
                        "图片描述属于模型生成内容，关键数字需回看原文件。"
                        if request.attachment_context
                        else "附件已登记，但当前没有可提取的文本或视觉描述。"
                    ),
                    ("multimodal-attachment-context",),
                    agent_name="attachment-agent",
                )
            )
        if active:
            for package in active.values():
                reference = cls._skill_reference(package)
                trace.append(
                    InvestmentAgentTraceStep(
                        "skill",
                        f"使用 Skill · {reference['name']}",
                        "succeeded",
                        f"来源 {reference['provider']} · 版本 {reference['version']} · "
                        f"manifest {reference['manifest_sha256'][:12]}",
                        (reference["name"],),
                    )
                )
        else:
            trace.append(
                InvestmentAgentTraceStep(
                    "skill",
                    "Skill 选择",
                    "skipped",
                    "本轮没有激活 Skill；仅使用平台固定安全边界。",
                )
            )

        raw_steps = state.get("steps", [])
        state_steps = raw_steps if isinstance(raw_steps, list) else []
        for index, observation in enumerate(observations):
            state_step = state_steps[index] if index < len(state_steps) else {}
            reason = str(state_step.get("reason", "")) if isinstance(state_step, dict) else ""
            summary = str(
                observation.get("summary") or observation.get("error") or "工具未返回摘要"
            )
            if reason:
                summary = f"选择原因：{reason}；观察：{summary}"
            tool_name = str(observation.get("tool", ""))
            trace.append(
                InvestmentAgentTraceStep(
                    "tool",
                    cls._tool_title(tool_name),
                    "succeeded" if observation.get("ok") else "failed",
                    summary,
                    tool_name=tool_name,
                )
            )
            data = observation.get("data", {})
            nested = data.get("research_trace", []) if isinstance(data, dict) else []
            if not isinstance(nested, list):
                continue
            for item in nested:
                if not isinstance(item, dict):
                    continue
                raw_skills = item.get("skills", [])
                skills = (
                    tuple(str(value) for value in raw_skills)
                    if isinstance(raw_skills, list)
                    else ()
                )
                trace.append(
                    InvestmentAgentTraceStep(
                        str(item.get("stage", "research-task")),
                        str(item.get("title", "研究步骤")),
                        str(item.get("status", "unknown")),
                        str(item.get("summary", "")),
                        skills,
                        tool_name,
                        str(item.get("evidence_path", "")),
                        str(item.get("agent", "")),
                        tuple(str(value) for value in item.get("depends_on", []))
                        if isinstance(item.get("depends_on", []), list)
                        else (),
                    )
                )
        trace.append(
            InvestmentAgentTraceStep(
                "guardrail",
                "输出安全与事实校验",
                "failed" if failure else "succeeded",
                failure
                or (f"回答模式 {answer_mode}；已执行投资输出守卫，结论不连接券商、不自动下单。"),
                ("investment-decision-engine",) if "investment-decision-engine" in active else (),
            )
        )
        return tuple(trace)

    @staticmethod
    def _trace_markdown(
        request: InvestmentAgentRunRequest,
        trace: Sequence[InvestmentAgentTraceStep],
    ) -> str:
        lines = [
            "# 求衡投研助手执行 Trace",
            "",
            f"- 研究目标：{request.question}",
            f"- 意图：`{request.intent}`",
            "- 说明：这是可审阅的步骤、证据与规则理由，不包含模型隐藏思维链。",
            "",
            "## 执行链",
            "",
        ]
        for index, item in enumerate(trace, start=1):
            skills = "、".join(item.skill_names) or "平台固定能力"
            lines.extend(
                [
                    f"### {index}. {item.title}",
                    "",
                    f"- 阶段：`{item.stage}`",
                    f"- 状态：`{item.status}`",
                    f"- 子智能体：`{item.agent_name}`"
                    if item.agent_name
                    else "- 子智能体：平台主 Agent",
                    "- 依赖：" + ("、".join(item.depends_on) or "无"),
                    f"- Skill：{skills}",
                    f"- 工具：`{item.tool_name}`" if item.tool_name else "- 工具：无",
                    f"- 依据：{item.summary}",
                    (
                        f"- 证据文件：`{item.evidence_path}`"
                        if item.evidence_path
                        else "- 证据文件：本轮状态或内存结构化结果"
                    ),
                    "",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _report_markdown(
        request: InvestmentAgentRunRequest,
        answer: str,
        observations: Sequence[dict[str, Any]],
        active: dict[str, SkillPackage],
        trace: Sequence[InvestmentAgentTraceStep],
    ) -> str:
        skill_text = ", ".join(active) or "无"
        tool_text = (
            "\n".join(
                f"- `{item.get('tool')}` · {'成功' if item.get('ok') else '失败'} · "
                f"{item.get('summary') or item.get('error', '')}"
                for item in observations
            )
            or "- 本轮未调用工具"
        )
        trace_text = (
            "\n".join(
                f"{index}. **{item.title}** · `{item.status}` · {item.summary}"
                for index, item in enumerate(trace, start=1)
            )
            or "- 本轮无 Trace"
        )
        return (
            "# 求衡投研助手研究成果\n\n"
            f"- 目标：{request.question}\n"
            f"- 数据源：{request.source}\n"
            f"- 激活 Skill：{skill_text}\n\n"
            "## Agent 回答\n\n"
            f"{answer}\n\n"
            "## 工具执行\n\n"
            f"{tool_text}\n\n"
            "## 可审阅 Trace\n\n"
            f"{trace_text}\n\n"
            "## 边界\n\n"
            "本成果用于基于历史证据的投资研究，不保证收益、不连接券商、不自动下单。"
        )

    @staticmethod
    def _notify(callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            return


class DeepSeekInvestmentActionModel:
    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client

    async def choose_investment_action(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        active_skills: list[dict[str, str]],
        remaining_steps: int,
    ) -> InvestmentAgentAction:
        payload = {
            "goal": goal,
            "context": context,
            "available_tools": tools,
            "observations": observations,
            "active_skills": active_skills,
            "remaining_steps": remaining_steps,
        }
        data = await self.client.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "你是求衡（EquiSeek）投资 lead agent。每轮只返回一个 JSON 对象。"
                        "action 只能是 tool、final 或 clarify。tool 时必须使用 available_tools "
                        "中的名字并提供 arguments；final 时提供 content；clarify 仅在确实缺少"
                        "用户选择时使用 content。优先按需加载 Skill，重要事实必须来自工具观察；"
                        "不得虚构实时行情、网页、公告、指标或交易执行。read/write/edit 只操作"
                        "用户选定工作区，修改已有文件前先 read；bash 是本轮持续会话，cwd、导出"
                        "变量和后台任务可跨调用保留，但不得绕过当前工作区权限。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            max_tokens=1_000,
        )
        action = str(data.get("action", ""))
        if action not in {"tool", "final", "clarify"}:
            raise ModelServiceError("DeepSeek 返回了未知 Agent 动作")
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ModelServiceError("DeepSeek 返回的工具参数不是对象")
        return InvestmentAgentAction(
            kind=action,  # type: ignore[arg-type]
            tool_name=str(data.get("tool")) if data.get("tool") else None,
            arguments=arguments,
            content=str(data.get("content", "")),
            reason=str(data.get("reason", ""))[:500],
        )
