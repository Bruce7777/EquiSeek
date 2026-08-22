from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from aegisrun.marketdata.indicators import calculate_indicators, calculate_signal_indicators
from aegisrun.marketdata.models import AdjustmentMode, PriceBar
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.marketdata.timeframes import Timeframe, aggregate_bars
from aegisrun.research.signals import (
    Direction,
    TimingAction,
    WrFrameState,
    WrZone,
    analyze_multi_timeframe,
    classify_wr,
    derive_timing_decision,
    detect_macd_top_structure,
    resolve_macd_direction,
)


def bar(day: date, opening: float, high: float, low: float, close: float) -> PriceBar:
    return PriceBar(day, opening, high, low, close, 100.0, 1_000.0)


def wr_state(
    timeframe: Timeframe,
    value: float,
    zone: WrZone,
    *,
    entered_oversold: bool = False,
) -> WrFrameState:
    return WrFrameState(
        timeframe=timeframe.value,
        label=timeframe.label,
        value=value,
        previous=50.0,
        zone=zone.value,
        zone_label=zone.label,
        entered_oversold=entered_oversold,
        entered_overbought=zone is WrZone.OVERBOUGHT,
    )


def test_weekly_and_monthly_aggregation_preserves_ohlcv() -> None:
    bars = (
        bar(date(2026, 1, 29), 10, 12, 9, 11),
        bar(date(2026, 1, 30), 11, 13, 10, 12),
        bar(date(2026, 2, 2), 12, 14, 11, 13),
        bar(date(2026, 2, 3), 13, 15, 12, 14),
    )

    weekly = aggregate_bars(bars, Timeframe.WEEKLY, today=date(2026, 2, 3))
    monthly = aggregate_bars(bars, Timeframe.MONTHLY, today=date(2026, 3, 1))

    assert len(weekly.bars) == 2
    assert len(monthly.bars) == 2
    assert monthly.bars[0].open == 10
    assert monthly.bars[0].high == 13
    assert monthly.bars[0].low == 9
    assert monthly.bars[0].close == 12
    assert monthly.bars[0].volume == 200
    assert monthly.latest_complete is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (95.0, WrZone.EXTREME_OVERSOLD),
        (85.0, WrZone.DEEP_OVERSOLD),
        (80.0, WrZone.OVERSOLD),
        (50.0, WrZone.NEUTRAL),
        (20.0, WrZone.OVERBOUGHT),
    ],
)
def test_wr_uses_positive_cn_quote_convention(value: float, expected: WrZone) -> None:
    assert classify_wr(value) is expected


def test_detects_price_dif_divergence_and_double_top() -> None:
    prices = [90, 95, 100, 110, 103, 101, 105, 108, 112, 116, 120, 114, 110, 108]
    dif = [0.5, 1, 2, 5, 3, 2, 1, 2, 3, 3.5, 4, 2, 1, 0.5]

    divergence, double_top = detect_macd_top_structure(prices, dif)

    assert divergence is True
    assert double_top is True


def test_bullish_direction_and_daily_oversold_yields_entry_watch() -> None:
    states = {
        Timeframe.DAILY.value: wr_state(
            Timeframe.DAILY,
            87.0,
            WrZone.DEEP_OVERSOLD,
            entered_oversold=True,
        ),
        Timeframe.WEEKLY.value: wr_state(Timeframe.WEEKLY, 55.0, WrZone.NEUTRAL),
        Timeframe.MONTHLY.value: wr_state(Timeframe.MONTHLY, 45.0, WrZone.NEUTRAL),
    }

    decision = derive_timing_decision(Direction.BULLISH, states, ())

    assert decision.action == TimingAction.ENTRY_WATCH.value
    assert decision.strength >= 80


def test_top_risk_and_overbought_yields_exit_watch() -> None:
    states = {
        Timeframe.DAILY.value: wr_state(Timeframe.DAILY, 15.0, WrZone.OVERBOUGHT),
        Timeframe.WEEKLY.value: wr_state(Timeframe.WEEKLY, 18.0, WrZone.OVERBOUGHT),
        Timeframe.MONTHLY.value: wr_state(Timeframe.MONTHLY, 40.0, WrZone.NEUTRAL),
    }

    decision = derive_timing_decision(
        Direction.BULLISH,
        states,
        ("周线 DIF 在零轴上形成双峰结构",),
    )

    assert decision.action == TimingAction.EXIT_WATCH.value
    assert "双峰" in " ".join(decision.reasons)


def test_full_analysis_contains_daily_weekly_monthly_and_candidate_score() -> None:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH",
        date(2019, 1, 1),
        date(2026, 8, 16),
        AdjustmentMode.QFQ,
    )

    analysis = analyze_multi_timeframe(data.bars)

    assert set(analysis.macd) == {"daily", "weekly", "monthly"}
    assert set(analysis.wr) == {"daily", "weekly", "monthly"}
    assert analysis.macd["monthly"].bars >= 26
    assert 0 <= analysis.candidate_score <= 100
    assert analysis.timing.reasons
    assert analysis.direction_method == "monthly_anchor_weekly_confirmation_daily_execution"
    assert [step.key for step in analysis.decision_path] == [
        "monthly_direction",
        "weekly_confirmation",
        "top_structure",
        "wr_timing",
        "final_action",
    ]
    assert all(step.summary for step in analysis.decision_path)


def test_decision_path_makes_wr_the_execution_gate_after_macd_direction() -> None:
    states = {
        Timeframe.DAILY.value: wr_state(
            Timeframe.DAILY,
            88.0,
            WrZone.DEEP_OVERSOLD,
            entered_oversold=True,
        ),
        Timeframe.WEEKLY.value: wr_state(Timeframe.WEEKLY, 50.0, WrZone.NEUTRAL),
        Timeframe.MONTHLY.value: wr_state(Timeframe.MONTHLY, 45.0, WrZone.NEUTRAL),
    }

    decision = derive_timing_decision(Direction.BULLISH, states, ())

    assert decision.action == TimingAction.ENTRY_WATCH.value
    assert decision.structure_signal == "no_top_risk"
    assert decision.wr_confirmation == "daily_oversold_entry"


def test_monthly_anchor_cannot_be_overruled_by_daily_and_weekly_noise() -> None:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH",
        date(2019, 1, 1),
        date(2026, 8, 16),
        AdjustmentMode.QFQ,
    )
    base = analyze_multi_timeframe(data.bars).macd
    bullish_anchor = {
        **base,
        "monthly": replace(base["monthly"], score=2),
        "weekly": replace(base["weekly"], score=-2),
        "daily": replace(base["daily"], score=-2),
    }
    bearish_anchor = {
        **base,
        "monthly": replace(base["monthly"], score=-2),
        "weekly": replace(base["weekly"], score=2),
        "daily": replace(base["daily"], score=2),
    }

    assert resolve_macd_direction(bullish_anchor)[0] is Direction.BULLISH
    assert resolve_macd_direction(bearish_anchor)[0] is Direction.BEARISH


def test_second_top_arms_exit_but_wr_confirms_the_exact_window() -> None:
    neutral_states = {
        Timeframe.DAILY.value: wr_state(Timeframe.DAILY, 45.0, WrZone.NEUTRAL),
        Timeframe.WEEKLY.value: wr_state(Timeframe.WEEKLY, 50.0, WrZone.NEUTRAL),
        Timeframe.MONTHLY.value: wr_state(Timeframe.MONTHLY, 40.0, WrZone.NEUTRAL),
    }
    overbought_states = {
        **neutral_states,
        Timeframe.DAILY.value: wr_state(Timeframe.DAILY, 16.0, WrZone.OVERBOUGHT),
    }
    risk = ("周线 DIF 在零轴上形成第二个顶部（双峰结构）",)

    armed = derive_timing_decision(Direction.BULLISH, neutral_states, risk)
    confirmed = derive_timing_decision(Direction.BULLISH, overbought_states, risk)

    assert armed.action == TimingAction.RISK_WATCH.value
    assert armed.structure_signal == "high_timeframe_second_top"
    assert armed.wr_confirmation == "awaiting_overbought_exit"
    assert confirmed.action == TimingAction.EXIT_WATCH.value
    assert confirmed.wr_confirmation == "daily_overbought_exit"


def test_historical_analysis_marks_current_snapshot_periods_without_wall_clock_dependency() -> None:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH",
        date(2019, 1, 1),
        date(2025, 6, 18),
        AdjustmentMode.QFQ,
    )

    analysis = analyze_multi_timeframe(data.bars)

    assert analysis.macd["monthly"].provisional is True
    assert analysis.macd["weekly"].provisional == (data.as_of.weekday() < 4)
    assert analysis.macd["monthly"].provisional_excluded is True
    assert analysis.macd["monthly"].latest_available_as_of == data.as_of.isoformat()
    assert analysis.macd["monthly"].as_of < analysis.macd["monthly"].latest_available_as_of
    assert analysis.wr["monthly"].as_of == analysis.macd["monthly"].as_of
    assert analysis.wr["monthly"].provisional_excluded is True


def test_incomplete_week_and_month_do_not_change_confirmed_signals() -> None:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH",
        date(2019, 1, 1),
        date(2025, 6, 18),
        AdjustmentMode.QFQ,
    )
    partial = analyze_multi_timeframe(data.bars)
    monthly = aggregate_bars(data.bars, Timeframe.MONTHLY, today=data.as_of)
    confirmed = calculate_signal_indicators(monthly.bars[:-1])

    assert partial.macd["monthly"].dif == round(float(confirmed.dif[-1] or 0), 4)
    assert partial.macd["monthly"].dea == round(float(confirmed.dea[-1] or 0), 4)
    assert partial.wr["monthly"].value == round(float(confirmed.wr[10][-1] or 0), 4)


def test_fast_signal_indicators_match_canonical_macd_and_wr() -> None:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH",
        date(2025, 1, 1),
        date(2026, 8, 16),
        AdjustmentMode.QFQ,
    )

    canonical = calculate_indicators(data.bars)
    fast = calculate_signal_indicators(data.bars)

    assert fast.dif == canonical.dif
    assert fast.dea == canonical.dea
    assert fast.macd == canonical.macd
    assert fast.wr[10] == canonical.wr[10]
