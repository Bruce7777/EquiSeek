from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from aegisrun.macro.analysis import MacroOverlay
from aegisrun.marketdata.indicators import IndicatorSet
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet
from aegisrun.marketdata.providers import MarketDataProvider
from aegisrun.portfolio.analysis import HoldingAssessment, assess_holding
from aegisrun.portfolio.models import Position
from aegisrun.research.advice import InvestmentAdvice, build_investment_advice
from aegisrun.research.deepseek import DeepSeekClient
from aegisrun.research.market_context import (
    MarketConfluence,
    MarketTrendContext,
    build_market_confluence,
)
from aegisrun.research.objective import (
    ResearchSnapshot,
    build_objective_summary,
    build_research_snapshot,
)
from aegisrun.research.signals import MultiTimeframeAnalysis


@dataclass(frozen=True, slots=True)
class ResearchResult:
    data: MarketDataSet
    indicators: IndicatorSet
    snapshot: ResearchSnapshot
    strategy: MultiTimeframeAnalysis
    market_context: MarketConfluence
    investment_advice: InvestmentAdvice
    macro_overlay: MacroOverlay | None
    holding_assessment: HoldingAssessment | None
    deterministic_summary: str
    model_summary: str | None = None
    model_warning: str | None = None
    plan: dict[str, object] | None = None
    workspace: str | None = None


def attach_sector_context(
    result: ResearchResult,
    sector: MarketTrendContext,
    *,
    position: Position | None = None,
) -> ResearchResult:
    context = build_market_confluence(
        result.strategy,
        result.market_context.benchmark,
        sector,
    )
    advice = build_investment_advice(
        result.data.symbol,
        result.data.bars,
        result.indicators,
        result.strategy,
        position=position,
        macro_overlay=result.macro_overlay,
        market_context=context,
    )
    snapshot = build_research_snapshot(
        result.data,
        result.indicators,
        result.strategy,
        advice,
        context,
    )
    holding = (
        assess_holding(
            position,
            result.data.bars[-1].close,
            result.strategy,
            advice,
            bars=result.data.bars,
            macro_overlay=result.macro_overlay,
        )
        if position is not None
        else None
    )
    return replace(
        result,
        market_context=context,
        investment_advice=advice,
        snapshot=snapshot,
        holding_assessment=holding,
        deterministic_summary=build_objective_summary(snapshot, result.strategy, advice),
        model_summary=None,
        model_warning="板块按需加载后规则结论已在本地更新；如需新的 AI 整理，请重新开始分析。",
    )


async def run_research(
    provider: MarketDataProvider,
    symbol: str,
    start_date: date,
    end_date: date,
    adjustment: AdjustmentMode,
    model: DeepSeekClient | None = None,
    *,
    workspace_root: Path | None = None,
    run_id: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    position: Position | None = None,
    macro_overlay: MacroOverlay | None = None,
) -> ResearchResult:
    from aegisrun.research.pipeline import run_research_pipeline

    return await run_research_pipeline(
        provider,
        symbol,
        start_date,
        end_date,
        adjustment,
        model,
        workspace_root=workspace_root,
        run_id=run_id,
        on_progress=on_progress,
        position=position,
        macro_overlay=macro_overlay,
    )
