from __future__ import annotations

import csv
import json
from datetime import date, timedelta

import pytest

from aegisrun.marketdata.models import PriceBar
from aegisrun.research.backtest import (
    BacktestOptions,
    HorizonReturn,
    SignalOutcome,
    backtest_report_digest,
    export_backtest_json,
    export_signal_csv,
    parse_horizons,
    summarize_signal_statistics,
    validate_historical_signals,
    walk_forward_backtest,
)
from aegisrun.research.signals import (
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)


def bars(count: int = 30) -> tuple[PriceBar, ...]:
    start = date(2026, 6, 1)
    return tuple(
        PriceBar(
            trade_date=start + timedelta(days=index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100.5 + index,
            volume=100,
            amount=10_000,
        )
        for index in range(count)
    )


def analyzer(seen: list[date]):
    def analyze(history: tuple[PriceBar, ...]) -> MultiTimeframeAnalysis:
        current = history[-1].trade_date
        seen.append(current)
        if current == date(2026, 6, 5):
            action = TimingAction.ENTRY_WATCH
        elif current == date(2026, 6, 12):
            action = TimingAction.EXIT_WATCH
        else:
            action = TimingAction.WAIT
        return MultiTimeframeAnalysis(
            version="test",
            direction=Direction.BULLISH.value,
            direction_label=Direction.BULLISH.label,
            direction_score=6,
            regime="trend",
            macd={},
            wr={},
            risk_flags=(),
            timing=TimingDecision(action.value, action.label, 80, ("确定性测试",)),
            candidate_score=80,
        )

    return analyze


def test_signal_validation_executes_next_bar_and_measures_future_returns() -> None:
    seen: list[date] = []

    signals = validate_historical_signals(
        bars(),
        date(2026, 6, 1),
        date(2026, 6, 15),
        horizons=(5, 10),
        min_history_bars=2,
        analyzer=analyzer(seen),
    )

    assert [item.action for item in signals] == [
        TimingAction.ENTRY_WATCH.value,
        TimingAction.EXIT_WATCH.value,
    ]
    assert signals[0].signal_date == date(2026, 6, 5)
    assert signals[0].execution_date == date(2026, 6, 6)
    assert signals[0].horizons[0].return_pct is not None
    assert signals[0].horizons[0].favorable is True
    assert all(item <= date(2026, 6, 15) for item in seen)


def test_signal_validation_uses_bounded_history_and_tracks_preexisting_state() -> None:
    lengths: list[int] = []

    def always_entry(history: tuple[PriceBar, ...]) -> MultiTimeframeAnalysis:
        lengths.append(len(history))
        return MultiTimeframeAnalysis(
            version="test",
            direction=Direction.BULLISH.value,
            direction_label=Direction.BULLISH.label,
            direction_score=6,
            regime="trend",
            macd={},
            wr={},
            risk_flags=(),
            timing=TimingDecision(
                TimingAction.ENTRY_WATCH.value,
                TimingAction.ENTRY_WATCH.label,
                80,
                ("确定性测试",),
            ),
            candidate_score=80,
        )

    signals = validate_historical_signals(
        bars(40),
        date(2026, 6, 20),
        date(2026, 6, 30),
        min_history_bars=2,
        analysis_window_bars=10,
        analyzer=always_entry,
    )

    assert signals == ()
    assert max(lengths) == 10


def test_walk_forward_backtest_reports_trades_costs_and_drawdown() -> None:
    report = walk_forward_backtest(
        bars(),
        date(2026, 6, 1),
        date(2026, 6, 20),
        transaction_cost_bps=0,
        min_history_bars=2,
        analyzer=analyzer([]),
    )

    assert report.lookahead_safe is True
    assert len(report.trades) == 1
    assert report.trades[0].entry_date == date(2026, 6, 6)
    assert report.trades[0].exit_date == date(2026, 6, 13)
    assert report.total_return_pct > 0
    assert report.excess_return_pct == round(
        report.total_return_pct - report.benchmark_return_pct, 4
    )
    assert report.win_rate_pct == 100
    assert report.max_drawdown_pct <= 0
    assert report.average_trade_return_pct == report.trades[0].return_pct
    assert report.median_trade_return_pct == report.trades[0].return_pct
    assert report.profit_factor is None
    assert 0 < report.exposure_pct < 100
    assert report.horizons == (5, 10, 20)
    assert report.signal_statistics


def test_backtest_rejects_invalid_time_and_cost_parameters() -> None:
    with pytest.raises(ValueError, match="开始日期"):
        walk_forward_backtest(
            bars(),
            date(2026, 6, 20),
            date(2026, 6, 1),
            min_history_bars=2,
            analyzer=analyzer([]),
        )


def test_backtest_options_and_horizon_parser_normalize_and_reject_bad_inputs() -> None:
    assert parse_horizons("20，5, 10 5") == (5, 10, 20)
    options = BacktestOptions(
        date(2025, 1, 1),
        date(2026, 1, 1),
        transaction_cost_bps=12.5,
        horizons=(20, 5, 10, 5),
        min_history_bars=2,
        analysis_window_bars=10,
    )

    assert options.horizons == (5, 10, 20)
    with pytest.raises(ValueError, match="观察周期"):
        parse_horizons("0,5")
    with pytest.raises(ValueError, match="最多"):
        BacktestOptions(
            date(2025, 1, 1),
            date(2026, 1, 1),
            horizons=tuple(range(1, 10)),
            min_history_bars=2,
            analysis_window_bars=10,
        )
    with pytest.raises(ValueError, match="最小预热数"):
        validate_historical_signals(
            bars(),
            date(2026, 6, 1),
            date(2026, 6, 20),
            min_history_bars=1,
            analyzer=analyzer([]),
        )


def test_signal_statistics_distinguish_entry_and_exit_favorable_direction() -> None:
    def outcome(action: TimingAction, value: float) -> SignalOutcome:
        return SignalOutcome(
            signal_date=date(2026, 6, 1),
            execution_date=date(2026, 6, 2),
            action=action.value,
            action_label=action.label,
            direction=Direction.BULLISH.value,
            execution_price=100,
            candidate_score=80,
            horizons=(HorizonReturn(5, date(2026, 6, 6), value, None),),
            reasons=("统计测试",),
        )

    statistics = summarize_signal_statistics(
        (
            outcome(TimingAction.ENTRY_WATCH, 4),
            outcome(TimingAction.ENTRY_WATCH, -2),
            outcome(TimingAction.EXIT_WATCH, -3),
            outcome(TimingAction.EXIT_WATCH, 1),
        ),
        (5,),
    )

    entry, exit_ = statistics
    assert entry.action == TimingAction.ENTRY_WATCH.value
    assert entry.sample_count == 2
    assert entry.favorable_count == 1
    assert entry.favorable_rate_pct == 50
    assert entry.average_return_pct == 1
    assert entry.median_return_pct == 1
    assert entry.best_return_pct == 4
    assert entry.worst_return_pct == -2
    assert exit_.action == TimingAction.EXIT_WATCH.value
    assert exit_.favorable_count == 1


def test_backtest_json_and_csv_exports_are_auditable_and_exclude_private_fields(tmp_path) -> None:
    report = walk_forward_backtest(
        bars(),
        date(2026, 6, 1),
        date(2026, 6, 20),
        transaction_cost_bps=0,
        min_history_bars=2,
        horizons=(5, 10),
        analyzer=analyzer([]),
        symbol="600519.SH",
    )
    json_path = tmp_path / "report.json"
    csv_path = tmp_path / "signals.csv"

    export_backtest_json(report, json_path)
    export_signal_csv(report, csv_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["symbol"] == "600519.SH"
    assert payload["report_sha256"] == backtest_report_digest(report)
    assert payload["lookahead_safe"] is True
    assert payload["horizons"] == [5, 10]
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows
    assert {"return_5d_pct", "return_10d_pct", "reasons"} <= set(rows[0])
    exported = json_path.read_text(encoding="utf-8") + csv_path.read_text(encoding="utf-8-sig")
    assert "api_key" not in exported
    assert "cost_price" not in exported


def test_historical_signal_metadata_does_not_change_when_later_prices_change() -> None:
    original = bars(45)
    mutated = tuple(
        PriceBar(
            item.trade_date,
            item.open * (2 if index > 25 else 1),
            item.high * (2 if index > 25 else 1),
            item.low * (2 if index > 25 else 1),
            item.close * (2 if index > 25 else 1),
            item.volume,
            item.amount,
        )
        for index, item in enumerate(original)
    )

    first = validate_historical_signals(
        original,
        date(2026, 6, 1),
        date(2026, 6, 20),
        min_history_bars=2,
        analyzer=analyzer([]),
    )
    second = validate_historical_signals(
        mutated,
        date(2026, 6, 1),
        date(2026, 6, 20),
        min_history_bars=2,
        analyzer=analyzer([]),
    )

    def metadata(signals: tuple[SignalOutcome, ...]) -> list[tuple[date, date, str, float]]:
        return [
            (item.signal_date, item.execution_date, item.action, item.execution_price)
            for item in signals
        ]

    assert metadata(first) == metadata(second)
    with pytest.raises(ValueError, match="分析窗口"):
        walk_forward_backtest(
            bars(),
            date(2026, 6, 1),
            date(2026, 6, 20),
            min_history_bars=10,
            analysis_window_bars=5,
            analyzer=analyzer([]),
        )
    with pytest.raises(ValueError, match="交易成本"):
        walk_forward_backtest(
            bars(),
            date(2026, 6, 1),
            date(2026, 6, 20),
            transaction_cost_bps=-1,
            min_history_bars=2,
            analyzer=analyzer([]),
        )
