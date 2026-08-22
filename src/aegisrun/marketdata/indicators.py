from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from aegisrun.marketdata.models import PriceBar

type NullableSeries = tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class IndicatorSet:
    version: str
    ma: dict[int, NullableSeries]
    dif: NullableSeries
    dea: NullableSeries
    macd: NullableSeries
    k: NullableSeries
    d: NullableSeries
    j: NullableSeries
    rsi: dict[int, NullableSeries]
    atr: dict[int, NullableSeries]
    boll_mid: NullableSeries
    boll_upper: NullableSeries
    boll_lower: NullableSeries
    wr: dict[int, NullableSeries]


@dataclass(frozen=True, slots=True)
class SignalIndicatorSet:
    """Minimal MACD/WR series used by multi-timeframe walk-forward analysis."""

    dif: NullableSeries
    dea: NullableSeries
    macd: NullableSeries
    wr: dict[int, NullableSeries]


def _rolling_mean(values: list[float], period: int) -> NullableSeries:
    result: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            result[index] = running / period
    return tuple(result)


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def _rsi(values: list[float], period: int) -> NullableSeries:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return tuple(result)
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def score(gain: float, loss: float) -> float:
        if math.isclose(gain, 0.0) and math.isclose(loss, 0.0):
            return 50.0
        if math.isclose(loss, 0.0):
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = score(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        result[index] = score(avg_gain, avg_loss)
    return tuple(result)


def _atr(bars: list[PriceBar], period: int) -> NullableSeries:
    result: list[float | None] = [None] * len(bars)
    if not bars:
        return tuple(result)
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    if len(true_ranges) < period:
        return tuple(result)
    value = sum(true_ranges[:period]) / period
    result[period - 1] = value
    for index in range(period, len(true_ranges)):
        value = (value * (period - 1) + true_ranges[index]) / period
        result[index] = value
    return tuple(result)


def _wr(bars: list[PriceBar], period: int) -> NullableSeries:
    result: list[float | None] = [None] * len(bars)
    for index in range(period - 1, len(bars)):
        window = bars[index - period + 1 : index + 1]
        highest = max(item.high for item in window)
        lowest = min(item.low for item in window)
        width = highest - lowest
        result[index] = (
            50.0 if math.isclose(width, 0.0) else (highest - bars[index].close) / width * 100
        )
    return tuple(result)


def calculate_signal_indicators(
    bars_input: list[PriceBar] | tuple[PriceBar, ...],
) -> SignalIndicatorSet:
    bars = list(bars_input)
    if not bars:
        raise ValueError("at least one price bar is required")
    if bars != sorted(bars, key=lambda item: item.trade_date):
        raise ValueError("price bars must be sorted by trade_date")
    closes = [bar.close for bar in bars]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif_values = [left - right for left, right in zip(ema12, ema26, strict=True)]
    dea_values = _ema(dif_values, 9)
    macd_values = [2.0 * (dif - dea) for dif, dea in zip(dif_values, dea_values, strict=True)]
    return SignalIndicatorSet(
        dif=tuple(dif_values),
        dea=tuple(dea_values),
        macd=tuple(macd_values),
        wr={10: _wr(bars, 10)},
    )


def calculate_indicators(bars_input: list[PriceBar] | tuple[PriceBar, ...]) -> IndicatorSet:
    bars = list(bars_input)
    if not bars:
        raise ValueError("at least one price bar is required")
    if bars != sorted(bars, key=lambda item: item.trade_date):
        raise ValueError("price bars must be sorted by trade_date")
    closes = [bar.close for bar in bars]
    ma = {period: _rolling_mean(closes, period) for period in (5, 10, 20, 30, 60)}

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif_values = [left - right for left, right in zip(ema12, ema26, strict=True)]
    dea_values = _ema(dif_values, 9)
    macd_values = [2.0 * (dif - dea) for dif, dea in zip(dif_values, dea_values, strict=True)]

    k_values: list[float | None] = []
    d_values: list[float | None] = []
    j_values: list[float | None] = []
    previous_k = 50.0
    previous_d = 50.0
    for index, bar in enumerate(bars):
        start = max(0, index - 8)
        window = bars[start : index + 1]
        highest = max(item.high for item in window)
        lowest = min(item.low for item in window)
        width = highest - lowest
        rsv = 50.0 if math.isclose(width, 0.0) else (bar.close - lowest) / width * 100
        previous_k = (2.0 * previous_k + rsv) / 3.0
        previous_d = (2.0 * previous_d + previous_k) / 3.0
        k_values.append(previous_k)
        d_values.append(previous_d)
        j_values.append(3.0 * previous_k - 2.0 * previous_d)

    boll_mid = ma[20]
    boll_upper: list[float | None] = [None] * len(bars)
    boll_lower: list[float | None] = [None] * len(bars)
    for index in range(19, len(bars)):
        close_window = closes[index - 19 : index + 1]
        mid = boll_mid[index]
        assert mid is not None
        deviation = statistics.pstdev(close_window)
        boll_upper[index] = mid + 2.0 * deviation
        boll_lower[index] = mid - 2.0 * deviation

    return IndicatorSet(
        version="canonical-cn-2026.08.1",
        ma=ma,
        dif=tuple(dif_values),
        dea=tuple(dea_values),
        macd=tuple(macd_values),
        k=tuple(k_values),
        d=tuple(d_values),
        j=tuple(j_values),
        rsi={period: _rsi(closes, period) for period in (6, 12, 24)},
        atr={period: _atr(bars, period) for period in (14, 20)},
        boll_mid=boll_mid,
        boll_upper=tuple(boll_upper),
        boll_lower=tuple(boll_lower),
        wr={period: _wr(bars, period) for period in (6, 10)},
    )
