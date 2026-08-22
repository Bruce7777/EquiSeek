from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aegisrun.marketdata.timeframes import Timeframe
from aegisrun.research.advice import InvestmentAdvice, build_investment_advice_summary
from aegisrun.research.signals import MultiTimeframeAnalysis


def _label(text: str, object_name: str = "") -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def _table(rows: int, headers: tuple[str, ...]) -> QTableWidget:
    widget = QTableWidget(rows, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    widget.verticalHeader().setVisible(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setAlternatingRowColors(True)
    return widget


class InvestmentDecisionWidget(QScrollArea):
    """Rendered investment conclusion with inspectable MACD/WR gates."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("investmentDecision")
        self.setAccessibleName("MACD/WR 结构化投资结论与方向预测")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._plain_text = "完成一次分析后显示"

        body = QWidget()
        body.setObjectName("decisionBody")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(12, 12, 12, 16)
        self.body_layout.setSpacing(10)

        hero = QFrame()
        hero.setObjectName("decisionHero")
        hero_layout = QGridLayout(hero)
        hero_layout.setContentsMargins(14, 12, 14, 12)
        self.action_badge = _label("等待分析", "decisionAction")
        self.action_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.symbol_label = _label("—", "decisionSymbol")
        self.confidence_label = _label("规则置信度 —", "decisionConfidence")
        self.direction_label = _label("大方向 —", "decisionDirection")
        hero_layout.addWidget(self.action_badge, 0, 0, 2, 1)
        hero_layout.addWidget(self.symbol_label, 0, 1)
        hero_layout.addWidget(self.confidence_label, 0, 2)
        hero_layout.addWidget(self.direction_label, 1, 1, 1, 2)
        hero_layout.setColumnStretch(1, 1)
        self.body_layout.addWidget(hero)

        price = QFrame()
        price.setObjectName("decisionCard")
        price_layout = QGridLayout(price)
        self.price_value = _label("—", "decisionMetricValue")
        self.zone_value = _label("—", "decisionMetricValue")
        self.invalidation_value = _label("—", "decisionBodyText")
        price_layout.addWidget(_label("当前价", "decisionCaption"), 0, 0)
        price_layout.addWidget(_label("动作参考区", "decisionCaption"), 0, 1)
        price_layout.addWidget(self.price_value, 1, 0)
        price_layout.addWidget(self.zone_value, 1, 1)
        price_layout.addWidget(_label("逻辑失效条件", "decisionCaption"), 2, 0, 1, 2)
        price_layout.addWidget(self.invalidation_value, 3, 0, 1, 2)
        self.body_layout.addWidget(price)

        self.body_layout.addWidget(_label("方向情景 · 不是收益承诺", "decisionSectionTitle"))
        self.forecast_table = _table(
            0,
            ("周期", "方向", "依据", "ATR 风险区间"),
        )
        self.forecast_table.setMaximumHeight(180)
        self.body_layout.addWidget(self.forecast_table)

        self.body_layout.addWidget(_label("月线→周线→日线状态矩阵", "decisionSectionTitle"))
        self.timeframe_table = _table(
            0,
            ("周期", "MACD 阶段", "WR10 / 区间", "正式信号截止"),
        )
        self.timeframe_table.setMaximumHeight(180)
        self.body_layout.addWidget(self.timeframe_table)

        self.body_layout.addWidget(_label("五级决策链", "decisionSectionTitle"))
        self.decision_path_table = _table(0, ("决策闸门", "状态", "当前结论"))
        self.decision_path_table.setMaximumHeight(205)
        self.body_layout.addWidget(self.decision_path_table)

        self.body_layout.addWidget(_label("触发证据与风险控制", "decisionSectionTitle"))
        self.evidence_layout = QVBoxLayout()
        self.evidence_layout.setSpacing(5)
        self.body_layout.addLayout(self.evidence_layout)
        self.body_layout.addStretch()
        self.setWidget(body)

    @staticmethod
    def _set_item(table: QTableWidget, row: int, column: int, value: str) -> None:
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        table.setItem(row, column, item)

    def _clear_evidence(self) -> None:
        while self.evidence_layout.count():
            item = self.evidence_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_advice(
        self,
        advice: InvestmentAdvice,
        analysis: MultiTimeframeAnalysis,
    ) -> None:
        self._plain_text = build_investment_advice_summary(advice)
        self.action_badge.setText(advice.action_label)
        self.action_badge.setProperty("action", advice.action.value)
        self.action_badge.style().unpolish(self.action_badge)
        self.action_badge.style().polish(self.action_badge)
        self.symbol_label.setText(f"{advice.symbol} · 截止 {advice.as_of}")
        self.confidence_label.setText(
            f"建议动作 · 规则置信度 {advice.confidence}/100（{advice.confidence_label}）\n"
            f"技术 {advice.technical_confidence} · 大盘/板块 "
            f"{advice.market_confidence_adjustment:+d} · 宏观 "
            f"{advice.macro_confidence_adjustment:+d}"
        )
        self.direction_label.setText(
            f"大方向：{advice.direction_label} · "
            f"{advice.market_context.get('priority_label', '未启用市场共振')}"
        )
        self.price_value.setText(f"{advice.current_price:.4f}")
        zone = "暂无动作区间"
        if advice.action_zone_low is not None and advice.action_zone_high is not None:
            zone = f"{advice.action_zone_low:.4f} – {advice.action_zone_high:.4f}"
        self.zone_value.setText(zone)
        self.invalidation_value.setText(advice.invalidation_condition)

        self.forecast_table.setRowCount(len(advice.forecasts))
        for row, forecast in enumerate(advice.forecasts):
            measure = (
                f"历史命中率 {forecast.probability_pct:.2f}% · 样本 {forecast.sample_count}"
                if forecast.probability_pct is not None
                else f"上涨情景分 {forecast.scenario_score:.2f}/100 · 非概率"
                if forecast.scenario_score is not None
                else "无可用统计"
            )
            expected = (
                "无可靠收益估计"
                if forecast.expected_return_pct is None
                else f"{forecast.expected_return_pct:+.2f}%"
            )
            risk_range = (
                "ATR 数据不足"
                if forecast.price_range_low is None or forecast.price_range_high is None
                else f"{forecast.price_range_low:.4f} – {forecast.price_range_high:.4f}"
            )
            for column, value in enumerate(
                (
                    f"{forecast.trading_days} 日",
                    forecast.direction_label,
                    f"{measure}\n{expected}",
                    risk_range,
                )
            ):
                self._set_item(self.forecast_table, row, column, value)
        self.forecast_table.resizeRowsToContents()

        order = (Timeframe.MONTHLY, Timeframe.WEEKLY, Timeframe.DAILY)
        self.timeframe_table.setRowCount(len(order))
        for row, timeframe in enumerate(order):
            macd = analysis.macd.get(timeframe.value)
            wr = analysis.wr.get(timeframe.value)
            if macd is None or wr is None:
                continue
            value = "数据不足" if wr.value is None else f"{wr.value:.2f}"
            deadline = macd.as_of
            if macd.provisional_excluded:
                deadline += f"（形成中 {macd.latest_available_as_of} 已排除）"
            for column, cell in enumerate(
                (
                    timeframe.label,
                    f"{macd.phase_label}\nDIF/DEA {macd.dif:.3f}/{macd.dea:.3f}",
                    f"{value} / {wr.zone_label}",
                    deadline,
                )
            ):
                self._set_item(self.timeframe_table, row, column, cell)
        self.timeframe_table.resizeRowsToContents()

        self.decision_path_table.setRowCount(len(analysis.decision_path))
        for row, step in enumerate(analysis.decision_path):
            self._set_item(self.decision_path_table, row, 0, step.title)
            status = QTableWidgetItem(step.status_label)
            status.setData(Qt.ItemDataRole.UserRole, step.status)
            status.setForeground(
                QColor(
                    "#35D0A0"
                    if step.status == "satisfied"
                    else "#F06B71"
                    if step.status == "block"
                    else "#EABF5A"
                )
            )
            self.decision_path_table.setItem(row, 1, status)
            self._set_item(self.decision_path_table, row, 2, step.summary)
        self.decision_path_table.resizeRowsToContents()

        self._clear_evidence()
        evidence = tuple(dict.fromkeys((*advice.evidence, *advice.risk_controls)))
        for item in evidence:
            evidence_frame = QFrame()
            evidence_frame.setObjectName("evidenceRow")
            row_layout = QHBoxLayout(evidence_frame)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.addWidget(_label("•", "evidenceBullet"))
            row_layout.addWidget(_label(item, "decisionBodyText"), 1)
            self.evidence_layout.addWidget(evidence_frame)

    def toPlainText(self) -> str:  # noqa: N802 - Qt compatibility for smoke tests
        return self._plain_text
