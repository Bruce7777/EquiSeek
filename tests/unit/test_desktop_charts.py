from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from aegisrun.desktop.charts import IndicatorChartWidget, build_chart_data
from aegisrun.desktop.indicator_selector import MultiIndicatorSelector
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.marketdata.timeframes import Timeframe
from aegisrun.research.service import run_research


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def research_result():
    end = date(2026, 8, 11)
    return asyncio.run(
        run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            end - timedelta(days=900),
            end,
            AdjustmentMode.QFQ,
        )
    )


def test_chart_data_aggregates_locally_for_daily_weekly_and_monthly() -> None:
    result = research_result()

    daily = build_chart_data(result, Timeframe.DAILY)
    weekly = build_chart_data(result, Timeframe.WEEKLY, today=date(2026, 8, 11))
    monthly = build_chart_data(result, Timeframe.MONTHLY, today=date(2026, 8, 11))

    assert daily.bars == result.data.bars
    assert len(monthly.bars) < len(weekly.bars) < len(daily.bars)
    assert len(weekly.indicators.macd) == len(weekly.bars)
    assert len(monthly.indicators.wr[10]) == len(monthly.bars)
    assert weekly.latest_complete is False
    assert monthly.latest_complete is False
    assert daily.visible_bars == 120
    assert weekly.visible_bars == 104
    assert monthly.visible_bars == 72


def test_chart_data_uses_research_cutoff_instead_of_wall_clock() -> None:
    end = date(2025, 6, 18)
    result = asyncio.run(
        run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            end - timedelta(days=900),
            end,
            AdjustmentMode.QFQ,
        )
    )

    monthly = build_chart_data(result, Timeframe.MONTHLY)

    assert monthly.latest_complete is False
    assert monthly.bars[-1].trade_date == end


def test_macd_uses_two_lines_and_a_separate_histogram() -> None:
    application()
    chart = IndicatorChartWidget()
    chart.set_mode("MACD")
    chart.set_chart_data(build_chart_data(research_result(), Timeframe.DAILY))

    assert [name for name, _, _ in chart._line_series()] == ["DIF（快线）", "DEA（慢线）"]
    histogram = chart._histogram_series()
    assert histogram is not None
    assert histogram[0] == "MACD柱"
    assert histogram[1] == chart.chart_data.indicators.macd


def test_multi_indicator_selector_enforces_one_to_three_unique_values() -> None:
    application()
    selector = MultiIndicatorSelector(("MA", "MACD", "WR"))

    assert selector.selected_indicators == ("MA", "MACD", "WR")
    assert selector.action_for("BOLL").isEnabled() is False
    assert selector.text() == "MA · MACD · WR  (3/3)"

    selector.action_for("MA").trigger()
    assert selector.selected_indicators == ("MACD", "WR")
    assert selector.action_for("BOLL").isEnabled() is True

    selector.set_selected(("BOLL", "RSI", "ATR"))
    assert selector.selected_indicators == ("BOLL", "RSI", "ATR")
    assert selector.accessibleName() == "技术指标多选，已选 3 项"

    with pytest.raises(ValueError, match="1 至 3"):
        selector.set_selected(())
    with pytest.raises(ValueError, match="1 至 3"):
        selector.set_selected(("MA", "MACD", "WR", "RSI"))
    with pytest.raises(ValueError, match="不能重复"):
        selector.set_selected(("MA", "MA"))
    with pytest.raises(ValueError, match="未知"):
        selector.set_selected(("UNKNOWN",))
