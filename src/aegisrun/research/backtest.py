from __future__ import annotations

import csv
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from statistics import mean, median
from typing import Any

from aegisrun.core.security import canonical_hash
from aegisrun.marketdata.models import PriceBar
from aegisrun.research.signals import (
    MultiTimeframeAnalysis,
    TimingAction,
    analyze_multi_timeframe,
)

StrategyAnalyzer = Callable[[tuple[PriceBar, ...]], MultiTimeframeAnalysis]


def parse_horizons(value: str) -> tuple[int, ...]:
    try:
        horizons = tuple(int(item) for item in re.split(r"[,，;；\s]+", value.strip()) if item)
    except ValueError as error:
        raise ValueError("观察周期必须是以逗号分隔的整数") from error
    return _normalize_horizons(horizons)


def _normalize_horizons(values: tuple[int, ...]) -> tuple[int, ...]:
    if not values:
        raise ValueError("至少需要一个回测观察周期")
    if any(type(value) is not int or value < 1 or value > 250 for value in values):
        raise ValueError("观察周期必须是 1–250 之间的交易日整数")
    normalized = tuple(sorted(set(values)))
    if len(normalized) > 8:
        raise ValueError("回测观察周期最多设置 8 个")
    return normalized


@dataclass(frozen=True, slots=True)
class BacktestOptions:
    evaluation_start: date
    evaluation_end: date
    transaction_cost_bps: float = 10.0
    horizons: tuple[int, ...] = (5, 10, 20)
    min_history_bars: int = 520
    analysis_window_bars: int = 800

    def __post_init__(self) -> None:
        if self.evaluation_start >= self.evaluation_end:
            raise ValueError("回测开始日期必须早于结束日期")
        if not math.isfinite(self.transaction_cost_bps) or not (
            0 <= self.transaction_cost_bps < 1_000
        ):
            raise ValueError("交易成本基点必须在 [0, 1000) 范围")
        if self.min_history_bars < 2:
            raise ValueError("最小预热数不能小于 2")
        if self.analysis_window_bars < self.min_history_bars:
            raise ValueError("分析窗口不能小于最小预热数")
        object.__setattr__(self, "horizons", _normalize_horizons(self.horizons))


@dataclass(frozen=True, slots=True)
class HorizonReturn:
    trading_days: int
    outcome_date: date | None
    return_pct: float | None
    favorable: bool | None


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    signal_date: date
    execution_date: date
    action: str
    action_label: str
    direction: str
    execution_price: float
    candidate_score: int
    horizons: tuple[HorizonReturn, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    entry_signal_date: date
    entry_date: date
    entry_price: float
    exit_signal_date: date | None
    exit_date: date
    exit_price: float
    bars_held: int
    return_pct: float


@dataclass(frozen=True, slots=True)
class HorizonStatistics:
    action: str
    action_label: str
    trading_days: int
    sample_count: int
    favorable_count: int
    favorable_rate_pct: float | None
    average_return_pct: float | None
    median_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None


@dataclass(frozen=True, slots=True)
class BacktestReport:
    version: str
    symbol: str
    evaluation_start: date
    evaluation_end: date
    transaction_cost_bps: float
    horizons: tuple[int, ...]
    signals: tuple[SignalOutcome, ...]
    trades: tuple[BacktestTrade, ...]
    total_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    win_rate_pct: float | None
    max_drawdown_pct: float
    average_trade_return_pct: float | None
    median_trade_return_pct: float | None
    profit_factor: float | None
    exposure_pct: float
    signal_statistics: tuple[HorizonStatistics, ...]
    lookahead_safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "transaction_cost_bps": self.transaction_cost_bps,
            "horizons": list(self.horizons),
            "signals": [
                {
                    "signal_date": signal.signal_date.isoformat(),
                    "execution_date": signal.execution_date.isoformat(),
                    "action": signal.action,
                    "action_label": signal.action_label,
                    "direction": signal.direction,
                    "execution_price": signal.execution_price,
                    "candidate_score": signal.candidate_score,
                    "reasons": list(signal.reasons),
                    "horizons": [
                        {
                            "trading_days": outcome.trading_days,
                            "outcome_date": (
                                outcome.outcome_date.isoformat() if outcome.outcome_date else None
                            ),
                            "return_pct": outcome.return_pct,
                            "favorable": outcome.favorable,
                        }
                        for outcome in signal.horizons
                    ],
                }
                for signal in self.signals
            ],
            "trades": [
                {
                    "entry_signal_date": trade.entry_signal_date.isoformat(),
                    "entry_date": trade.entry_date.isoformat(),
                    "entry_price": trade.entry_price,
                    "exit_signal_date": (
                        trade.exit_signal_date.isoformat() if trade.exit_signal_date else None
                    ),
                    "exit_date": trade.exit_date.isoformat(),
                    "exit_price": trade.exit_price,
                    "bars_held": trade.bars_held,
                    "return_pct": trade.return_pct,
                }
                for trade in self.trades
            ],
            "total_return_pct": self.total_return_pct,
            "benchmark_return_pct": self.benchmark_return_pct,
            "excess_return_pct": self.excess_return_pct,
            "win_rate_pct": self.win_rate_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "average_trade_return_pct": self.average_trade_return_pct,
            "median_trade_return_pct": self.median_trade_return_pct,
            "profit_factor": self.profit_factor,
            "exposure_pct": self.exposure_pct,
            "signal_statistics": [
                {
                    "action": item.action,
                    "action_label": item.action_label,
                    "trading_days": item.trading_days,
                    "sample_count": item.sample_count,
                    "favorable_count": item.favorable_count,
                    "favorable_rate_pct": item.favorable_rate_pct,
                    "average_return_pct": item.average_return_pct,
                    "median_return_pct": item.median_return_pct,
                    "best_return_pct": item.best_return_pct,
                    "worst_return_pct": item.worst_return_pct,
                }
                for item in self.signal_statistics
            ],
            "lookahead_safe": self.lookahead_safe,
        }


def summarize_signal_statistics(
    signals: tuple[SignalOutcome, ...],
    horizons: tuple[int, ...],
) -> tuple[HorizonStatistics, ...]:
    result: list[HorizonStatistics] = []
    for action in (TimingAction.ENTRY_WATCH, TimingAction.EXIT_WATCH):
        matching = tuple(signal for signal in signals if signal.action == action.value)
        for horizon in _normalize_horizons(horizons):
            values = [
                outcome.return_pct
                for signal in matching
                for outcome in signal.horizons
                if outcome.trading_days == horizon and outcome.return_pct is not None
            ]
            favorable = sum(_favorable(action.value, value) for value in values)
            best = None
            worst = None
            if values:
                best = min(values) if action is TimingAction.EXIT_WATCH else max(values)
                worst = max(values) if action is TimingAction.EXIT_WATCH else min(values)
            result.append(
                HorizonStatistics(
                    action=action.value,
                    action_label=action.label,
                    trading_days=horizon,
                    sample_count=len(values),
                    favorable_count=favorable,
                    favorable_rate_pct=(
                        round(favorable / len(values) * 100, 2) if values else None
                    ),
                    average_return_pct=round(mean(values), 4) if values else None,
                    median_return_pct=round(median(values), 4) if values else None,
                    best_return_pct=round(best, 4) if best is not None else None,
                    worst_return_pct=round(worst, 4) if worst is not None else None,
                )
            )
    return tuple(result)


def backtest_report_digest(report: BacktestReport) -> str:
    return canonical_hash(report.to_dict())


def _atomic_write_text(path: Path, text: str) -> None:
    destination = path.expanduser()
    if destination.exists() and destination.is_dir():
        raise ValueError("导出目标不能是目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_backtest_json(report: BacktestReport, path: Path) -> None:
    payload = report.to_dict()
    payload["report_sha256"] = backtest_report_digest(report)
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def export_signal_csv(report: BacktestReport, path: Path) -> None:
    horizon_fields = [
        field
        for horizon in report.horizons
        for field in (
            f"outcome_date_{horizon}d",
            f"return_{horizon}d_pct",
            f"favorable_{horizon}d",
        )
    ]
    fieldnames = [
        "symbol",
        "signal_date",
        "execution_date",
        "action",
        "action_label",
        "direction",
        "execution_price",
        "candidate_score",
        "reasons",
        *horizon_fields,
    ]
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for signal in report.signals:
        row: dict[str, object] = {
            "symbol": report.symbol,
            "signal_date": signal.signal_date.isoformat(),
            "execution_date": signal.execution_date.isoformat(),
            "action": signal.action,
            "action_label": signal.action_label,
            "direction": signal.direction,
            "execution_price": signal.execution_price,
            "candidate_score": signal.candidate_score,
            "reasons": "；".join(signal.reasons),
        }
        by_horizon = {item.trading_days: item for item in signal.horizons}
        for horizon in report.horizons:
            outcome = by_horizon[horizon]
            row[f"outcome_date_{horizon}d"] = (
                outcome.outcome_date.isoformat() if outcome.outcome_date else ""
            )
            row[f"return_{horizon}d_pct"] = "" if outcome.return_pct is None else outcome.return_pct
            row[f"favorable_{horizon}d"] = (
                "" if outcome.favorable is None else str(outcome.favorable).lower()
            )
        writer.writerow(row)
    _atomic_write_text(path, "\ufeff" + buffer.getvalue())


def _validate_bars(bars: tuple[PriceBar, ...]) -> None:
    if len(bars) < 2:
        raise ValueError("回测至少需要 2 根日 K")
    if list(bars) != sorted(bars, key=lambda item: item.trade_date):
        raise ValueError("回测日 K 必须按交易日升序排列")


def _favorable(action: str, return_pct: float) -> bool:
    if action == TimingAction.ENTRY_WATCH.value:
        return return_pct > 0
    if action == TimingAction.EXIT_WATCH.value:
        return return_pct < 0
    return False


def validate_historical_signals(
    bars_input: tuple[PriceBar, ...] | list[PriceBar],
    signal_start: date,
    signal_end: date,
    *,
    horizons: tuple[int, ...] = (5, 10, 20),
    min_history_bars: int = 520,
    analysis_window_bars: int = 800,
    analyzer: StrategyAnalyzer = analyze_multi_timeframe,
) -> tuple[SignalOutcome, ...]:
    bars = tuple(bars_input)
    _validate_bars(bars)
    if signal_start > signal_end:
        raise ValueError("信号开始日期不能晚于结束日期")
    horizons = _normalize_horizons(horizons)
    if min_history_bars < 2:
        raise ValueError("最小预热数不能小于 2")
    if analysis_window_bars < min_history_bars:
        raise ValueError("分析窗口不能小于最小预热数")
    outcomes: list[SignalOutcome] = []
    previous_action = TimingAction.WAIT.value
    first_evaluation_index = next(
        (index for index, bar in enumerate(bars) if bar.trade_date >= signal_start),
        len(bars) - 1,
    )
    loop_start = max(1, min_history_bars, first_evaluation_index - 1)
    for index in range(loop_start, len(bars) - 1):
        signal_date = bars[index].trade_date
        if signal_date > signal_end:
            break
        history_start = max(0, index + 1 - analysis_window_bars)
        analysis = analyzer(bars[history_start : index + 1])
        action = analysis.timing.action
        if signal_date < signal_start:
            previous_action = action
            continue
        actionable = action in {
            TimingAction.ENTRY_WATCH.value,
            TimingAction.EXIT_WATCH.value,
        }
        if not actionable or action == previous_action:
            previous_action = action
            continue
        execution_index = index + 1
        execution_price = bars[execution_index].open
        horizon_results: list[HorizonReturn] = []
        for horizon in horizons:
            outcome_index = execution_index + horizon - 1
            if outcome_index >= len(bars):
                horizon_results.append(HorizonReturn(horizon, None, None, None))
                continue
            future = bars[outcome_index]
            return_pct = (future.close / execution_price - 1.0) * 100
            horizon_results.append(
                HorizonReturn(
                    horizon,
                    future.trade_date,
                    round(return_pct, 4),
                    _favorable(action, return_pct),
                )
            )
        outcomes.append(
            SignalOutcome(
                signal_date=signal_date,
                execution_date=bars[execution_index].trade_date,
                action=action,
                action_label=analysis.timing.label,
                direction=analysis.direction,
                execution_price=round(execution_price, 4),
                candidate_score=analysis.candidate_score,
                horizons=tuple(horizon_results),
                reasons=analysis.timing.reasons,
            )
        )
        previous_action = action
    return tuple(outcomes)


def walk_forward_backtest(
    bars_input: tuple[PriceBar, ...] | list[PriceBar],
    evaluation_start: date,
    evaluation_end: date,
    *,
    transaction_cost_bps: float = 10.0,
    horizons: tuple[int, ...] = (5, 10, 20),
    min_history_bars: int = 520,
    analysis_window_bars: int = 800,
    analyzer: StrategyAnalyzer = analyze_multi_timeframe,
    symbol: str = "",
) -> BacktestReport:
    bars = tuple(bars_input)
    _validate_bars(bars)
    options = BacktestOptions(
        evaluation_start,
        evaluation_end,
        transaction_cost_bps,
        horizons,
        min_history_bars,
        analysis_window_bars,
    )
    signals = validate_historical_signals(
        bars,
        options.evaluation_start,
        options.evaluation_end,
        horizons=options.horizons,
        min_history_bars=options.min_history_bars,
        analysis_window_bars=options.analysis_window_bars,
        analyzer=analyzer,
    )
    signal_by_date = {item.signal_date: item for item in signals}
    indices = [
        index
        for index, bar in enumerate(bars)
        if options.evaluation_start <= bar.trade_date <= options.evaluation_end
    ]
    if len(indices) < 2:
        raise ValueError("所选回测区间有效交易日不足")
    start_index, end_index = indices[0], indices[-1]
    cost = options.transaction_cost_bps / 10_000.0
    capital = 1.0
    shares = 0.0
    entry: tuple[date, date, float, int] | None = None
    trades: list[BacktestTrade] = []
    equity_curve: list[float] = []
    invested_closes = 0
    pending: SignalOutcome | None = None
    for index in range(start_index, end_index + 1):
        bar = bars[index]
        if pending is not None and pending.execution_date == bar.trade_date:
            if pending.action == TimingAction.ENTRY_WATCH.value and entry is None:
                entry_price = bar.open * (1.0 + cost)
                shares = capital / entry_price
                capital = 0.0
                entry = (pending.signal_date, bar.trade_date, entry_price, index)
            elif pending.action == TimingAction.EXIT_WATCH.value and entry is not None:
                exit_price = bar.open * (1.0 - cost)
                capital = shares * exit_price
                entry_signal, entry_date, entry_price, entry_index = entry
                trades.append(
                    BacktestTrade(
                        entry_signal,
                        entry_date,
                        round(entry_price, 4),
                        pending.signal_date,
                        bar.trade_date,
                        round(exit_price, 4),
                        index - entry_index,
                        round((exit_price / entry_price - 1.0) * 100, 4),
                    )
                )
                shares = 0.0
                entry = None
            pending = None
        signal = signal_by_date.get(bar.trade_date)
        if signal is not None:
            pending = signal
        if entry is not None:
            invested_closes += 1
        equity_curve.append(capital if entry is None else shares * bar.close)

    if entry is not None:
        final = bars[end_index]
        exit_price = final.close * (1.0 - cost)
        capital = shares * exit_price
        entry_signal, entry_date, entry_price, entry_index = entry
        trades.append(
            BacktestTrade(
                entry_signal,
                entry_date,
                round(entry_price, 4),
                None,
                final.trade_date,
                round(exit_price, 4),
                end_index - entry_index,
                round((exit_price / entry_price - 1.0) * 100, 4),
            )
        )
        equity_curve[-1] = capital

    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
    wins = sum(trade.return_pct > 0 for trade in trades)
    benchmark_entry = bars[start_index].open * (1.0 + cost)
    benchmark_exit = bars[end_index].close * (1.0 - cost)
    trade_returns = [trade.return_pct for trade in trades]
    gains = sum(value for value in trade_returns if value > 0)
    losses = abs(sum(value for value in trade_returns if value < 0))
    total_return_pct = round((capital - 1.0) * 100, 4)
    benchmark_return_pct = round((benchmark_exit / benchmark_entry - 1.0) * 100, 4)
    return BacktestReport(
        version="macd-wr-walk-forward-2026.08.3",
        symbol=symbol,
        evaluation_start=bars[start_index].trade_date,
        evaluation_end=bars[end_index].trade_date,
        transaction_cost_bps=options.transaction_cost_bps,
        horizons=options.horizons,
        signals=signals,
        trades=tuple(trades),
        total_return_pct=total_return_pct,
        benchmark_return_pct=benchmark_return_pct,
        excess_return_pct=round(total_return_pct - benchmark_return_pct, 4),
        win_rate_pct=round(wins / len(trades) * 100, 2) if trades else None,
        max_drawdown_pct=round(max_drawdown * 100, 4),
        average_trade_return_pct=(round(mean(trade_returns), 4) if trade_returns else None),
        median_trade_return_pct=(round(median(trade_returns), 4) if trade_returns else None),
        profit_factor=round(gains / losses, 4) if losses > 0 else None,
        exposure_pct=round(invested_closes / len(indices) * 100, 2),
        signal_statistics=summarize_signal_statistics(signals, options.horizons),
    )


def build_backtest_summary(report: BacktestReport) -> str:
    win_rate = "无完整交易" if report.win_rate_pct is None else f"{report.win_rate_pct:.2f}%"
    average_trade = (
        "无完整交易"
        if report.average_trade_return_pct is None
        else f"{report.average_trade_return_pct:.2f}%"
    )
    profit_factor = (
        "无亏损交易/不可计算" if report.profit_factor is None else f"{report.profit_factor:.2f}"
    )
    lines = [
        "Walk-forward 策略回测",
        f"- 证券：{report.symbol or '未指定'}",
        f"- 区间：{report.evaluation_start.isoformat()} 至 {report.evaluation_end.isoformat()}",
        f"- 规则版本：{report.version}",
        f"- 报告摘要 SHA-256：{backtest_report_digest(report)}",
        f"- 信号：{len(report.signals)} 个；完整交易：{len(report.trades)} 笔",
        f"- 策略收益：{report.total_return_pct:.2f}%",
        f"- 买入持有基准：{report.benchmark_return_pct:.2f}%",
        f"- 超额收益：{report.excess_return_pct:.2f}%",
        f"- 胜率：{win_rate}；平均每笔：{average_trade}；利润因子：{profit_factor}",
        f"- 最大回撤：{report.max_drawdown_pct:.2f}%；持仓暴露：{report.exposure_pct:.2f}%",
        f"- 单边交易成本：{report.transaction_cost_bps:.1f} bps",
        f"- 后验观察周期：{', '.join(str(value) for value in report.horizons)} 个交易日",
        "- 防未来函数：当日只使用当日及以前数据，信号最早在下一交易日开盘执行。",
        "",
        "分周期信号统计",
    ]
    for item in report.signal_statistics:
        rate = "数据不足" if item.favorable_rate_pct is None else f"{item.favorable_rate_pct:.2f}%"
        average = (
            "数据不足" if item.average_return_pct is None else f"{item.average_return_pct:.2f}%"
        )
        lines.append(
            f"- {item.action_label} / {item.trading_days} 日："
            f"样本 {item.sample_count}；有利率 {rate}；平均 {average}"
        )
    lines.extend(
        (
            "",
            "历史信号后验",
        )
    )
    if not report.signals:
        lines.append("- 区间内没有触发技术入场/退出观察窗口。")
    for signal in report.signals[-20:]:
        outcomes = "；".join(
            f"{item.trading_days}日="
            f"{'数据不足' if item.return_pct is None else f'{item.return_pct:.2f}%'}"
            for item in signal.horizons
        )
        lines.append(
            f"- {signal.signal_date.isoformat()} {signal.action_label} → "
            f"{signal.execution_date.isoformat()} 开盘；{outcomes}"
        )
    lines.extend(("", "- 历史回测不代表未来表现，不构成个性化投资建议。"))
    return "\n".join(lines)
