from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aegisrun.portfolio.analysis import CandidateResult, HoldingAssessment
from aegisrun.portfolio.models import Position, WatchItem
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.research.backtest import (
    BacktestOptions,
    BacktestReport,
    build_backtest_summary,
    export_backtest_json,
    export_signal_csv,
    parse_horizons,
)


class PositionEditor(QDialog):
    def __init__(self, position: Position | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑持仓" if position else "新增持仓")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.symbol = QLineEdit(position.symbol if position else "")
        self.symbol.setPlaceholderText("例如 600519.SH")
        self.symbol.setAccessibleName("持仓股票代码")
        self.name = QLineEdit(position.name if position else "")
        self.industry = QLineEdit(position.industry if position else "")
        self.industry.setPlaceholderText("例如 工业自动化、房地产开发、白酒消费")
        self.industry.setAccessibleName("持仓行业标签")
        self.quantity = QDoubleSpinBox()
        self.quantity.setRange(0.01, 1_000_000_000)
        self.quantity.setDecimals(2)
        self.quantity.setValue(position.quantity if position else 100)
        self.cost_price = QDoubleSpinBox()
        self.cost_price.setRange(0.001, 1_000_000)
        self.cost_price.setDecimals(4)
        self.cost_price.setValue(position.cost_price if position else 1)
        self.has_opened_on = QCheckBox("记录建仓日期")
        self.has_opened_on.setChecked(bool(position and position.opened_on))
        self.opened_on = QDateEdit()
        self.opened_on.setCalendarPopup(True)
        self.opened_on.setDisplayFormat("yyyy-MM-dd")
        opened = position.opened_on if position and position.opened_on else date.today()
        self.opened_on.setDate(QDate(opened.year, opened.month, opened.day))
        self.opened_on.setEnabled(self.has_opened_on.isChecked())
        self.has_opened_on.toggled.connect(self.opened_on.setEnabled)
        opened_row = QHBoxLayout()
        opened_row.addWidget(self.has_opened_on)
        opened_row.addWidget(self.opened_on)
        self.notes = QLineEdit(position.notes if position else "")
        form.addRow("股票代码", self.symbol)
        form.addRow("名称", self.name)
        form.addRow("行业标签", self.industry)
        form.addRow("持仓数量", self.quantity)
        form.addRow("单位成本", self.cost_price)
        form.addRow("建仓日期", opened_row)
        form.addRow("备注", self.notes)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> Position:
        opened_on = None
        if self.has_opened_on.isChecked():
            selected = self.opened_on.date()
            opened_on = date(selected.year(), selected.month(), selected.day())
        return Position(
            symbol=self.symbol.text(),
            name=self.name.text(),
            quantity=self.quantity.value(),
            cost_price=self.cost_price.value(),
            opened_on=opened_on,
            notes=self.notes.text(),
            industry=self.industry.text(),
        )


class WatchEditor(QDialog):
    def __init__(self, item: WatchItem | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑自选" if item else "新增自选")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.symbol = QLineEdit(item.symbol if item else "")
        self.symbol.setPlaceholderText("例如 000001.SZ")
        self.symbol.setAccessibleName("自选股票代码")
        self.name = QLineEdit(item.name if item else "")
        self.industry = QLineEdit(item.industry if item else "")
        self.industry.setPlaceholderText("用于资本三流/成本转嫁行业映射")
        self.industry.setAccessibleName("自选行业标签")
        self.notes = QLineEdit(item.notes if item else "")
        form.addRow("股票代码", self.symbol)
        form.addRow("名称", self.name)
        form.addRow("行业标签", self.industry)
        form.addRow("关注理由", self.notes)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> WatchItem:
        return WatchItem(
            self.symbol.text(),
            self.name.text(),
            self.notes.text(),
            self.industry.text(),
        )


class PortfolioDialog(QDialog):
    def __init__(self, repository: PortfolioRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("本地持仓与自选")
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "持仓数量、成本、日期和备注只保存在本机，不发送给 DeepSeek；"
            "行业标签会进入去持仓化的宏观行业结论。"
            "删除记录不会影响已生成的研究工作区。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        tabs = QTabWidget()
        self.positions_table = self._table(
            ("代码", "名称", "行业", "数量", "成本", "建仓日", "备注")
        )
        self.watch_table = self._table(("代码", "名称", "行业", "关注理由"))
        tabs.addTab(
            self._tab(
                self.positions_table,
                self._add_position,
                self._edit_position,
                self._remove_position,
            ),
            "持仓",
        )
        tabs.addTab(
            self._tab(self.watch_table, self._add_watch, self._edit_watch, self._remove_watch),
            "自选股",
        )
        layout.addWidget(tabs)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(self.reject)
        layout.addWidget(close_button)
        self.refresh()

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _tab(
        table: QTableWidget,
        add_callback: Callable[[], None],
        edit_callback: Callable[[], None],
        remove_callback: Callable[[], None],
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(table)
        buttons = QHBoxLayout()
        for label, callback in (
            ("新增", add_callback),
            ("编辑", edit_callback),
            ("删除", remove_callback),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        return page

    def refresh(self) -> None:
        book = self.repository.load()
        self.positions_table.setRowCount(len(book.positions))
        for row, position_item in enumerate(book.positions):
            values = (
                position_item.symbol,
                position_item.name,
                position_item.industry,
                f"{position_item.quantity:.2f}",
                f"{position_item.cost_price:.4f}",
                position_item.opened_on.isoformat() if position_item.opened_on else "",
                position_item.notes,
            )
            for column, value in enumerate(values):
                self.positions_table.setItem(row, column, QTableWidgetItem(value))
        self.watch_table.setRowCount(len(book.watchlist))
        for row, watch_item in enumerate(book.watchlist):
            for column, value in enumerate(
                (watch_item.symbol, watch_item.name, watch_item.industry, watch_item.notes)
            ):
                self.watch_table.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def _selected_symbol(table: QTableWidget) -> str | None:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else None

    def _save_position(self, editor: PositionEditor) -> None:
        try:
            self.repository.upsert_position(editor.value())
        except ValueError as error:
            QMessageBox.warning(self, "持仓数据无效", str(error))
            return
        self.refresh()

    def _add_position(self) -> None:
        editor = PositionEditor(parent=self)
        if editor.exec():
            self._save_position(editor)

    def _edit_position(self) -> None:
        symbol = self._selected_symbol(self.positions_table)
        if symbol is None:
            return
        position = self.repository.load().position(symbol)
        if position is None:
            return
        editor = PositionEditor(position, self)
        if editor.exec():
            self._save_position(editor)

    def _remove_position(self) -> None:
        symbol = self._selected_symbol(self.positions_table)
        if symbol is not None:
            self.repository.remove_position(symbol)
            self.refresh()

    def _save_watch(self, editor: WatchEditor) -> None:
        try:
            self.repository.upsert_watch(editor.value())
        except ValueError as error:
            QMessageBox.warning(self, "自选数据无效", str(error))
            return
        self.refresh()

    def _add_watch(self) -> None:
        editor = WatchEditor(parent=self)
        if editor.exec():
            self._save_watch(editor)

    def _edit_watch(self) -> None:
        symbol = self._selected_symbol(self.watch_table)
        if symbol is None:
            return
        item = next(
            (value for value in self.repository.load().watchlist if value.symbol == symbol),
            None,
        )
        if item is None:
            return
        editor = WatchEditor(item, self)
        if editor.exec():
            self._save_watch(editor)

    def _remove_watch(self) -> None:
        symbol = self._selected_symbol(self.watch_table)
        if symbol is not None:
            self.repository.remove_watch(symbol)
            self.refresh()


class CandidateResultsDialog(QDialog):
    def __init__(
        self,
        candidates: tuple[CandidateResult, ...],
        holdings: tuple[HoldingAssessment, ...],
        parent: QWidget | None = None,
        *,
        failures: dict[str, str] | None = None,
        strategy_label: str = "平台基础排序",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("持仓风险与策略候选池")
        self.resize(1180, 640)
        layout = QVBoxLayout(self)
        notice = QLabel(
            f"当前筛选策略：{strategy_label}。候选建议来自确定性研究结果，"
            "仅扫描本地持仓与自选池；"
            "默认结合上市市场基准排序，板块需进入单股页按需确认；"
            "历史命中率与规则情景分会分开标注；"
            "宏观行业映射只调置信度、不覆盖技术动作。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        tabs = QTabWidget()
        holding_table = PortfolioDialog._table(
            (
                "代码",
                "行业",
                "最新价",
                "浮动盈亏",
                "峰值回撤",
                "退出优先级",
                "建议动作",
                "置信度",
                "宏观",
                "下一触发",
            )
        )
        holding_table.setAccessibleName("候选扫描持仓风险")
        holding_table.setAlternatingRowColors(True)
        holding_table.setRowCount(len(holdings))
        for row, holding in enumerate(holdings):
            holding_values = (
                holding.symbol,
                holding.industry or "—",
                f"{holding.latest_close:.2f}",
                f"{holding.unrealized_pnl:.2f}",
                (
                    f"{holding.drawdown_from_peak_pct:.2f}%"
                    if holding.drawdown_from_peak_pct is not None
                    else "—"
                ),
                holding.exit_priority_label,
                holding.recommended_action_label or "—",
                f"{holding.confidence}/100" if holding.confidence else "—",
                holding.macro_stance_label or "未映射",
                holding.next_trigger,
            )
            for column, value in enumerate(holding_values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                holding_table.setItem(row, column, item)
        candidate_table = PortfolioDialog._table(
            (
                "排名",
                "代码",
                "名称",
                "行业",
                "动作",
                "技术/综合置信度",
                "大盘共振",
                "宏观",
                "20日方向",
                "20日度量",
                "候选分",
                "策略分",
                "大方向",
                "主要依据",
            )
        )
        candidate_table.setAccessibleName("策略候选结果")
        candidate_table.setAlternatingRowColors(True)
        candidate_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            candidate_values = (
                str(candidate.rank),
                candidate.symbol,
                candidate.name,
                candidate.industry or "—",
                candidate.action_label or "等待",
                (
                    f"{candidate.technical_confidence}/{candidate.confidence}"
                    if candidate.confidence
                    else "—"
                ),
                (
                    f"{candidate.benchmark_direction_label or '不可用'} · "
                    f"{candidate.market_priority_label or '未启用'} "
                    f"({candidate.market_confidence_adjustment:+d})"
                ),
                (
                    f"{candidate.macro_stance_label or '未映射'} "
                    f"({candidate.macro_confidence_adjustment:+d})"
                ),
                candidate.forecast_20d_direction or "—",
                (
                    f"{candidate.forecast_20d_measure_label} "
                    f"{candidate.forecast_20d_probability_pct:.2f}%"
                    if candidate.forecast_20d_probability_pct is not None
                    else (
                        f"{candidate.forecast_20d_measure_label} "
                        f"{candidate.forecast_20d_scenario_score:.2f}/100"
                        if candidate.forecast_20d_scenario_score is not None
                        else "—"
                    )
                ),
                str(candidate.score),
                (
                    f"{candidate.strategy_score:.2f}"
                    if candidate.strategy_score is not None
                    else "—"
                ),
                candidate.direction_label,
                "；".join(candidate.reasons[:2]),
            )
            for column, value in enumerate(candidate_values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                candidate_table.setItem(row, column, item)
        holding_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        holding_table.horizontalHeader().setSectionResizeMode(
            holding_table.columnCount() - 1, QHeaderView.ResizeMode.Stretch
        )
        candidate_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        candidate_table.horizontalHeader().setSectionResizeMode(
            candidate_table.columnCount() - 1, QHeaderView.ResizeMode.Stretch
        )
        tabs.addTab(
            self._result_page(
                holding_table,
                "当前候选池中没有登记持仓；可在“持仓与自选”中补充后重新扫描。",
            ),
            f"持仓风险（{len(holdings)}）",
        )
        tabs.addTab(
            self._result_page(
                candidate_table,
                "当前策略没有筛出候选。可检查策略阈值、行业排除项和市场买入闸门。",
            ),
            f"策略候选（{len(candidates)}）",
        )
        if failures:
            failure_table = PortfolioDialog._table(("代码", "失败原因"))
            failure_table.setAccessibleName("候选池失败明细")
            failure_table.setRowCount(len(failures))
            for row, (symbol, message) in enumerate(sorted(failures.items())):
                failure_table.setItem(row, 0, QTableWidgetItem(symbol))
                message_item = QTableWidgetItem(message[:500])
                message_item.setToolTip(message[:2_000])
                failure_table.setItem(row, 1, message_item)
            tabs.addTab(failure_table, f"失败明细（{len(failures)}）")
        self.tabs = tabs
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _result_page(table: QTableWidget, empty_text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        if table.rowCount() == 0:
            empty = QLabel(empty_text)
            empty.setObjectName("sourceNotice")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        layout.addWidget(table)
        return page


class BacktestConfigDialog(QDialog):
    def __init__(
        self,
        available_start: date,
        available_end: date,
        default_start: date,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if available_start >= available_end:
            raise ValueError("可用于回测的交易日不足")
        self.setWindowTitle("策略验证参数")
        self.setMinimumWidth(500)
        self._available_start = available_start
        self._available_end = available_end
        self._options: BacktestOptions | None = None
        layout = QVBoxLayout(self)
        notice = QLabel(
            f"可用评估区间：{available_start.isoformat()} 至 {available_end.isoformat()}。"
            "信号在当日收盘后生成，最早按下一交易日开盘执行。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.start_date = self._date_edit(max(available_start, min(default_start, available_end)))
        self.end_date = self._date_edit(available_end)
        self.cost_bps = QDoubleSpinBox()
        self.cost_bps.setRange(0, 999.9)
        self.cost_bps.setDecimals(1)
        self.cost_bps.setSingleStep(1)
        self.cost_bps.setValue(10)
        self.cost_bps.setSuffix(" bps")
        self.cost_bps.setAccessibleName("单边交易成本")
        self.horizons = QLineEdit("5, 10, 20")
        self.horizons.setPlaceholderText("例如 5, 10, 20, 60")
        self.horizons.setAccessibleName("信号后验观察周期")
        form.addRow("评估开始日期", self.start_date)
        form.addRow("评估结束日期", self.end_date)
        form.addRow("单边交易成本", self.cost_bps)
        form.addRow("观察周期（交易日）", self.horizons)
        layout.addLayout(form)
        boundary = QLabel(
            "默认保持 MACD/WR 规则不变。本窗口只配置验证条件，避免把回测变成自动调参。"
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("执行回测")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _date_edit(self, value: date) -> QDateEdit:
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("yyyy-MM-dd")
        widget.setDateRange(
            QDate(
                self._available_start.year,
                self._available_start.month,
                self._available_start.day,
            ),
            QDate(self._available_end.year, self._available_end.month, self._available_end.day),
        )
        widget.setDate(QDate(value.year, value.month, value.day))
        return widget

    @staticmethod
    def _selected_date(widget: QDateEdit) -> date:
        value = widget.date()
        return date(value.year(), value.month(), value.day())

    def value(self) -> BacktestOptions:
        return BacktestOptions(
            self._selected_date(self.start_date),
            self._selected_date(self.end_date),
            transaction_cost_bps=self.cost_bps.value(),
            horizons=parse_horizons(self.horizons.text()),
        )

    def accept(self) -> None:
        try:
            self._options = self.value()
        except ValueError as error:
            QMessageBox.warning(self, "回测参数无效", str(error))
            return
        super().accept()

    @property
    def options(self) -> BacktestOptions:
        return self._options or self.value()


class BacktestMetricCard(QFrame):
    def __init__(self, title: str, value: str, tone: str = "neutral") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        caption = QLabel(title.upper())
        caption.setObjectName("metricLabel")
        number = QLabel(value)
        number.setObjectName("metricValue")
        number.setProperty("tone", tone)
        number.setAccessibleName(title)
        layout.addWidget(caption)
        layout.addWidget(number)


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def _direction_label(value: str) -> str:
    return {
        "bullish": "上涨",
        "bearish": "下跌",
        "sideways": "震荡",
    }.get(value, value)


class BacktestDialog(QDialog):
    def __init__(self, report: BacktestReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("MACD/WR 策略验证工作台")
        self.resize(1040, 700)
        self.setMinimumSize(900, 620)
        layout = QVBoxLayout(self)
        title = QLabel(
            f"{report.symbol or '未指定证券'} · {report.evaluation_start.isoformat()} 至 "
            f"{report.evaluation_end.isoformat()} · 单边 {report.transaction_cost_bps:.1f} bps"
        )
        title.setObjectName("pageSubtitle")
        layout.addWidget(title)
        metrics = QGridLayout()
        values = (
            ("策略收益", _pct(report.total_return_pct), self._tone(report.total_return_pct)),
            (
                "买入持有",
                _pct(report.benchmark_return_pct),
                self._tone(report.benchmark_return_pct),
            ),
            ("超额收益", _pct(report.excess_return_pct), self._tone(report.excess_return_pct)),
            ("最大回撤", _pct(report.max_drawdown_pct), "danger"),
            ("交易胜率", _pct(report.win_rate_pct, signed=False), "neutral"),
            ("持仓暴露", _pct(report.exposure_pct, signed=False), "neutral"),
        )
        for column, (caption, value, tone) in enumerate(values):
            metrics.addWidget(BacktestMetricCard(caption, value, tone), 0, column)
        layout.addLayout(metrics)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("策略验证结果")
        summary = QPlainTextEdit(build_backtest_summary(report))
        summary.setReadOnly(True)
        summary.setObjectName("summaryView")
        summary.setAccessibleName("策略回测报告")
        self.tabs.addTab(summary, "摘要")
        self.statistics_table = self._statistics_table(report)
        self.trade_table = self._trade_table(report)
        self.signal_table = self._signal_table(report)
        self.tabs.addTab(
            self._table_page(self.statistics_table, "没有可完成的信号后验样本。"), "信号统计"
        )
        self.tabs.addTab(self._table_page(self.trade_table, "评估区间内没有完整交易。"), "交易明细")
        self.tabs.addTab(
            self._table_page(self.signal_table, "评估区间内没有触发观察窗口。"), "信号明细"
        )
        layout.addWidget(self.tabs)

        actions = QHBoxLayout()
        export_json_button = QPushButton("导出 JSON")
        export_json_button.setObjectName("secondaryButton")
        export_json_button.setAccessibleName("导出完整回测 JSON")
        export_json_button.clicked.connect(self._choose_json_path)
        export_csv_button = QPushButton("导出信号 CSV")
        export_csv_button.setObjectName("secondaryButton")
        export_csv_button.setAccessibleName("导出回测信号 CSV")
        export_csv_button.clicked.connect(self._choose_csv_path)
        actions.addWidget(export_json_button)
        actions.addWidget(export_csv_button)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    @staticmethod
    def _tone(value: float) -> str:
        return "positive" if value > 0 else "negative" if value < 0 else "neutral"

    @staticmethod
    def _table(headers: tuple[str, ...], accessible_name: str) -> QTableWidget:
        table = PortfolioDialog._table(headers)
        table.setAccessibleName(accessible_name)
        table.setAlternatingRowColors(True)
        return table

    @staticmethod
    def _set_row(table: QTableWidget, row: int, values: tuple[str, ...]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if column > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            table.setItem(row, column, item)

    def _statistics_table(self, report: BacktestReport) -> QTableWidget:
        table = self._table(
            ("观察窗口", "周期", "样本", "有利数", "有利率", "平均", "中位", "最佳", "最差"),
            "回测信号统计",
        )
        table.setRowCount(len(report.signal_statistics))
        for row, item in enumerate(report.signal_statistics):
            self._set_row(
                table,
                row,
                (
                    item.action_label,
                    f"{item.trading_days} 日",
                    str(item.sample_count),
                    str(item.favorable_count),
                    _pct(item.favorable_rate_pct, signed=False),
                    _pct(item.average_return_pct),
                    _pct(item.median_return_pct),
                    _pct(item.best_return_pct),
                    _pct(item.worst_return_pct),
                ),
            )
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _trade_table(self, report: BacktestReport) -> QTableWidget:
        table = self._table(
            ("入场信号", "入场日", "入场价", "退出信号", "退出日", "退出价", "持有", "收益"),
            "回测交易明细",
        )
        table.setRowCount(len(report.trades))
        for row, item in enumerate(report.trades):
            self._set_row(
                table,
                row,
                (
                    item.entry_signal_date.isoformat(),
                    item.entry_date.isoformat(),
                    f"{item.entry_price:.4f}",
                    item.exit_signal_date.isoformat() if item.exit_signal_date else "区间末平仓",
                    item.exit_date.isoformat(),
                    f"{item.exit_price:.4f}",
                    f"{item.bars_held} 日",
                    _pct(item.return_pct),
                ),
            )
        for column in range(6):
            table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        return table

    def _signal_table(self, report: BacktestReport) -> QTableWidget:
        table = self._table(
            ("信号日", "执行日", "观察窗口", "方向", "执行价", "候选分", "周期后验", "依据"),
            "回测信号明细",
        )
        table.setRowCount(len(report.signals))
        for row, item in enumerate(report.signals):
            outcomes = "；".join(
                f"{outcome.trading_days}日 "
                f"{'—' if outcome.return_pct is None else f'{outcome.return_pct:+.2f}%'}"
                for outcome in item.horizons
            )
            self._set_row(
                table,
                row,
                (
                    item.signal_date.isoformat(),
                    item.execution_date.isoformat(),
                    item.action_label,
                    _direction_label(item.direction),
                    f"{item.execution_price:.4f}",
                    str(item.candidate_score),
                    outcomes,
                    "；".join(item.reasons),
                ),
            )
        for column in range(6):
            table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        return table

    @staticmethod
    def _table_page(table: QTableWidget, empty_text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        if table.rowCount() == 0:
            empty = QLabel(empty_text)
            empty.setObjectName("sourceNotice")
            layout.addWidget(empty)
        layout.addWidget(table)
        return page

    def export_json_to(self, path: Path) -> None:
        export_backtest_json(self.report, path)

    def export_csv_to(self, path: Path) -> None:
        export_signal_csv(self.report, path)

    def _suggested_name(self, suffix: str) -> str:
        symbol = (self.report.symbol or "unknown").replace(".", "-")
        return f"{symbol}_{self.report.evaluation_end.isoformat()}_backtest.{suffix}"

    def _choose_json_path(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出完整回测 JSON",
            self._suggested_name("json"),
            "JSON 文件 (*.json)",
        )
        if selected:
            self._export(Path(selected), self.export_json_to)

    def _choose_csv_path(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出信号 CSV",
            self._suggested_name("csv"),
            "CSV 文件 (*.csv)",
        )
        if selected:
            self._export(Path(selected), self.export_csv_to)

    def _export(self, path: Path, writer: Callable[[Path], None]) -> None:
        try:
            writer(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "导出失败", f"无法写入所选路径：{error}")
            return
        QMessageBox.information(self, "导出完成", f"已保存到：\n{path}")
