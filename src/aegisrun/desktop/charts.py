from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from aegisrun.marketdata.indicators import IndicatorSet, calculate_indicators
from aegisrun.marketdata.models import MarketDataSet, PriceBar
from aegisrun.marketdata.timeframes import Timeframe, aggregate_bars

if TYPE_CHECKING:
    from aegisrun.research.service import ResearchResult


BACKGROUND = QColor("#08121B")
GRID = QColor("#1D2C38")
TEXT = QColor("#91A6B5")
UP = QColor("#F05B61")
DOWN = QColor("#36B37E")
MA5 = QColor("#55A7FF")
MA20 = QColor("#EABF5A")
CYAN = QColor("#35D0A0")
VIOLET = QColor("#B18CFF")
AMBER = QColor("#F0A45D")
PINK = QColor("#F07DB1")


@dataclass(frozen=True, slots=True)
class ChartData:
    timeframe: Timeframe
    bars: tuple[PriceBar, ...]
    indicators: IndicatorSet
    latest_complete: bool

    @property
    def visible_bars(self) -> int:
        return {
            Timeframe.DAILY: 120,
            Timeframe.WEEKLY: 104,
            Timeframe.MONTHLY: 72,
        }[self.timeframe]


def build_chart_data(
    result: ResearchResult,
    timeframe: Timeframe,
    *,
    today: date | None = None,
) -> ChartData:
    """Build a local chart view without fetching or mutating market data."""

    aggregated = aggregate_bars(
        result.data.bars,
        timeframe,
        today=today or result.data.as_of,
    )
    indicators = (
        result.indicators if timeframe is Timeframe.DAILY else calculate_indicators(aggregated.bars)
    )
    return ChartData(
        timeframe=timeframe,
        bars=aggregated.bars,
        indicators=indicators,
        latest_complete=aggregated.latest_complete,
    )


def build_dataset_chart_data(
    data: MarketDataSet,
    timeframe: Timeframe,
    *,
    today: date | None = None,
) -> ChartData:
    """Build the same chart contract for benchmark or sector-proxy datasets."""

    aggregated = aggregate_bars(data.bars, timeframe, today=today or data.as_of)
    return ChartData(
        timeframe=timeframe,
        bars=aggregated.bars,
        indicators=calculate_indicators(aggregated.bars),
        latest_complete=aggregated.latest_complete,
    )


def _bounds(values: Iterable[float]) -> tuple[float, float]:
    items = list(values)
    low = min(items)
    high = max(items)
    if high == low:
        margin = max(abs(high) * 0.05, 1.0)
        return low - margin, high + margin
    margin = (high - low) * 0.06
    return low - margin, high + margin


class PriceChartWidget(QWidget):
    OVERLAY_MODES = ("MA", "BOLL")

    def __init__(self) -> None:
        super().__init__()
        self._chart_data: ChartData | None = None
        self._overlays: tuple[str, ...] = ("MA",)
        self.setMinimumHeight(280)
        self.setAccessibleName("日K线、成交量和主图指标")

    @property
    def chart_data(self) -> ChartData | None:
        return self._chart_data

    @property
    def overlays(self) -> tuple[str, ...]:
        return self._overlays

    def set_result(self, result: ResearchResult) -> None:
        self.set_chart_data(build_chart_data(result, Timeframe.DAILY))

    def set_chart_data(self, data: ChartData) -> None:
        self._chart_data = data
        self.setAccessibleName(f"{data.timeframe.label}K线、成交量和主图指标")
        self.update()

    def clear_data(self) -> None:
        self._chart_data = None
        self.update()

    def set_overlays(self, indicators: Sequence[str]) -> None:
        self._overlays = tuple(item for item in indicators if item in self.OVERLAY_MODES)
        self.update()

    def paintEvent(self, _: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._chart_data is None:
            painter.setPen(TEXT)
            painter.setFont(QFont("", 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "选择数据源并开始分析")
            return
        data = self._chart_data
        bars = list(data.bars[-data.visible_bars :])
        offset = len(data.bars) - len(bars)
        chart = QRectF(58, 28, max(self.width() - 78, 100), max(self.height() * 0.7 - 34, 145))
        volume = QRectF(
            58, chart.bottom() + 20, chart.width(), max(self.height() - chart.bottom() - 46, 44)
        )
        self._grid(painter, chart, 5)
        indicator_values = self._price_overlay_values(data.indicators, offset)
        price_values = [value for bar in bars for value in (bar.low, bar.high)]
        price_values.extend(indicator_values)
        low, high = _bounds(price_values)
        slot = chart.width() / max(len(bars), 1)
        body_width = max(min(slot * 0.58, 8.0), 1.5)

        def price_y(value: float) -> float:
            return chart.bottom() - (value - low) / (high - low) * chart.height()

        for index, bar in enumerate(bars):
            x = chart.left() + (index + 0.5) * slot
            color = UP if bar.close >= bar.open else DOWN
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, price_y(bar.low)), QPointF(x, price_y(bar.high)))
            top = price_y(max(bar.open, bar.close))
            bottom = price_y(min(bar.open, bar.close))
            body = QRectF(x - body_width / 2, top, body_width, max(bottom - top, 1.0))
            painter.fillRect(body, color)

        legends: list[tuple[str, QColor]] = []
        if "MA" in self._overlays:
            self._line(painter, chart, data.indicators.ma[5][offset:], price_y, MA5)
            self._line(painter, chart, data.indicators.ma[20][offset:], price_y, MA20)
            legends.extend((("MA5", MA5), ("MA20", MA20)))
        if "BOLL" in self._overlays:
            self._line(painter, chart, data.indicators.boll_upper[offset:], price_y, PINK)
            self._line(painter, chart, data.indicators.boll_mid[offset:], price_y, CYAN)
            self._line(painter, chart, data.indicators.boll_lower[offset:], price_y, VIOLET)
            legends.extend((("BOLL-U", PINK), ("BOLL-M", CYAN), ("BOLL-L", VIOLET)))
        self._axis_labels(painter, chart, low, high)

        max_volume = max((bar.volume for bar in bars), default=1.0) or 1.0
        for index, bar in enumerate(bars):
            x = volume.left() + (index + 0.5) * slot
            height = bar.volume / max_volume * volume.height()
            color = QColor(UP if bar.close >= bar.open else DOWN)
            color.setAlpha(115)
            painter.fillRect(
                QRectF(x - body_width / 2, volume.bottom() - height, body_width, height), color
            )
        painter.setPen(TEXT)
        painter.setFont(QFont("", 9))
        painter.drawText(10, int(volume.top() + 12), "成交量")
        x = chart.left()
        for name, color in legends:
            painter.setPen(color)
            painter.drawText(int(x), 18, name)
            x += painter.fontMetrics().horizontalAdvance(name) + 16
        status = f"{data.timeframe.label}K"
        if not data.latest_complete:
            status += " · 最新柱形成中（仅供观察）"
            painter.setPen(AMBER)
        else:
            painter.setPen(TEXT)
        painter.drawText(
            QRectF(chart.left(), 4, chart.width(), 18),
            Qt.AlignmentFlag.AlignRight,
            status,
        )
        painter.setPen(TEXT)
        painter.drawText(
            QRectF(chart.left(), volume.bottom() + 4, chart.width(), 18),
            Qt.AlignmentFlag.AlignLeft,
            bars[0].trade_date.isoformat(),
        )
        painter.drawText(
            QRectF(chart.left(), volume.bottom() + 4, chart.width(), 18),
            Qt.AlignmentFlag.AlignRight,
            bars[-1].trade_date.isoformat(),
        )

    def _price_overlay_values(self, indicators: IndicatorSet, offset: int) -> list[float]:
        series: list[tuple[float | None, ...]] = []
        if "MA" in self._overlays:
            series.extend((indicators.ma[5][offset:], indicators.ma[20][offset:]))
        if "BOLL" in self._overlays:
            series.extend(
                (
                    indicators.boll_upper[offset:],
                    indicators.boll_mid[offset:],
                    indicators.boll_lower[offset:],
                )
            )
        return [value for values in series for value in values if value is not None]

    @staticmethod
    def _grid(painter: QPainter, rect: QRectF, rows: int) -> None:
        painter.setPen(QPen(GRID, 1))
        for row in range(rows + 1):
            y = rect.top() + row / rows * rect.height()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    @staticmethod
    def _axis_labels(painter: QPainter, rect: QRectF, low: float, high: float) -> None:
        painter.setPen(TEXT)
        painter.setFont(QFont("Menlo", 9))
        for row in range(6):
            value = high - row / 5 * (high - low)
            y = rect.top() + row / 5 * rect.height()
            painter.drawText(QRectF(2, y - 8, 50, 16), Qt.AlignmentFlag.AlignRight, f"{value:.2f}")

    @staticmethod
    def _line(
        painter: QPainter,
        rect: QRectF,
        values: tuple[float | None, ...],
        y_for: object,
        color: QColor,
    ) -> None:
        if not values:
            return
        slot = rect.width() / len(values)
        path = QPainterPath()
        active = False
        for index, value in enumerate(values):
            if value is None:
                active = False
                continue
            point = QPointF(rect.left() + (index + 0.5) * slot, y_for(value))  # type: ignore[operator]
            if active:
                path.lineTo(point)
            else:
                path.moveTo(point)
                active = True
        painter.setPen(QPen(color, 1.35))
        painter.drawPath(path)


class IndicatorChartWidget(QWidget):
    MODES = ("MACD", "KDJ", "RSI", "ATR", "WR")

    def __init__(self) -> None:
        super().__init__()
        self._chart_data: ChartData | None = None
        self._mode = "MACD"
        self.setMinimumHeight(118)
        self.setAccessibleName("MACD 两线一柱技术指标副图")
        self.setToolTip("MACD：DIF 为快线，DEA 为慢线；红柱表示柱值为正，绿柱表示柱值为负")

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def chart_data(self) -> ChartData | None:
        return self._chart_data

    def set_result(self, result: ResearchResult) -> None:
        self.set_chart_data(build_chart_data(result, Timeframe.DAILY))

    def set_chart_data(self, data: ChartData) -> None:
        self._chart_data = data
        detail = " 两线一柱" if self._mode == "MACD" else ""
        self.setAccessibleName(f"{data.timeframe.label}{self._mode}{detail} 技术指标副图")
        self.update()

    def clear_data(self) -> None:
        self._chart_data = None
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"未知副图指标：{mode}")
        self._mode = mode
        if self._chart_data is not None:
            detail = " 两线一柱" if self._mode == "MACD" else ""
            self.setAccessibleName(
                f"{self._chart_data.timeframe.label}{self._mode}{detail} 技术指标副图"
            )
        elif mode == "MACD":
            self.setAccessibleName("MACD 两线一柱技术指标副图")
        self.update()

    def _line_series(self) -> list[tuple[str, tuple[float | None, ...], QColor]]:
        assert self._chart_data is not None
        indicators = self._chart_data.indicators
        if self._mode == "RSI":
            return [
                (f"RSI{period}", values, color)
                for (period, values), color in zip(
                    indicators.rsi.items(), (CYAN, MA5, VIOLET), strict=True
                )
            ]
        if self._mode == "KDJ":
            return [
                ("K", indicators.k, CYAN),
                ("D", indicators.d, MA5),
                ("J", indicators.j, VIOLET),
            ]
        if self._mode == "WR":
            return [
                (f"WR{period}", values, color)
                for (period, values), color in zip(indicators.wr.items(), (CYAN, MA5), strict=True)
            ]
        if self._mode == "ATR":
            return [
                (f"ATR{period}", values, color)
                for (period, values), color in zip(indicators.atr.items(), (CYAN, MA5), strict=True)
            ]
        return [
            ("DIF（快线）", indicators.dif, CYAN),
            ("DEA（慢线）", indicators.dea, MA5),
        ]

    def _histogram_series(self) -> tuple[str, tuple[float | None, ...]] | None:
        if self._chart_data is None or self._mode != "MACD":
            return None
        return "MACD柱", self._chart_data.indicators.macd

    def paintEvent(self, _: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._chart_data is None:
            return
        data = self._chart_data
        rect = QRectF(48, 26, max(self.width() - 64, 100), max(self.height() - 40, 64))
        PriceChartWidget._grid(painter, rect, 4)
        display_series = [
            (name, values[-data.visible_bars :], color)
            for name, values, color in self._line_series()
        ]
        histogram = self._histogram_series()
        histogram_values = histogram[1][-data.visible_bars :] if histogram is not None else ()
        available = [
            value for _, values, _ in display_series for value in values if value is not None
        ]
        available.extend(value for value in histogram_values if value is not None)
        if not available:
            return
        if self._mode in {"RSI", "WR"}:
            low, high = 0.0, 100.0
        elif self._mode == "KDJ":
            low, high = _bounds([*available, 0.0, 100.0])
        elif self._mode == "MACD":
            low, high = _bounds([*available, 0.0])
        else:
            low, high = _bounds(available)

        def value_y(value: float) -> float:
            return rect.bottom() - (value - low) / (high - low) * rect.height()

        self._reference_lines(painter, rect, low, high, value_y)
        if histogram is not None:
            self._draw_histogram(painter, rect, histogram_values, value_y)
        for _name, values, color in display_series:
            PriceChartWidget._line(painter, rect, values, value_y, color)
        PriceChartWidget._axis_labels(painter, rect, low, high)
        x = rect.left()
        painter.setFont(QFont("", 9))
        for name, _, color in display_series:
            legend_name = (
                name.split("（", maxsplit=1)[0]
                if self._mode == "MACD" and rect.width() < 500
                else name
            )
            painter.setPen(color)
            painter.drawText(int(x), 17, legend_name)
            x += painter.fontMetrics().horizontalAdvance(legend_name) + 16
        if histogram is not None:
            painter.setPen(TEXT)
            legend = "柱 " if rect.width() < 500 else "MACD柱 "
            painter.drawText(int(x), 17, legend)
            x += painter.fontMetrics().horizontalAdvance(legend)
            painter.setPen(UP)
            painter.drawText(int(x), 17, "+红")
            x += painter.fontMetrics().horizontalAdvance("+红") + 6
            painter.setPen(DOWN)
            painter.drawText(int(x), 17, "−绿")
        painter.setPen(TEXT)
        chart_label = (
            data.timeframe.label if rect.width() < 360 else f"{self._mode} · {data.timeframe.label}"
        )
        painter.drawText(
            QRectF(rect.left(), 3, rect.width(), 18),
            Qt.AlignmentFlag.AlignRight,
            chart_label,
        )

    @staticmethod
    def _draw_histogram(
        painter: QPainter,
        rect: QRectF,
        values: tuple[float | None, ...],
        value_y: object,
    ) -> None:
        if not values:
            return
        slot = rect.width() / len(values)
        bar_width = max(1.0, min(slot * 0.68, 7.0))
        zero_y = value_y(0.0)  # type: ignore[operator]
        for index, value in enumerate(values):
            if value is None:
                continue
            value_position = value_y(value)  # type: ignore[operator]
            top = min(zero_y, value_position)
            height = max(abs(value_position - zero_y), 1.0)
            color = QColor(UP if value >= 0 else DOWN)
            color.setAlpha(190)
            x = rect.left() + (index + 0.5) * slot
            painter.fillRect(QRectF(x - bar_width / 2, top, bar_width, height), color)

    def _reference_lines(
        self,
        painter: QPainter,
        rect: QRectF,
        low: float,
        high: float,
        value_y: object,
    ) -> None:
        references = {
            "MACD": (0.0,),
            "RSI": (30.0, 70.0),
            "KDJ": (20.0, 80.0),
            "WR": (20.0, 85.0, 90.0),
        }.get(self._mode, ())
        pen = QPen(QColor("#3B5362"), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setFont(QFont("Menlo", 8))
        for value in references:
            if not low <= value <= high:
                continue
            y = value_y(value)  # type: ignore[operator]
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(TEXT)
            painter.drawText(
                QRectF(rect.right() - 30, y - 10, 28, 10),
                Qt.AlignmentFlag.AlignRight,
                f"{value:g}",
            )
