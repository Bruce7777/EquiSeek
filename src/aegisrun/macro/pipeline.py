from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from aegisrun.agents.runtime import (
    AgentContext,
    AgentOutcome,
    AgentRegistry,
    AgentSpec,
    LocalAgentRuntime,
)
from aegisrun.core.security import canonical_hash
from aegisrun.macro.analysis import (
    CapitalFlowAssessment,
    CostTransferAssessment,
    MacroAnalysis,
    analyze_macro_snapshot,
    build_macro_report,
)
from aegisrun.macro.freshness import (
    MacroFreshnessVerifier,
    MacroValidity,
    OfficialMacroFreshnessVerifier,
)
from aegisrun.macro.models import MacroSnapshot
from aegisrun.macro.providers import MacroDataProvider
from aegisrun.orchestration.models import ExecutionPlan, PlanStatus, TaskSpec
from aegisrun.research.guardrails import InvestmentOutputGuard
from aegisrun.research.paths import default_research_workspace_root
from aegisrun.skills.catalog import builtin_skill_catalog
from aegisrun.skills.registry import SkillRegistry
from aegisrun.workspace.manager import WorkspaceManager


@dataclass(frozen=True, slots=True)
class MacroResearchResult:
    analysis: MacroAnalysis
    validity: MacroValidity
    report: str
    plan: dict[str, object]
    workspace: str


@dataclass(slots=True)
class _MacroState:
    snapshot: MacroSnapshot | None = None
    validity: MacroValidity | None = None
    capital_flow: CapitalFlowAssessment | None = None
    cost_transfer: CostTransferAssessment | None = None
    analysis: MacroAnalysis | None = None
    report: str | None = None
    failure: Exception | None = None


def macro_research_plan(run_id: str) -> ExecutionPlan:
    return ExecutionPlan.create(
        run_id,
        "使用可追溯官方数据完成资本三流、成本转嫁、长期资产配置与行业建议",
        (
            TaskSpec(
                "macro_data",
                "联网发现最新官方发布并生成结构化宏观快照",
                "macro_data",
                agent="macro-data-agent",
                skills=("macro-official-data",),
                required_capabilities=("macro-data",),
            ),
            TaskSpec(
                "freshness_gate",
                "联网核验官方发布并判定快照是否仍可用于当前决策",
                "freshness_gate",
                depends_on=("macro_data",),
                agent="macro-freshness-agent",
                skills=("macro-official-freshness",),
                required_capabilities=("macro-freshness",),
                network_allowed=True,
            ),
            TaskSpec(
                "capital_flow",
                "计算资本流量、流向和流速代理",
                "capital_flow",
                depends_on=("macro_data",),
                agent="capital-flow-agent",
                skills=("capital-three-flows",),
                required_capabilities=("capital-flow-analysis",),
            ),
            TaskSpec(
                "cost_transfer",
                "计算代价转嫁压力通道",
                "cost_transfer",
                depends_on=("macro_data",),
                agent="cost-transfer-agent",
                skills=("cost-transfer-lens",),
                required_capabilities=("cost-transfer-analysis",),
            ),
            TaskSpec(
                "macro_synthesis",
                "合成宏观投资结论、长期资产配置与行业建议",
                "macro_synthesis",
                depends_on=("freshness_gate", "capital_flow", "cost_transfer"),
                agent="macro-synthesis-agent",
                skills=("macro-investment-synthesis",),
                required_capabilities=("macro-synthesis",),
            ),
            TaskSpec(
                "guardrail",
                "校验宏观建议无收益保证且未声称自动交易",
                "guardrail",
                depends_on=("macro_synthesis",),
                agent="compliance-agent",
                skills=("investment-output-guardrail",),
                required_capabilities=("investment-output-guard",),
            ),
        ),
        context={"plan_source": "deterministic-macro-planner", "lead_agent": "lead-agent"},
    )


def _registry(
    state: _MacroState,
    provider: MacroDataProvider,
    verifier: MacroFreshnessVerifier,
    today: date | None,
) -> AgentRegistry:
    registry = AgentRegistry()

    async def macro_data(context: AgentContext) -> AgentOutcome:
        try:
            state.snapshot = await asyncio.to_thread(provider.load)
            payload = state.snapshot.to_dict()
            await context.emit(
                "macro/data-loaded",
                {
                    "version": state.snapshot.version,
                    "as_of": state.snapshot.as_of.isoformat(),
                    "metrics": len(state.snapshot.metrics),
                    "snapshot_sha256": canonical_hash(payload),
                },
            )
            return AgentOutcome(
                "官方最新宏观数据刷新与结构化校验完成",
                {"as_of": state.snapshot.as_of.isoformat(), "metrics": len(state.snapshot.metrics)},
            )
        except Exception as error:
            state.failure = error
            raise

    async def freshness_gate(context: AgentContext) -> AgentOutcome:
        try:
            if state.snapshot is None:
                raise RuntimeError("macro snapshot is unavailable")
            state.validity = await verifier.verify(state.snapshot, today=today)
            await context.emit("macro/freshness-checked", state.validity.to_dict())
            return AgentOutcome(
                state.validity.status_label,
                {
                    "status": state.validity.status,
                    "current_decision_allowed": state.validity.current_decision_allowed,
                    "newer_release_count": state.validity.newer_release_count,
                    "successful_sources": sum(
                        item.status == "succeeded" for item in state.validity.source_checks
                    ),
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def capital_flow(context: AgentContext) -> AgentOutcome:
        try:
            if state.snapshot is None:
                raise RuntimeError("macro snapshot is unavailable")
            analysis = analyze_macro_snapshot(state.snapshot)
            state.capital_flow = analysis.capital_flow
            await context.emit("macro/capital-flow", asdict(state.capital_flow))
            return AgentOutcome(
                "资本流量、流向和流速代理完成",
                {
                    "direction_score": state.capital_flow.direction_score,
                    "volume_score": state.capital_flow.volume_score,
                    "transmission_score": state.capital_flow.transmission_score,
                    "speed_score": state.capital_flow.speed_score,
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def cost_transfer(context: AgentContext) -> AgentOutcome:
        try:
            if state.snapshot is None:
                raise RuntimeError("macro snapshot is unavailable")
            analysis = analyze_macro_snapshot(state.snapshot)
            state.cost_transfer = analysis.cost_transfer
            await context.emit("macro/cost-transfer", asdict(state.cost_transfer))
            return AgentOutcome(
                "代价转嫁代理压力完成",
                {"pressure_score": state.cost_transfer.pressure_score},
            )
        except Exception as error:
            state.failure = error
            raise

    async def synthesis(context: AgentContext) -> AgentOutcome:
        try:
            if (
                state.snapshot is None
                or state.validity is None
                or state.capital_flow is None
                or state.cost_transfer is None
            ):
                raise RuntimeError("macro analysis inputs are unavailable")
            state.analysis = analyze_macro_snapshot(state.snapshot)
            state.report = build_macro_report(state.analysis)
            if not state.validity.current_decision_allowed:
                state.report = (
                    "# 时效门禁：当前宏观投资结论不可用\n\n"
                    f"- 状态：{state.validity.status_label}\n"
                    f"- 原因：{state.validity.reason}\n"
                    "- 处理：以下内容仅保留为历史模型回放，不用于当前仓位、行业或个股决策。\n\n"
                    + state.report
                )
            await context.emit(
                "macro/report-built",
                {
                    "version": state.analysis.version,
                    "report_sha256": canonical_hash({"report": state.report}),
                    "source_count": len(state.snapshot.methodology_sources)
                    + len({metric.source_url for metric in state.snapshot.metrics}),
                },
            )
            return AgentOutcome("宏观投资研究报告完成", {"regime": state.analysis.regime})
        except Exception as error:
            state.failure = error
            raise

    async def guardrail(context: AgentContext) -> AgentOutcome:
        try:
            if state.report is None:
                raise RuntimeError("macro report is unavailable")
            InvestmentOutputGuard().ensure_safe(state.report)
            await context.emit(
                "policy/decision",
                {
                    "policy": "investment-output-guardrail",
                    "decision": "allow",
                    "model_used": False,
                },
            )
            return AgentOutcome("宏观报告通过输出门", {"safe": True})
        except Exception as error:
            state.failure = error
            raise

    registry.register(
        AgentSpec(
            "macro-freshness-agent",
            "直接访问官方统计发布页，核验快照时效并执行当前决策门禁",
            frozenset({"freshness_gate"}),
            frozenset({"macro-official-freshness"}),
            capabilities=frozenset({"macro-freshness"}),
            network_allowed=True,
        ),
        {"freshness_gate": freshness_gate},
    )
    registry.register(
        AgentSpec(
            "macro-data-agent",
            "只读加载并校验带来源的宏观数据",
            frozenset({"macro_data"}),
            frozenset({"macro-official-data"}),
            capabilities=frozenset({"macro-data"}),
        ),
        {"macro_data": macro_data},
    )
    registry.register(
        AgentSpec(
            "capital-flow-agent",
            "计算资本流量、流向与流速代理",
            frozenset({"capital_flow"}),
            frozenset({"capital-three-flows"}),
            capabilities=frozenset({"capital-flow-analysis"}),
        ),
        {"capital_flow": capital_flow},
    )
    registry.register(
        AgentSpec(
            "cost-transfer-agent",
            "计算成本转嫁压力通道和缓冲项",
            frozenset({"cost_transfer"}),
            frozenset({"cost-transfer-lens"}),
            capabilities=frozenset({"cost-transfer-analysis"}),
        ),
        {"cost_transfer": cost_transfer},
    )
    registry.register(
        AgentSpec(
            "macro-synthesis-agent",
            "合成宏观投资结论、长期资产配置、行业建议和方法边界",
            frozenset({"macro_synthesis"}),
            frozenset({"macro-investment-synthesis"}),
            capabilities=frozenset({"macro-synthesis"}),
        ),
        {"macro_synthesis": synthesis},
    )
    registry.register(
        AgentSpec(
            "compliance-agent",
            "允许规则配置建议并阻止收益保证或虚构交易",
            frozenset({"guardrail"}),
            frozenset({"investment-output-guardrail"}),
            capabilities=frozenset({"investment-output-guard"}),
        ),
        {"guardrail": guardrail},
    )
    return registry


async def run_macro_research(
    provider: MacroDataProvider,
    *,
    workspace_root: Path | None = None,
    run_id: str | None = None,
    verifier: MacroFreshnessVerifier | None = None,
    today: date | None = None,
) -> MacroResearchResult:
    plan = macro_research_plan(run_id or f"macro-{uuid4()}")
    manager = WorkspaceManager(workspace_root or default_research_workspace_root())
    state = _MacroState()
    runtime = LocalAgentRuntime(
        manager,
        _registry(state, provider, verifier or OfficialMacroFreshnessVerifier(), today),
        SkillRegistry.from_catalog(builtin_skill_catalog()),
        max_concurrency=2,
        max_delegations=6,
    )
    await runtime.execute(plan)
    if plan.status is not PlanStatus.SUCCEEDED:
        if state.failure is not None:
            raise state.failure
        raise RuntimeError("macro research agent plan did not complete")
    if state.analysis is None or state.validity is None or state.report is None:
        raise RuntimeError("macro research agent returned incomplete state")
    return MacroResearchResult(
        state.analysis,
        state.validity,
        state.report,
        plan.to_dict(),
        str(manager.paths(plan.id).root),
    )
