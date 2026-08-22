from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from aegisrun.agents.runtime import (
    AgentContext,
    AgentOutcome,
    AgentRegistry,
    AgentSpec,
    LocalAgentRuntime,
)
from aegisrun.core.security import canonical_hash
from aegisrun.harness.prompt import PromptRegistry
from aegisrun.harness.requests import ModelRequestEnvelope
from aegisrun.harness.surface import RuntimeContextProjection
from aegisrun.macro.analysis import MacroOverlay
from aegisrun.marketdata.indicators import IndicatorSet, calculate_indicators
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet
from aegisrun.marketdata.providers import MarketDataProvider
from aegisrun.orchestration.models import ExecutionPlan, PlanStatus, TaskSpec
from aegisrun.portfolio.analysis import HoldingAssessment, assess_holding
from aegisrun.portfolio.models import Position
from aegisrun.research.advice import (
    InvestmentAdvice,
    build_investment_advice,
)
from aegisrun.research.deepseek import DeepSeekClient, ModelServiceError, deepseek_model_label
from aegisrun.research.guardrails import InvestmentOutputGuard
from aegisrun.research.market_context import (
    MarketConfluence,
    MarketTrendContext,
    build_market_confluence,
    default_benchmark,
    load_market_trend,
)
from aegisrun.research.objective import (
    ResearchSnapshot,
    build_objective_summary,
    build_research_snapshot,
)
from aegisrun.research.paths import default_research_workspace_root
from aegisrun.research.service import ResearchResult
from aegisrun.research.signals import MultiTimeframeAnalysis, analyze_multi_timeframe
from aegisrun.skills.catalog import builtin_skill_catalog
from aegisrun.skills.registry import SkillRegistry
from aegisrun.workspace.manager import WorkspaceManager

ProgressCallback = Callable[[dict[str, Any]], None]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResearchState:
    data: MarketDataSet | None = None
    indicators: IndicatorSet | None = None
    benchmark: MarketTrendContext | None = None
    strategy: MultiTimeframeAnalysis | None = None
    market_context: MarketConfluence | None = None
    investment_advice: InvestmentAdvice | None = None
    holding_assessment: HoldingAssessment | None = None
    snapshot: ResearchSnapshot | None = None
    deterministic: str | None = None
    model_summary: str | None = None
    model_warning: str | None = None
    failure: Exception | None = None


def research_plan(
    run_id: str,
    *,
    use_model: bool,
    model_name: str | None = None,
    has_position: bool = False,
    has_macro: bool = False,
) -> ExecutionPlan:
    tasks = [
        TaskSpec(
            "market_data",
            "获取并校验历史日 K",
            "market_data",
            agent="market-data-agent",
            skills=("a-share-market-data",),
            required_capabilities=("market-data",),
            network_allowed=True,
        ),
        TaskSpec(
            "indicators",
            "计算 MA/MACD/KDJ/RSI/ATR/BOLL/WR",
            "indicators",
            depends_on=("market_data",),
            agent="indicator-agent",
            skills=("technical-indicators",),
            required_capabilities=("indicator-engine",),
        ),
        TaskSpec(
            "benchmark_market",
            "默认加载大盘日 K 并计算多周期趋势",
            "benchmark_market",
            depends_on=("market_data",),
            agent="market-context-agent",
            skills=("market-sector-confluence",),
            required_capabilities=("market-context",),
            network_allowed=True,
        ),
        TaskSpec(
            "timing",
            "计算个股日/周/月 MACD、WR 与大盘共振门控",
            "timing",
            depends_on=("indicators", "benchmark_market"),
            agent="timing-agent",
            skills=("multi-timeframe-macd-wr",),
            required_capabilities=("multi-timeframe-signal",),
        ),
    ]
    advice_dependency = "timing"
    if has_macro:
        tasks.append(
            TaskSpec(
                "macro_linkage",
                "把用户行业标签映射到资本三流与成本转嫁行业视图",
                "macro_linkage",
                depends_on=("timing",),
                agent="macro-linkage-agent",
                skills=("macro-investment-synthesis",),
                required_capabilities=("macro-linkage",),
            )
        )
        advice_dependency = "macro_linkage"
    tasks.append(
        TaskSpec(
            "advice",
            "生成可回测的投资动作、方向预测与失效条件",
            "advice",
            depends_on=(advice_dependency,),
            agent="advice-agent",
            skills=("investment-decision-engine",),
            required_capabilities=("investment-decision",),
        )
    )
    facts_dependency = "advice"
    if has_position:
        tasks.append(
            TaskSpec(
                "portfolio",
                "结合本地持仓计算技术退出风险",
                "portfolio",
                depends_on=("advice",),
                agent="portfolio-agent",
                skills=("portfolio-risk-monitor",),
                required_capabilities=("portfolio-risk",),
            )
        )
        facts_dependency = "portfolio"
    tasks.append(
        TaskSpec(
            "facts",
            "生成可追溯历史事实摘要",
            "facts",
            depends_on=(facts_dependency,),
            agent="evidence-agent",
            skills=("historical-evidence",),
            required_capabilities=("historical-evidence",),
        )
    )
    if use_model:
        tasks.append(
            TaskSpec(
                "model",
                f"{deepseek_model_label(model_name)} 语言整理",
                "model",
                depends_on=("facts",),
                agent="language-agent",
                skills=("deepseek-summary",),
                required_capabilities=("model-summary",),
                network_allowed=True,
            )
        )
    tasks.append(
        TaskSpec(
            "guardrail",
            "校验规则建议无收益保证且未声称自动交易",
            "guardrail",
            depends_on=(("model",) if use_model else ("facts",)),
            agent="compliance-agent",
            skills=("investment-output-guardrail",),
            required_capabilities=("investment-output-guard",),
        )
    )
    return ExecutionPlan.create(
        run_id,
        "完成一次结合个股、大盘和按需板块共振的可追溯 A 股投资决策研究",
        tuple(tasks),
        context={"plan_source": "deterministic-research-planner", "lead_agent": "lead-agent"},
    )


def _research_registry(
    state: _ResearchState,
    provider: MarketDataProvider,
    symbol: str,
    start_date: date,
    end_date: date,
    adjustment: AdjustmentMode,
    model: DeepSeekClient | None,
    position: Position | None,
    macro_overlay: MacroOverlay | None,
) -> AgentRegistry:
    registry = AgentRegistry()

    async def market_data(context: AgentContext) -> AgentOutcome:
        try:
            state.data = await asyncio.to_thread(
                provider.fetch_daily, symbol, start_date, end_date, adjustment
            )
            if len(state.data.bars) < 30:
                raise ValueError("至少需要 30 个有效交易日才能生成研究摘要")
            bars_digest = hashlib.sha256(
                "\n".join(
                    f"{bar.trade_date.isoformat()}|{bar.open}|{bar.high}|{bar.low}|"
                    f"{bar.close}|{bar.volume}|{bar.amount}|{bar.pre_close}"
                    for bar in state.data.bars
                ).encode()
            ).hexdigest()
            await context.emit(
                "market-data/fetched",
                {
                    "symbol": state.data.symbol,
                    "source": state.data.source,
                    "adjustment": state.data.adjustment.value,
                    "as_of": state.data.as_of.isoformat(),
                    "bars": len(state.data.bars),
                    "synthetic": state.data.is_synthetic,
                    "warnings": list(state.data.warnings),
                    "cache": {
                        "status": state.data.cache_status,
                        "hit_bars": state.data.cache_hit_bars,
                        "added_bars": state.data.cache_added_bars,
                        "network_rows": state.data.network_rows,
                        "fetch_ranges": list(state.data.fetch_ranges),
                        "rebuilt": state.data.cache_rebuilt,
                        "lineage_key": {
                            "source": state.data.source,
                            "symbol": state.data.symbol,
                            "adjustment": state.data.adjustment.value,
                        },
                    },
                    "dataset_sha256": bars_digest,
                },
            )
            return AgentOutcome(
                "历史日 K 已校验",
                {
                    "source": state.data.source,
                    "bars": len(state.data.bars),
                    "as_of": state.data.as_of.isoformat(),
                    "synthetic": state.data.is_synthetic,
                    "cache_status": state.data.cache_status,
                    "cache_hit_bars": state.data.cache_hit_bars,
                    "cache_added_bars": state.data.cache_added_bars,
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def indicators(context: AgentContext) -> AgentOutcome:
        try:
            if state.data is None:
                raise RuntimeError("market data is unavailable")
            state.indicators = calculate_indicators(state.data.bars)
            await context.emit(
                "indicator/computed",
                {
                    "formula_version": state.indicators.version,
                    "bars": len(state.data.bars),
                    "indicators": ["MA", "MACD", "KDJ", "RSI", "ATR", "BOLL", "WR"],
                },
            )
            return AgentOutcome(
                "指标计算完成",
                {
                    "formula_version": state.indicators.version,
                    "bars": len(state.data.bars),
                    "indicators": ["MA", "MACD", "KDJ", "RSI", "ATR", "BOLL", "WR"],
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def benchmark_market(context: AgentContext) -> AgentOutcome:
        instrument = default_benchmark(symbol)
        try:
            state.benchmark = await asyncio.to_thread(
                load_market_trend,
                provider,
                instrument,
                start_date,
                end_date,
            )
        except Exception as error:
            state.benchmark = MarketTrendContext.unavailable(instrument, error)
        payload = state.benchmark.to_dict()
        await context.emit(
            "market-context/benchmark",
            {
                **payload,
                "same_source_required": True,
                "technical_action_overridden": False,
            },
        )
        if state.benchmark.available and state.benchmark.strategy is not None:
            return AgentOutcome(
                "大盘趋势已加载",
                {
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "direction": state.benchmark.strategy.direction,
                    "source": state.benchmark.data.source if state.benchmark.data else None,
                },
            )
        return AgentOutcome(
            "大盘趋势不可用，买入门控将降级为等待",
            {"symbol": instrument.symbol, "available": False, "error": state.benchmark.error},
        )

    async def timing(context: AgentContext) -> AgentOutcome:
        try:
            if state.data is None or state.indicators is None or state.benchmark is None:
                raise RuntimeError("indicator inputs are unavailable")
            state.strategy = analyze_multi_timeframe(state.data.bars)
            state.market_context = build_market_confluence(state.strategy, state.benchmark)
            payload = state.strategy.to_dict()
            await context.emit(
                "strategy/multi-timeframe",
                {
                    "version": state.strategy.version,
                    "direction": state.strategy.direction,
                    "timing": state.strategy.timing.action,
                    "candidate_score": state.strategy.candidate_score,
                    "market_confluence": state.market_context.to_dict(),
                    "risk_flags": list(state.strategy.risk_flags),
                    "analysis_sha256": canonical_hash(payload),
                },
            )
            return AgentOutcome(
                "多周期技术结构完成",
                {
                    "direction": state.strategy.direction,
                    "timing": state.strategy.timing.action,
                    "candidate_score": state.strategy.candidate_score,
                    "risk_count": len(state.strategy.risk_flags),
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def advice(context: AgentContext) -> AgentOutcome:
        try:
            if (
                state.data is None
                or state.indicators is None
                or state.strategy is None
                or state.market_context is None
            ):
                raise RuntimeError("investment decision inputs are unavailable")
            state.investment_advice = await asyncio.to_thread(
                build_investment_advice,
                state.data.symbol,
                state.data.bars,
                state.indicators,
                state.strategy,
                position=position,
                macro_overlay=macro_overlay,
                market_context=state.market_context,
            )
            payload = state.investment_advice.to_dict()
            await context.emit(
                "investment/advice",
                {
                    "version": state.investment_advice.version,
                    "action": state.investment_advice.action.value,
                    "confidence": state.investment_advice.confidence,
                    "forecast_horizons": [
                        item.trading_days for item in state.investment_advice.forecasts
                    ],
                    "advice_sha256": canonical_hash(payload),
                    "broker_execution": False,
                },
            )
            return AgentOutcome(
                "投资动作与方向预测完成",
                {
                    "action": state.investment_advice.action.value,
                    "confidence": state.investment_advice.confidence,
                    "broker_execution": False,
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def macro_linkage(context: AgentContext) -> AgentOutcome:
        if macro_overlay is None:
            raise RuntimeError("macro overlay is unavailable")
        await context.emit(
            "macro/stock-overlay",
            {
                **macro_overlay.to_dict(),
                "technical_action_overridden": False,
            },
        )
        return AgentOutcome(
            "宏观行业映射完成",
            {
                "industry": macro_overlay.industry,
                "stance": macro_overlay.stance,
                "confidence_adjustment": macro_overlay.confidence_adjustment,
                "technical_action_overridden": False,
            },
        )

    async def portfolio(context: AgentContext) -> AgentOutcome:
        try:
            if (
                position is None
                or state.data is None
                or state.strategy is None
                or state.investment_advice is None
            ):
                raise RuntimeError("portfolio inputs are unavailable")
            state.holding_assessment = assess_holding(
                position,
                state.data.bars[-1].close,
                state.strategy,
                state.investment_advice,
                bars=state.data.bars,
                macro_overlay=macro_overlay,
            )
            await context.emit(
                "portfolio/risk-assessed",
                {
                    "symbol": position.symbol,
                    "status": state.holding_assessment.status,
                    "strategy_version": state.strategy.version,
                    "private_fields_persisted": False,
                },
            )
            return AgentOutcome(
                "本地持仓技术风险完成",
                {
                    "status": state.holding_assessment.status,
                    "private_fields_persisted": False,
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def facts(context: AgentContext) -> AgentOutcome:
        try:
            if (
                state.data is None
                or state.indicators is None
                or state.strategy is None
                or state.investment_advice is None
            ):
                raise RuntimeError("research inputs are unavailable")
            state.snapshot = build_research_snapshot(
                state.data,
                state.indicators,
                state.strategy,
                state.investment_advice,
                state.market_context,
            )
            state.deterministic = build_objective_summary(
                state.snapshot, state.strategy, state.investment_advice
            )
            prompt_payload = state.snapshot.to_prompt_payload()
            await context.emit(
                "research/facts",
                {
                    "snapshot": prompt_payload,
                    "snapshot_sha256": canonical_hash(prompt_payload),
                    "summary_sha256": hashlib.sha256(state.deterministic.encode()).hexdigest(),
                },
            )
            return AgentOutcome(
                "事实摘要完成",
                {
                    "as_of": state.snapshot.as_of.isoformat(),
                    "source": state.snapshot.source,
                    "facts_sha256": hashlib.sha256(state.deterministic.encode()).hexdigest(),
                },
            )
        except Exception as error:
            state.failure = error
            raise

    async def language(context: AgentContext) -> AgentOutcome:
        try:
            if model is None or state.snapshot is None:
                raise RuntimeError("model summary inputs are unavailable")
            request_id = f"model-{uuid4()}"
            model_input = state.snapshot.to_prompt_payload()
            credential_ref = "deepseek-api-key"
            request_builder = getattr(model, "request_payload", None)
            envelope_builder = getattr(model, "request_envelope", None)
            model_config = getattr(model, "config", None)
            model_name = str(getattr(model_config, "model", type(model).__name__))
            if callable(envelope_builder):
                envelope = envelope_builder(model_input)
                if not isinstance(envelope, ModelRequestEnvelope):
                    raise TypeError("model request_envelope returned an invalid value")
            else:
                request_body = (
                    request_builder(model_input)
                    if callable(request_builder)
                    else {"input": model_input, "adapter": type(model).__name__}
                )
                prompt = PromptRegistry().assemble()
                envelope = ModelRequestEnvelope.create(
                    provider="in-process",
                    model=model_name,
                    prompt=prompt,
                    messages=({"role": "user", "content": model_input},),
                    effective_config={},
                    defaults={},
                    request_body=request_body,
                )
            context_event = await RuntimeContextProjection().project(
                envelope.prompt,
                context.events,
                actor_id=context.spec.name,
                task_id=context.task.id,
            )
            input_event = await context.emit(
                "user/message",
                {
                    "message_id": f"input-{request_id}",
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                model_input,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "message_source": {
                        "kind": "research-facts",
                        "sha256": canonical_hash(model_input),
                    },
                },
            )
            surface_seqs = [input_event.seq]
            if context_event is not None:
                surface_seqs.insert(0, context_event.seq)
            header = await context.emit(
                "request/header",
                envelope.header_payload(
                    request_id,
                    credential_ref=credential_ref,
                    surface_event_seqs=surface_seqs,
                ),
            )
            await context.emit(
                "model/request",
                envelope.model_request_payload(
                    request_id,
                    credential_ref=credential_ref,
                    header_seq=header.seq,
                ),
            )
            try:
                prepared = getattr(model, "summarize_prepared", None)
                state.model_summary = (
                    await prepared(envelope)
                    if callable(prepared)
                    else await model.summarize(model_input)
                )
                await context.emit(
                    "model/response",
                    {
                        "request_id": request_id,
                        "model": model_name,
                        "content": state.model_summary,
                        "content_sha256": hashlib.sha256(state.model_summary.encode()).hexdigest(),
                    },
                )
                await context.emit(
                    "assistant/message",
                    {
                        "message_id": f"response-{request_id}",
                        "role": "assistant",
                        "content": [{"type": "text", "text": state.model_summary}],
                        "message_source": {"kind": "model-response", "request_id": request_id},
                    },
                )
                return AgentOutcome(
                    "模型语言整理已通过输出门",
                    {
                        "accepted": True,
                        "summary_sha256": hashlib.sha256(state.model_summary.encode()).hexdigest(),
                    },
                )
            except ModelServiceError as error:
                await context.emit(
                    "model/failure",
                    {
                        "request_id": request_id,
                        "model": model_name,
                        "error_type": type(error).__name__,
                        "message": str(error)[:2_000],
                    },
                )
                state.model_warning = f"DeepSeek 语言整理未采用：{error}"
                return AgentOutcome(
                    "模型失败，已安全降级",
                    {"accepted": False, "warning": state.model_warning[:2_000]},
                )
            except Exception as error:
                await context.emit(
                    "model/failure",
                    {
                        "request_id": request_id,
                        "model": model_name,
                        "error_type": type(error).__name__,
                        "message": str(error)[:2_000],
                        "expected": False,
                    },
                )
                raise
        except Exception as error:
            state.failure = error
            raise

    async def guardrail(context: AgentContext) -> AgentOutcome:
        try:
            if state.deterministic is None:
                raise RuntimeError("deterministic summary is unavailable")
            guard = InvestmentOutputGuard()
            guard.ensure_safe(state.deterministic)
            if state.model_summary is not None:
                guard.ensure_safe(state.model_summary)
            await context.emit(
                "policy/decision",
                {
                    "policy": "investment-output-guardrail",
                    "decision": "allow",
                    "model_used": state.model_summary is not None,
                },
            )
            return AgentOutcome(
                "研究结果组装完成",
                {"safe": True, "model_used": state.model_summary is not None},
            )
        except Exception as error:
            state.failure = error
            raise

    registry.register(
        AgentSpec(
            "market-data-agent",
            "只读获取并校验历史行情",
            frozenset({"market_data"}),
            frozenset({"a-share-market-data"}),
            frozenset({"market-data-read"}),
            frozenset({"market-data"}),
            network_allowed=True,
        ),
        {"market_data": market_data},
    )
    registry.register(
        AgentSpec(
            "indicator-agent",
            "只用确定性公式计算技术指标",
            frozenset({"indicators"}),
            frozenset({"technical-indicators"}),
            capabilities=frozenset({"indicator-engine"}),
        ),
        {"indicators": indicators},
    )
    registry.register(
        AgentSpec(
            "market-context-agent",
            "默认加载同源大盘行情；板块趋势由桌面端按需加载",
            frozenset({"benchmark_market"}),
            frozenset({"market-sector-confluence"}),
            frozenset({"market-data-read"}),
            frozenset({"market-context"}),
            network_allowed=True,
        ),
        {"benchmark_market": benchmark_market},
    )
    registry.register(
        AgentSpec(
            "timing-agent",
            "用日/周/月 MACD 判断结构方向，并用 WR 定位技术观察窗口",
            frozenset({"timing"}),
            frozenset({"multi-timeframe-macd-wr"}),
            capabilities=frozenset({"multi-timeframe-signal"}),
        ),
        {"timing": timing},
    )
    if macro_overlay is not None:
        registry.register(
            AgentSpec(
                "macro-linkage-agent",
                "只按用户填写的行业标签映射宏观行业视图，不猜测公司行业",
                frozenset({"macro_linkage"}),
                frozenset({"macro-investment-synthesis"}),
                capabilities=frozenset({"macro-linkage"}),
            ),
            {"macro_linkage": macro_linkage},
        )
    registry.register(
        AgentSpec(
            "advice-agent",
            "把 MACD 大方向、WR 时机与历史验证映射为明确投资动作和方向预测",
            frozenset({"advice"}),
            frozenset({"investment-decision-engine"}),
            capabilities=frozenset({"investment-decision"}),
        ),
        {"advice": advice},
    )
    if position is not None:
        registry.register(
            AgentSpec(
                "portfolio-agent",
                "只在本地结合持仓与确定性信号计算技术风险",
                frozenset({"portfolio"}),
                frozenset({"portfolio-risk-monitor"}),
                capabilities=frozenset({"portfolio-risk"}),
            ),
            {"portfolio": portfolio},
        )
    registry.register(
        AgentSpec(
            "evidence-agent",
            "生成有来源和截止日期的历史事实",
            frozenset({"facts"}),
            frozenset({"historical-evidence"}),
            capabilities=frozenset({"historical-evidence"}),
        ),
        {"facts": facts},
    )
    if model is not None:
        registry.register(
            AgentSpec(
                "language-agent",
                "只整理结构化事实的可选模型代理",
                frozenset({"model"}),
                frozenset({"deepseek-summary"}),
                frozenset({"deepseek-chat"}),
                frozenset({"model-summary"}),
                network_allowed=True,
            ),
            {"model": language},
        )
    registry.register(
        AgentSpec(
            "compliance-agent",
            "允许有证据的规则建议，同时阻止收益保证和虚构交易执行",
            frozenset({"guardrail"}),
            frozenset({"investment-output-guardrail"}),
            capabilities=frozenset({"investment-output-guard"}),
        ),
        {"guardrail": guardrail},
    )
    return registry


async def run_research_pipeline(
    provider: MarketDataProvider,
    symbol: str,
    start_date: date,
    end_date: date,
    adjustment: AdjustmentMode,
    model: DeepSeekClient | None = None,
    *,
    workspace_root: Path | None = None,
    run_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    position: Position | None = None,
    macro_overlay: MacroOverlay | None = None,
) -> ResearchResult:
    model_config = getattr(model, "config", None)
    selected_model = (
        str(getattr(model_config, "model", type(model).__name__)) if model is not None else None
    )
    plan = research_plan(
        run_id or f"research-{uuid4()}",
        use_model=model is not None,
        model_name=selected_model,
        has_position=position is not None,
        has_macro=macro_overlay is not None,
    )
    plan.context.update(
        {
            "symbol": symbol,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "adjustment": adjustment.value,
            "provider": type(provider).__name__,
        }
    )
    manager = WorkspaceManager(workspace_root or default_research_workspace_root())
    state = _ResearchState()
    runtime = LocalAgentRuntime(
        manager,
        _research_registry(
            state,
            provider,
            symbol,
            start_date,
            end_date,
            adjustment,
            model,
            position,
            macro_overlay,
        ),
        SkillRegistry.from_catalog(builtin_skill_catalog()),
        max_concurrency=3,
        max_delegations=10,
    )
    await runtime.execute(plan, on_progress=on_progress)
    if plan.status is not PlanStatus.SUCCEEDED:
        if state.failure is not None:
            raise state.failure
        raise RuntimeError("research agent plan did not complete")
    if (
        state.data is None
        or state.indicators is None
        or state.market_context is None
        or state.strategy is None
        or state.investment_advice is None
        or state.snapshot is None
        or state.deterministic is None
    ):
        raise RuntimeError("research agent returned incomplete state")
    return ResearchResult(
        data=state.data,
        indicators=state.indicators,
        snapshot=state.snapshot,
        strategy=state.strategy,
        market_context=state.market_context,
        investment_advice=state.investment_advice,
        macro_overlay=macro_overlay,
        holding_assessment=state.holding_assessment,
        deterministic_summary=state.deterministic,
        model_summary=state.model_summary,
        model_warning=state.model_warning,
        plan=plan.to_dict(),
        workspace=str(manager.paths(plan.id).root),
    )
