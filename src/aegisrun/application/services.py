from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from aegisrun.agents.investment_runtime import (
    DeepSeekInvestmentActionModel,
    InvestmentAgentRunResult,
    InvestmentAgentRuntime,
)
from aegisrun.application.requests import (
    AdvisorChatRequest,
    BacktestRequest,
    CandidateScreenRequest,
    CandidateScreenResult,
    InvestmentAgentTaskRequest,
    InvestmentChatRequest,
    ResearchRequest,
    SectorContextRequest,
)
from aegisrun.macro.analysis import analyze_macro_snapshot, build_macro_overlay
from aegisrun.macro.freshness import snapshot_is_verified_current
from aegisrun.macro.pipeline import MacroResearchResult, run_macro_research
from aegisrun.macro.providers import default_macro_provider
from aegisrun.marketdata.baostock_provider import BaoStockProvider
from aegisrun.marketdata.cache import MarketDataCache, market_cache_enabled
from aegisrun.marketdata.cached_provider import CachedMarketDataProvider
from aegisrun.marketdata.providers import DemoMarketDataProvider, MarketDataProvider
from aegisrun.marketdata.tushare_provider import TushareProvider
from aegisrun.research.advisor_chat import (
    AdvisorAnswer,
    answer_holding_question,
    answer_investment_conversation,
)
from aegisrun.research.backtest import BacktestReport, walk_forward_backtest
from aegisrun.research.deepseek import DeepSeekClient, DeepSeekConfig
from aegisrun.research.market_context import MarketTrendContext, load_market_trend
from aegisrun.research.service import ResearchResult, run_research

ProgressCallback = Callable[[dict[str, Any]], None]


def market_data_provider(source: str, tushare_token: str | None) -> MarketDataProvider:
    if source == "tushare":
        if not tushare_token:
            raise ValueError("尚未配置 Tushare Token，请打开设置或改用 BaoStock")
        provider: MarketDataProvider = TushareProvider(tushare_token)
        return (
            CachedMarketDataProvider(provider, MarketDataCache())
            if market_cache_enabled()
            else provider
        )
    if source == "demo":
        return DemoMarketDataProvider()
    provider = BaoStockProvider()
    return (
        CachedMarketDataProvider(provider, MarketDataCache())
        if market_cache_enabled()
        else provider
    )


def _close_provider(provider: MarketDataProvider | None) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()


async def verify_deepseek_connection(api_key: str, model: str) -> str:
    client = DeepSeekClient(DeepSeekConfig(api_key=api_key, model=model))
    try:
        return await client.verify_connection()
    finally:
        await client.close()


async def execute_research(
    request: ResearchRequest,
    *,
    on_progress: ProgressCallback | None = None,
    provider: MarketDataProvider | None = None,
) -> ResearchResult:
    active_provider = provider or market_data_provider(request.source, request.tushare_token)
    model = None
    if request.use_ai and request.deepseek_api_key:
        model = DeepSeekClient(
            DeepSeekConfig(api_key=request.deepseek_api_key, model=request.deepseek_model)
        )
    macro_overlay = None
    if request.industry:
        snapshot = default_macro_provider().load()
        if snapshot_is_verified_current(snapshot, reference_date=request.end_date):
            macro_overlay = build_macro_overlay(request.industry, analyze_macro_snapshot(snapshot))
    try:
        return await run_research(
            active_provider,
            request.symbol,
            request.start_date,
            request.end_date,
            request.adjustment,
            model,
            workspace_root=Path(request.workspace_root) if request.workspace_root else None,
            on_progress=on_progress,
            position=request.position,
            macro_overlay=macro_overlay,
        )
    finally:
        if model is not None:
            await model.close()
        _close_provider(active_provider)


async def answer_advisor_chat(request: AdvisorChatRequest) -> AdvisorAnswer:
    model = (
        DeepSeekClient(
            DeepSeekConfig(api_key=request.deepseek_api_key, model=request.deepseek_model)
        )
        if request.deepseek_api_key
        else None
    )
    try:
        return await answer_holding_question(
            request.evidence,
            request.history,
            request.question,
            model,
            request.conversation,
        )
    finally:
        if model is not None:
            await model.close()


async def answer_general_investment_chat(request: InvestmentChatRequest) -> AdvisorAnswer:
    model = (
        DeepSeekClient(
            DeepSeekConfig(api_key=request.deepseek_api_key, model=request.deepseek_model)
        )
        if request.deepseek_api_key
        else None
    )
    try:
        return await answer_investment_conversation(
            request.history,
            request.question,
            request.intent,
            model,
            request.conversation,
        )
    finally:
        if model is not None:
            await model.close()


async def execute_investment_agent(
    request: InvestmentAgentTaskRequest,
    *,
    on_progress: ProgressCallback | None = None,
) -> InvestmentAgentRunResult:
    client = (
        DeepSeekClient(
            DeepSeekConfig(
                api_key=request.deepseek_api_key,
                model=request.deepseek_model,
                provider=request.model_provider,
                base_url=request.model_base_url,
            )
        )
        if request.deepseek_api_key
        else None
    )
    runtime = InvestmentAgentRuntime(
        Path(request.workspace_root),
        request.skills,
        model=DeepSeekInvestmentActionModel(client) if client is not None else None,
        portfolio_manager=request.portfolio_manager,
    )
    try:
        return await runtime.run(request.run, on_progress=on_progress)
    finally:
        if client is not None:
            await client.close()


def execute_sector_context(request: SectorContextRequest) -> MarketTrendContext:
    provider = market_data_provider(request.source, request.tushare_token)
    try:
        return load_market_trend(
            provider,
            request.instrument,
            request.start_date,
            request.end_date,
        )
    finally:
        _close_provider(provider)


async def execute_candidate_screen(
    request: CandidateScreenRequest,
    *,
    on_progress: ProgressCallback | None = None,
) -> CandidateScreenResult:
    results: list[ResearchResult] = []
    failures: dict[str, str] = {}
    total = len(request.symbols)
    positions = {item.symbol: item for item in request.positions}
    industries = dict(request.industries)
    macro_analysis = None
    if any(industries.values()):
        snapshot = default_macro_provider().load()
        if snapshot_is_verified_current(snapshot, reference_date=request.end_date):
            macro_analysis = analyze_macro_snapshot(snapshot)
    for index, symbol in enumerate(request.symbols, start=1):
        if on_progress is not None:
            on_progress(
                {"kind": "candidate-screen", "current": index, "total": total, "symbol": symbol}
            )
        provider = market_data_provider(request.source, request.tushare_token)
        try:
            result = await run_research(
                provider,
                symbol,
                request.start_date,
                request.end_date,
                request.adjustment,
                position=positions.get(symbol),
                macro_overlay=(
                    build_macro_overlay(industries[symbol], macro_analysis)
                    if macro_analysis is not None and industries.get(symbol)
                    else None
                ),
            )
            results.append(result)
        except Exception as error:
            failures[symbol] = str(error)
        finally:
            _close_provider(provider)
    if not results:
        details = "；".join(f"{symbol}: {message}" for symbol, message in failures.items())
        raise RuntimeError(f"候选池没有可用分析结果：{details}")
    return CandidateScreenResult(tuple(results), failures)


def execute_backtest(request: BacktestRequest) -> BacktestReport:
    return walk_forward_backtest(
        request.result.data.bars,
        request.options.evaluation_start,
        request.options.evaluation_end,
        transaction_cost_bps=request.options.transaction_cost_bps,
        horizons=request.options.horizons,
        min_history_bars=request.options.min_history_bars,
        analysis_window_bars=request.options.analysis_window_bars,
        symbol=request.result.data.symbol,
    )


async def execute_macro_research(workspace_root: str = "") -> MacroResearchResult:
    return await run_macro_research(
        default_macro_provider(live=True),
        workspace_root=Path(workspace_root) if workspace_root else None,
    )
