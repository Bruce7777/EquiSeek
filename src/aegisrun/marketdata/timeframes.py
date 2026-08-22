from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from aegisrun.marketdata.models import PriceBar


class Timeframe(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def label(self) -> str:
        return {
            self.DAILY: "日线",
            self.WEEKLY: "周线",
            self.MONTHLY: "月线",
        }[self]


@dataclass(frozen=True, slots=True)
class TimeframeBars:
    timeframe: Timeframe
    bars: tuple[PriceBar, ...]
    latest_complete: bool


def _period_key(value: date, timeframe: Timeframe) -> tuple[int, int]:
    if timeframe is Timeframe.WEEKLY:
        iso = value.isocalendar()
        return iso.year, iso.week
    return value.year, value.month


def _latest_complete(last_date: date, timeframe: Timeframe, today: date) -> bool:
    if timeframe is Timeframe.DAILY:
        return True
    if _period_key(last_date, timeframe) < _period_key(today, timeframe):
        return True
    if timeframe is Timeframe.WEEKLY:
        return last_date.weekday() >= 4
    return False


def aggregate_bars(
    bars_input: tuple[PriceBar, ...] | list[PriceBar],
    timeframe: Timeframe,
    *,
    today: date | None = None,
) -> TimeframeBars:
    bars = tuple(bars_input)
    if not bars:
        raise ValueError("at least one price bar is required")
    if list(bars) != sorted(bars, key=lambda item: item.trade_date):
        raise ValueError("price bars must be sorted by trade_date")
    if timeframe is Timeframe.DAILY:
        return TimeframeBars(timeframe, bars, True)

    grouped: list[list[PriceBar]] = []
    current_key: tuple[int, int] | None = None
    for bar in bars:
        key = _period_key(bar.trade_date, timeframe)
        if key != current_key:
            grouped.append([])
            current_key = key
        grouped[-1].append(bar)

    aggregated: list[PriceBar] = []
    previous_close: float | None = None
    for group in grouped:
        first = group[0]
        last = group[-1]
        aggregated.append(
            PriceBar(
                trade_date=last.trade_date,
                open=first.open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=last.close,
                volume=sum(item.volume for item in group),
                amount=sum(item.amount for item in group),
                pre_close=first.pre_close if first.pre_close is not None else previous_close,
            )
        )
        previous_close = last.close
    reference_date = today or date.today()
    return TimeframeBars(
        timeframe,
        tuple(aggregated),
        _latest_complete(aggregated[-1].trade_date, timeframe, reference_date),
    )
