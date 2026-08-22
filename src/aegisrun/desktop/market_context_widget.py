from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aegisrun.desktop.charts import (
    IndicatorChartWidget,
    PriceChartWidget,
    build_dataset_chart_data,
)
from aegisrun.marketdata.timeframes import Timeframe
from aegisrun.research.market_context import (
    SECTOR_PROXIES,
    ContextInstrument,
    MarketConfluence,
    MarketTrendContext,
    sector_proxy_for,
)


class ContextTrendPane(QWidget):
    def __init__(self, empty_text: str) -> None:
        super().__init__()
        self.context: MarketTrendContext | None = None
        self.timeframe = Timeframe.DAILY
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.price_chart = PriceChartWidget()
        self.price_chart.setMinimumHeight(205)
        self.price_chart.set_overlays(("MA",))
        layout.addWidget(self.price_chart, 5)

        indicator_column = QVBoxLayout()
        indicator_column.setSpacing(6)
        self.macd_chart = IndicatorChartWidget()
        self.macd_chart.setMinimumHeight(98)
        self.macd_chart.set_mode("MACD")
        self.wr_chart = IndicatorChartWidget()
        self.wr_chart.setMinimumHeight(98)
        self.wr_chart.set_mode("WR")
        indicator_column.addWidget(self.macd_chart)
        indicator_column.addWidget(self.wr_chart)
        layout.addLayout(indicator_column, 3)

        summary = QFrame()
        summary.setObjectName("marketContextSummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 4, 10, 4)
        summary_layout.setSpacing(2)
        summary_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title = QLabel(empty_text)
        self.title.setObjectName("marketContextTitle")
        self.title.setFixedHeight(22)
        self.status = QLabel("等待数据")
        self.status.setObjectName("marketContextStatus")
        self.status.setFixedHeight(22)
        self.timeframe_table = QTableWidget(2, 3)
        self.timeframe_table.setObjectName("marketTimeframeTable")
        frames = (Timeframe.MONTHLY, Timeframe.WEEKLY, Timeframe.DAILY)
        self.timeframe_table.setHorizontalHeaderLabels(tuple(frame.label for frame in frames))
        self.timeframe_table.setVerticalHeaderLabels(("MACD", "WR"))
        self.timeframe_table.verticalHeader().setFixedWidth(48)
        self.timeframe_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.timeframe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.timeframe_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.timeframe_table.setAlternatingRowColors(True)
        self.timeframe_table.horizontalHeader().setFixedHeight(17)
        self.timeframe_table.verticalHeader().setMinimumSectionSize(15)
        for row in range(2):
            self.timeframe_table.setRowHeight(row, 16)
        self.timeframe_table.setFixedHeight(51)
        summary_layout.addWidget(self.title)
        summary_layout.addWidget(self.status)
        summary_layout.addWidget(self.timeframe_table)
        layout.addWidget(summary, 3)

    @staticmethod
    def _item(value: str, tone: str = "neutral") -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        if tone == "positive":
            item.setForeground(QColor("#F06B71"))
        elif tone == "negative":
            item.setForeground(QColor("#43C590"))
        return item

    def set_context(self, context: MarketTrendContext, timeframe: Timeframe) -> None:
        self.context = context
        self.timeframe = timeframe
        self.title.setText(
            f"{context.instrument.name} · {context.instrument.symbol}"
            + (f" · {context.instrument.proxy_for}代理" if context.instrument.proxy_for else "")
        )
        if not context.available or context.data is None or context.strategy is None:
            self.timeframe_table.clearContents()
            self.price_chart.clear_data()
            self.macd_chart.clear_data()
            self.wr_chart.clear_data()
            message = context.error or "数据不足"
            self.status.setText("加载失败")
            self.status.setToolTip(message)
            return
        self.status.setText(
            f"{context.strategy.direction_label} {context.strategy.direction_score:+d} · "
            f"{context.data.as_of.isoformat()} · {context.data.cache_status}"
        )
        self.status.setToolTip(
            f"数据源 {context.data.source} · 截止 {context.data.as_of.isoformat()} · "
            f"缓存 {context.data.cache_status}"
        )
        chart_data = build_dataset_chart_data(context.data, timeframe)
        self.price_chart.set_chart_data(chart_data)
        self.macd_chart.set_chart_data(chart_data)
        self.wr_chart.set_chart_data(chart_data)
        for column, frame in enumerate((Timeframe.MONTHLY, Timeframe.WEEKLY, Timeframe.DAILY)):
            macd = context.strategy.macd.get(frame.value)
            wr = context.strategy.wr.get(frame.value)
            phase = macd.phase_label if macd is not None else "数据不足"
            wr_value = (
                "数据不足" if wr is None or wr.value is None else f"{wr.value:.1f} {wr.zone_label}"
            )
            tone = (
                "positive"
                if macd is not None and macd.score > 0
                else "negative"
                if macd is not None and macd.score < 0
                else "neutral"
            )
            self.timeframe_table.setItem(0, column, self._item(phase, tone))
            self.timeframe_table.setItem(1, column, self._item(wr_value))

    def set_timeframe(self, timeframe: Timeframe) -> None:
        if self.context is not None:
            self.set_context(self.context, timeframe)

    def clear(self, title: str) -> None:
        self.context = None
        self.title.setText(title)
        self.status.setText("等待数据")
        self.timeframe_table.clearContents()
        self.price_chart.clear_data()
        self.macd_chart.clear_data()
        self.wr_chart.clear_data()


class MarketContextWidget(QFrame):
    sector_load_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("marketContextPanel")
        self.setMinimumHeight(300)
        self.setMaximumHeight(350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("市场共振过滤器")
        title.setObjectName("marketContextHeading")
        subtitle = QLabel("大盘默认加载 · 中证行业指数代理按需加载 · 同向优先")
        subtitle.setObjectName("mutedText")
        self.confluence_label = QLabel("等待个股分析")
        self.confluence_label.setObjectName("confluenceBadge")
        self.confluence_label.setProperty("status", "pending")
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self.confluence_label)

        self.sector_combo = QComboBox()
        self.sector_combo.setAccessibleName("板块趋势代理")
        self.sector_combo.addItem("选择板块代理", None)
        for proxy in SECTOR_PROXIES:
            instrument = proxy.instrument
            self.sector_combo.addItem(
                f"{instrument.proxy_for} · {instrument.name} ({instrument.symbol})",
                instrument,
            )
        header.addWidget(self.sector_combo)
        self.load_sector_button = QPushButton("加载板块指标")
        self.load_sector_button.setObjectName("secondaryButton")
        self.load_sector_button.clicked.connect(self._request_sector)
        header.addWidget(self.load_sector_button)
        layout.addLayout(header)

        self.error_label = QLabel("")
        self.error_label.setObjectName("contextError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.tabs = QTabWidget()
        self.benchmark_pane = ContextTrendPane("大盘日 K 与指标默认展示")
        self.sector_pane = ContextTrendPane("选择板块后按需加载 K 线与指标")
        self.tabs.addTab(self.benchmark_pane, "大盘 · 默认")
        self.tabs.addTab(self.sector_pane, "板块 · 按需")
        layout.addWidget(self.tabs, 1)

    def _request_sector(self) -> None:
        value = self.sector_combo.currentData()
        if not isinstance(value, ContextInstrument):
            self.show_error("请先选择一个板块趋势代理。")
            return
        self.set_sector_loading(True)
        self.sector_load_requested.emit(value)

    def suggest_industry(self, industry: str) -> None:
        suggested = sector_proxy_for(industry)
        if suggested is None:
            return
        for index in range(self.sector_combo.count()):
            value = self.sector_combo.itemData(index)
            if isinstance(value, ContextInstrument) and value.symbol == suggested.symbol:
                self.sector_combo.setCurrentIndex(index)
                return

    def set_context(self, context: MarketConfluence, timeframe: Timeframe) -> None:
        self.benchmark_pane.set_context(context.benchmark, timeframe)
        if context.sector is not None:
            self.sector_pane.set_context(context.sector, timeframe)
            self.tabs.setTabText(1, f"板块 · {context.sector.instrument.proxy_for}")
        self.confluence_label.setText(context.priority_label)
        self.confluence_label.setProperty("status", context.status)
        self.confluence_label.style().unpolish(self.confluence_label)
        self.confluence_label.style().polish(self.confluence_label)
        self.tabs.setCurrentIndex(0)
        self.error_label.hide()

    def set_timeframe(self, timeframe: Timeframe) -> None:
        self.benchmark_pane.set_timeframe(timeframe)
        self.sector_pane.set_timeframe(timeframe)

    def reset_sector(self) -> None:
        self.sector_pane.clear("选择板块后按需加载 K 线与指标")
        self.tabs.setTabText(1, "板块 · 按需")
        self.set_sector_loading(False)
        self.error_label.hide()

    def set_sector_loading(self, loading: bool) -> None:
        self.load_sector_button.setEnabled(not loading)
        self.sector_combo.setEnabled(not loading)
        self.load_sector_button.setText("板块加载中…" if loading else "加载板块指标")

    def show_error(self, message: str) -> None:
        self.set_sector_loading(False)
        self.error_label.setText(message)
        self.error_label.show()
        self.tabs.setCurrentIndex(1)
