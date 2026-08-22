from __future__ import annotations

from datetime import date, timedelta

import pytest

from aegisrun.macro.analysis import analyze_macro_snapshot, build_macro_overlay
from aegisrun.macro.providers import BundledOfficialMacroProvider
from aegisrun.marketdata.indicators import calculate_indicators
from aegisrun.marketdata.models import AdjustmentMode, PriceBar
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.portfolio.models import Position
from aegisrun.research.advice import (
    InvestmentAction,
    build_investment_advice,
    build_investment_advice_summary,
)
from aegisrun.research.guardrails import InvestmentOutputGuard, UnsafeAdviceError
from aegisrun.research.market_context import (
    ContextInstrument,
    MarketTrendContext,
    build_market_confluence,
)
from aegisrun.research.signals import (
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)


def _bars(count: int = 620) -> tuple[PriceBar, ...]:
    start = date(2023, 1, 2)
    result: list[PriceBar] = []
    close = 100.0
    for index in range(count):
        close = 100 + index * 0.04 + ((index % 23) - 11) * 0.35
        result.append(
            PriceBar(
                start + timedelta(days=index),
                close - 0.2,
                close + 1.0,
                close - 1.0,
                close,
                10_000 + index,
                close * (10_000 + index),
                result[-1].close if result else close,
            )
        )
    return tuple(result)


def _analysis(
    direction: Direction,
    timing: TimingAction,
    *,
    strength: int = 82,
    risks: tuple[str, ...] = (),
) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        version="test",
        direction=direction.value,
        direction_label=direction.label,
        direction_score=6 if direction is Direction.BULLISH else -6,
        regime="test",
        macd={},
        wr={},
        risk_flags=risks,
        timing=TimingDecision(timing.value, timing.label, strength, ("测试触发",)),
        candidate_score=80,
    )


def _market_context(
    stock: MultiTimeframeAnalysis,
    market: Direction,
    sector: Direction | None = None,
):
    benchmark = MarketTrendContext(
        ContextInstrument("benchmark", "000001.SH", "上证综指"),
        DemoMarketDataProvider().fetch_daily(
            "000001.SH", date(2023, 1, 1), date(2026, 1, 1), AdjustmentMode.BFQ
        ),
        _analysis(market, TimingAction.WAIT),
    )
    sector_trend = (
        MarketTrendContext(
            ContextInstrument("sector", "000935.SH", "中证信息技术指数", "信息技术"),
            DemoMarketDataProvider().fetch_daily(
                "000935.SH", date(2023, 1, 1), date(2026, 1, 1), AdjustmentMode.BFQ
            ),
            _analysis(sector, TimingAction.WAIT),
        )
        if sector is not None
        else None
    )
    return build_market_confluence(stock, benchmark, sector_trend)


def test_bullish_wr_entry_window_becomes_explicit_buy_advice() -> None:
    bars = _bars(300)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        _analysis(Direction.BULLISH, TimingAction.ENTRY_WATCH),
    )

    assert advice.action is InvestmentAction.BUY
    assert advice.action_label == "买入"
    assert advice.direction == Direction.BULLISH.value
    assert advice.invalidation_price is not None
    assert [item.trading_days for item in advice.forecasts] == [5, 10, 20]
    assert all(item.probability_pct is None for item in advice.forecasts)
    assert all(item.scenario_score is not None for item in advice.forecasts)
    summary = build_investment_advice_summary(advice)
    assert summary.startswith("## 投资结论（MACD 大方向 + WR 时机）\n")
    assert "\n### 方向预测\n" in summary
    assert "日线 WR" in summary


def test_same_signal_maps_to_add_for_existing_position() -> None:
    bars = _bars(300)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        _analysis(Direction.BULLISH, TimingAction.ENTRY_WATCH),
        position=Position("600519.SH", 100, 90),
    )

    assert advice.action is InvestmentAction.ADD


def test_bullish_stock_buy_is_gated_when_market_is_bearish() -> None:
    bars = _bars(300)
    stock = _analysis(Direction.BULLISH, TimingAction.ENTRY_WATCH)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        stock,
        market_context=_market_context(stock, Direction.BEARISH),
    )

    assert advice.action is InvestmentAction.WAIT
    assert advice.market_confidence_adjustment < 0
    assert advice.market_context["buy_gate_open"] is False
    assert any("大盘" in item for item in advice.thesis)


def test_three_layer_bullish_confluence_keeps_buy_and_increases_priority() -> None:
    bars = _bars(300)
    stock = _analysis(Direction.BULLISH, TimingAction.ENTRY_WATCH)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        stock,
        market_context=_market_context(stock, Direction.BULLISH, Direction.BULLISH),
    )

    assert advice.action is InvestmentAction.BUY
    assert advice.market_confidence_adjustment > 0
    assert "优先候选" in advice.market_context["priority_label"]


def test_bearish_sector_proxy_blocks_buy_after_on_demand_load() -> None:
    bars = _bars(300)
    stock = _analysis(Direction.BULLISH, TimingAction.ENTRY_WATCH)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        stock,
        market_context=_market_context(stock, Direction.BULLISH, Direction.BEARISH),
    )

    assert advice.action is InvestmentAction.WAIT
    assert advice.market_context["status"] == "sector_divergent"


def test_high_timeframe_top_and_wr_exit_maps_to_sell_for_position() -> None:
    bars = _bars(300)
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        _analysis(
            Direction.BULLISH,
            TimingAction.EXIT_WATCH,
            risks=("周线 DIF 在零轴上形成双峰结构",),
        ),
        position=Position("600519.SH", 100, 90),
    )

    assert advice.action is InvestmentAction.SELL
    assert any("双峰" in item for item in advice.evidence)


def test_positive_macro_overlay_never_overrides_technical_sell_action() -> None:
    bars = _bars(300)
    macro = build_macro_overlay(
        "工业自动化",
        analyze_macro_snapshot(BundledOfficialMacroProvider().load()),
    )
    advice = build_investment_advice(
        "600519.SH",
        bars,
        calculate_indicators(bars),
        _analysis(Direction.BULLISH, TimingAction.EXIT_WATCH),
        position=Position("600519.SH", 100, 90),
        macro_overlay=macro,
    )

    assert advice.action is InvestmentAction.SELL
    assert advice.macro_overlay["stance"] == "overweight"
    assert advice.macro_confidence_adjustment < 0


def test_rule_prediction_discloses_basis_when_no_backtest_sample_exists() -> None:
    bars = _bars(300)
    advice = build_investment_advice(
        "000001.SZ",
        bars,
        calculate_indicators(bars),
        _analysis(Direction.BULLISH, TimingAction.WAIT, strength=40),
    )

    assert advice.action is InvestmentAction.WAIT
    assert all(item.basis == "rule_score" for item in advice.forecasts)
    assert all(item.expected_return_pct is None for item in advice.forecasts)
    assert all(item.probability_pct is None for item in advice.forecasts)
    assert all(item.scenario_score is not None for item in advice.forecasts)
    summary = build_investment_advice_summary(advice)
    assert "上涨情景分" in summary
    assert "条件概率/情景分" not in summary


def test_investment_guard_allows_supported_advice_but_blocks_guarantees_and_auto_orders() -> None:
    guard = InvestmentOutputGuard()
    guard.ensure_safe("规则结论：买入；上涨情景概率 62%，跌破失效价则退出。")
    guard.ensure_safe("这是规则型研究建议，不构成收益承诺，也不代表已代您执行任何交易。")
    guard.ensure_safe("该规则不承诺收益，不自动下单。")
    guard.ensure_safe("周线 WR 已经进入超买区，没有自动下单。")

    with pytest.raises(UnsafeAdviceError):
        guard.ensure_safe("这只股票必涨并且保证收益。")
    with pytest.raises(UnsafeAdviceError):
        guard.ensure_safe("系统已经自动下单买入 100 股。")
    with pytest.raises(UnsafeAdviceError):
        guard.ensure_safe("已经完成了买入。")
