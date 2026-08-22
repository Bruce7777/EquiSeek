from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aegisrun.macro.analysis import LongTermAllocationPlan, MacroAnalysis
from aegisrun.macro.pipeline import MacroResearchResult


def _text(text: str, object_name: str = "") -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _set_row(table: QTableWidget, row: int, values: tuple[str, ...]) -> None:
    for column, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        table.setItem(row, column, item)


class FlowMapWidget(QWidget):
    """Compact source → channel → destination map for capital flow paths."""

    def __init__(self, analysis: MacroAnalysis) -> None:
        super().__init__()
        self.analysis = analysis
        self.setMinimumHeight(280)
        self.setAccessibleName("资本三流与实体传导路径图")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#08141D"))
        width = float(self.width())
        margin = 20.0
        gap = 18.0
        column_width = max(160.0, (width - margin * 2 - gap * 2) / 3)
        header_y = 18.0
        painter.setPen(QColor("#8FA8B7"))
        painter.drawText(QRectF(margin, header_y, column_width, 24), "资金来源")
        painter.drawText(
            QRectF(margin + column_width + gap, header_y, column_width, 24), "传导通道"
        )
        painter.drawText(
            QRectF(margin + (column_width + gap) * 2, header_y, column_width, 24),
            "承接部门 / 投资含义",
        )
        paths = self.analysis.capital_flow.paths
        available = max(1.0, self.height() - 58.0)
        row_height = max(30.0, min(54.0, available / max(1, len(paths))))
        painter.setFont(self.font())
        for index, path in enumerate(paths):
            y = 48.0 + index * row_height
            tone = (
                QColor("#35D0A0")
                if path.score >= 65
                else QColor("#EABF5A")
                if path.score >= 45
                else QColor("#F06B71")
            )
            boxes = (
                (path.source, margin),
                (path.channel, margin + column_width + gap),
                (path.destination, margin + (column_width + gap) * 2),
            )
            for value, x in boxes:
                rect = QRectF(x, y, column_width, row_height - 8)
                painter.setBrush(QColor("#0D202B"))
                painter.setPen(QPen(QColor("#294552"), 1))
                painter.drawRoundedRect(rect, 6, 6)
                painter.setPen(QColor("#D7E4EA"))
                painter.drawText(
                    rect.adjusted(8, 4, -8, -4),
                    Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                    value,
                )
            painter.setPen(QPen(tone, 2))
            left_end = margin + column_width
            middle_start = margin + column_width + gap
            middle_end = middle_start + column_width
            right_start = margin + (column_width + gap) * 2
            mid_y = y + (row_height - 8) / 2
            painter.drawLine(int(left_end), int(mid_y), int(middle_start), int(mid_y))
            painter.drawLine(int(middle_end), int(mid_y), int(right_start), int(mid_y))
            painter.setPen(tone)
            painter.drawText(
                QRectF(left_end - 34, y - 2, gap + 68, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"{path.dimension} {path.score}",
            )
        painter.end()


class CostTransferMapWidget(QWidget):
    """Visualize who originates, transmits, bears and benefits from each cost."""

    def __init__(self, analysis: MacroAnalysis) -> None:
        super().__init__()
        self.analysis = analysis
        self.setMinimumHeight(300)
        self.setAccessibleName("成本转嫁来源、通道、承接者和相对受益者路径图")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#08141D"))
        margin = 16.0
        gap = 14.0
        width = float(self.width())
        column_width = max(125.0, (width - margin * 2 - gap * 3) / 4)
        headings = ("代价来源", "转嫁机制", "主要承接者", "相对受益者")
        for index, heading in enumerate(headings):
            x = margin + index * (column_width + gap)
            painter.setPen(QColor("#8FA8B7"))
            painter.drawText(QRectF(x, 12, column_width, 24), heading)
        chains = self.analysis.cost_transfer.chains
        row_height = max(34.0, min(46.0, (self.height() - 48.0) / max(1, len(chains))))
        for row, chain in enumerate(chains):
            y = 40.0 + row * row_height
            tone = (
                QColor("#F06B71")
                if chain.pressure_score >= 70
                else QColor("#EABF5A")
                if chain.pressure_score >= 45
                else QColor("#35D0A0")
            )
            values = (chain.source, chain.channel, chain.bearer, chain.beneficiary)
            for column, value in enumerate(values):
                x = margin + column * (column_width + gap)
                rect = QRectF(x, y, column_width, row_height - 7)
                painter.setBrush(QColor("#171D24") if column < 3 else QColor("#0C2724"))
                painter.setPen(QPen(QColor("#55353C") if column < 3 else QColor("#236754"), 1))
                painter.drawRoundedRect(rect, 5, 5)
                painter.setPen(QColor("#D7E4EA"))
                painter.drawText(
                    rect.adjusted(7, 3, -7, -3),
                    Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                    value,
                )
                if column < 3:
                    painter.setPen(QPen(tone, 2))
                    painter.drawLine(
                        int(x + column_width),
                        int(y + (row_height - 7) / 2),
                        int(x + column_width + gap),
                        int(y + (row_height - 7) / 2),
                    )
            painter.setPen(tone)
            painter.drawText(
                QRectF(margin, y - 2, column_width, 14),
                Qt.AlignmentFlag.AlignRight,
                f"{chain.pressure_score}",
            )
        painter.end()


class AllocationBarWidget(QWidget):
    COLORS = (
        QColor("#65C7B4"),
        QColor("#5D8DB8"),
        QColor("#4D6FD0"),
        QColor("#2FA77F"),
        QColor("#D09A46"),
        QColor("#8B6FC8"),
        QColor("#D7B55B"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.plan: LongTermAllocationPlan | None = None
        self.setMinimumHeight(82)
        self.setAccessibleName("长期资产配置比例图")

    def set_plan(self, plan: LongTermAllocationPlan) -> None:
        self.plan = plan
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#08141D"))
        if self.plan is None:
            painter.end()
            return
        margin = 4.0
        bar = QRectF(margin, 8.0, max(1.0, self.width() - margin * 2), 58.0)
        x = bar.left()
        for index, target in enumerate(self.plan.targets):
            segment_width = bar.width() * target.target_pct / 100
            rect = QRectF(x, bar.top(), segment_width, bar.height())
            painter.setBrush(self.COLORS[index % len(self.COLORS)])
            painter.setPen(QPen(QColor("#071019"), 1))
            painter.drawRect(rect)
            if target.target_pct >= 7:
                painter.setPen(QColor("#04100D") if index in (0, 3) else QColor("#F4F8FA"))
                painter.drawText(
                    rect.adjusted(4, 3, -4, -3),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    f"{target.label}\n{target.target_pct}%",
                )
            x += segment_width
        painter.setPen(QColor("#8198A6"))
        painter.drawText(
            QRectF(margin, 66.0, bar.width(), 16.0),
            Qt.AlignmentFlag.AlignRight,
            "目标合计 100% · 区间越界才触发再平衡",
        )
        painter.end()


class MacroScoreCard(QFrame):
    def __init__(self, title: str, score: int, summary: str, tone: str = "neutral") -> None:
        super().__init__()
        self.setObjectName("macroScoreCard")
        self.setProperty("tone", tone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(_text(title, "macroCardTitle"))
        score_label = _text(f"{score}/100", "macroCardScore")
        layout.addWidget(score_label)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(score)
        progress.setTextVisible(False)
        progress.setObjectName("macroProgress")
        layout.addWidget(progress)
        layout.addWidget(_text(summary, "macroCardSummary"))


class MacroDialog(QDialog):
    def __init__(self, result: MacroResearchResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("macroResearchDialog")
        self.setWindowTitle("资本三流与成本转嫁宏观投资分析")
        self.resize(1240, 820)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("macroResearchTabs")
        analysis = result.analysis
        validity = result.validity
        self.result_validity = validity

        status_hero = QFrame()
        status_hero.setObjectName("macroValidityHero")
        status_hero.setProperty("status", validity.status)
        status_layout = QHBoxLayout(status_hero)
        status_copy = QVBoxLayout()
        status_eyebrow = _text("MACRO EVIDENCE GATE · 官方数据时效核验", "macroValidityEyebrow")
        status_copy.addWidget(status_eyebrow)
        self.validity_title = _text(validity.status_label, "macroValidityTitle")
        status_copy.addWidget(self.validity_title)
        self.validity_reason = _text(validity.reason, "macroValidityReason")
        status_copy.addWidget(self.validity_reason)
        status_layout.addLayout(status_copy, 3)
        status_layout.addStretch()
        status_meta = _text(
            f"基线截止  {analysis.snapshot.as_of.isoformat()}\n"
            f"距今  {validity.age_days} 天\n"
            f"联网核验  {sum(item.status == 'succeeded' for item in validity.source_checks)}"
            f" / {len(validity.source_checks)}",
            "macroValidityMeta",
        )
        status_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_layout.addWidget(status_meta)
        layout.addWidget(status_hero)

        provenance = QLabel(
            "数据边界：联网核验的是官方发布页时效，不会把网页片段冒充结构化指标。"
            f"模型仍基于 {analysis.snapshot.version}"
            f"（截止 {analysis.snapshot.as_of.isoformat()}）；"
            + (
                "已通过当前决策门禁。"
                if validity.current_decision_allowed
                else "未通过门禁，以下配置与行业结论仅作历史回放。"
            )
        )
        provenance.setObjectName("macroDataBoundary")
        provenance.setAccessibleName("宏观数据时效、联网核验范围与决策门禁")
        provenance.setWordWrap(True)
        self.provenance_notice = provenance
        layout.addWidget(provenance)

        freshness_page = QWidget()
        freshness_layout = QVBoxLayout(freshness_page)
        freshness_layout.setContentsMargins(14, 14, 14, 14)
        freshness_layout.setSpacing(12)
        freshness_layout.addWidget(_text("这份宏观依据现在还能不能用？", "macroTitle"))
        freshness_layout.addWidget(
            _text(
                "判断顺序：先检查快照年龄，再并行访问四个官方统计发布页；"
                "只要发现基线之后的新发布、快照超过时效上限，或联网核验不足，"
                "系统就停止输出当前仓位结论。",
                "macroTheoryNote",
            )
        )
        gate_grid = QGridLayout()
        gate_values = (
            ("快照年龄", f"{validity.age_days} 天", f"上限 {validity.max_age_days} 天"),
            (
                "更新信号",
                f"{validity.newer_release_count} 个",
                "晚于基线的官方页面发布",
            ),
            (
                "当前决策",
                "允许" if validity.current_decision_allowed else "已阻止",
                "仓位 / 行业 / 个股宏观叠加",
            ),
        )
        for column, (gate_title, gate_value, gate_detail) in enumerate(gate_values):
            card = QFrame()
            card.setObjectName("macroValidityCard")
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(_text(gate_title, "macroCardTitle"))
            card_layout.addWidget(_text(gate_value, "macroValidityValue"))
            card_layout.addWidget(_text(gate_detail, "macroAsOf"))
            gate_grid.addWidget(card, 0, column)
        freshness_layout.addLayout(gate_grid)
        freshness_layout.addWidget(_text("官方来源核验记录", "macroSectionTitle"))
        checks_table = _table(("来源", "状态", "最近页面日期", "核验说明", "官方页面"))
        checks_table.setRowCount(len(validity.source_checks))
        for row, check in enumerate(validity.source_checks):
            _set_row(
                checks_table,
                row,
                (
                    check.name,
                    "成功" if check.status == "succeeded" else "失败",
                    check.latest_published_on.isoformat()
                    if check.latest_published_on
                    else "未识别",
                    check.detail,
                    check.url,
                ),
            )
        checks_table.cellDoubleClicked.connect(
            lambda row, _column: self._open_table_url(checks_table, row, 4)
        )
        checks_table.setAccessibleName("官方宏观发布页联网核验记录，双击打开来源")
        self.freshness_table = checks_table
        freshness_layout.addWidget(checks_table, 1)
        checked_at = validity.checked_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        freshness_layout.addWidget(
            _text(
                f"核验时间：{checked_at} · 双击任意来源行可打开官方页面。"
                "页面日期用于发现更新，不等同于逐指标统计期。",
                "macroAsOf",
            )
        )
        self.tabs.addTab(freshness_page, "时效核验")

        self._allocation_plans = {
            plan.profile: plan for plan in analysis.investment_view.allocation_plans
        }
        allocation_scroll = QScrollArea()
        allocation_scroll.setWidgetResizable(True)
        allocation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        allocation_content = QWidget()
        allocation_layout = QVBoxLayout(allocation_content)
        allocation_layout.setSpacing(10)

        allocation_title = QHBoxLayout()
        allocation_heading = _text(
            "长期资产配置" if validity.current_decision_allowed else "历史配置回放",
            "macroTitle",
        )
        allocation_heading.setWordWrap(False)
        allocation_heading.setMinimumWidth(360)
        allocation_title.addWidget(allocation_heading)
        allocation_title.addStretch()
        allocation_title.addWidget(
            _text(
                f"{'可用基线' if validity.current_decision_allowed else '已失效基线'} · "
                f"{analysis.snapshot.as_of.isoformat()} · "
                f"{analysis.investment_view.risk_appetite_label}",
                "macroAsOf",
            )
        )
        allocation_layout.addLayout(allocation_title)
        if not validity.current_decision_allowed:
            allocation_layout.addWidget(
                _text(
                    "已停止执行：本页只回放旧模型如何形成配置，不代表今天应采用的仓位。"
                    "请更新同口径结构化数据并重新通过官方发布核验后再计算。",
                    "macroHistoryWarning",
                )
            )
        allocation_layout.addWidget(
            _text(
                "先做对顺序：留足应急金 → 选择能承受的回撤 → 按目标比例分四批建立 → "
                "季度检查、越界再平衡。这里配置的是长期可投资资金，不是三年内要用的钱。",
                "macroTheoryNote",
            )
        )

        controls = QFrame()
        controls.setObjectName("macroAllocationControl")
        controls_layout = QHBoxLayout(controls)
        controls_layout.addWidget(_text("风险画像", "macroCardTitle"))
        self.profile_combo = QComboBox()
        for plan in analysis.investment_view.allocation_plans:
            self.profile_combo.addItem(plan.label, plan.profile)
        default_index = self.profile_combo.findData(
            analysis.investment_view.default_allocation_profile
        )
        self.profile_combo.setCurrentIndex(max(0, default_index))
        self.profile_combo.setAccessibleName("长期配置风险画像")
        self.profile_combo.setEnabled(validity.current_decision_allowed)
        controls_layout.addWidget(self.profile_combo)
        controls_layout.addSpacing(16)
        controls_layout.addWidget(_text("可投资金额", "macroCardTitle"))
        self.investable_amount = QDoubleSpinBox()
        self.investable_amount.setRange(10_000, 100_000_000)
        self.investable_amount.setDecimals(0)
        self.investable_amount.setSingleStep(10_000)
        self.investable_amount.setValue(100_000)
        self.investable_amount.setPrefix("¥ ")
        self.investable_amount.setGroupSeparatorShown(True)
        self.investable_amount.setAccessibleName("可投资金额，不含应急金")
        self.investable_amount.setEnabled(validity.current_decision_allowed)
        controls_layout.addWidget(self.investable_amount)
        controls_layout.addStretch()
        controls_layout.addWidget(_text("默认不使用杠杆 · 不自动下单", "macroAsOf"))
        allocation_layout.addWidget(controls)

        allocation_hero = QFrame()
        allocation_hero.setObjectName("macroAllocationHero")
        allocation_hero_layout = QHBoxLayout(allocation_hero)
        self.allocation_summary = _text("", "macroAllocationSummary")
        allocation_hero_layout.addWidget(self.allocation_summary, 2)
        self.allocation_equity = _text("", "macroAllocationEquity")
        allocation_hero_layout.addWidget(self.allocation_equity, 1)
        allocation_layout.addWidget(allocation_hero)

        self.allocation_bar = AllocationBarWidget()
        allocation_layout.addWidget(self.allocation_bar)
        self.allocation_table = _table(
            (
                "资产桶",
                "战略基准",
                "当前目标" if validity.current_decision_allowed else "历史目标",
                "允许区间",
                "按金额" if validity.current_decision_allowed else "历史模拟金额",
                "本轮动作" if validity.current_decision_allowed else "历史动作",
                "怎么实现",
                "为什么配置",
            )
        )
        self.allocation_table.setMinimumHeight(232)
        allocation_layout.addWidget(self.allocation_table)

        allocation_layout.addWidget(
            _text(
                "四批建仓：今天开始怎么做"
                if validity.current_decision_allowed
                else "历史模型建仓步骤（已停用）",
                "macroSectionTitle",
            )
        )
        steps_grid = QGridLayout()
        self.allocation_step_titles: list[QLabel] = []
        self.allocation_step_amounts: list[QLabel] = []
        self.allocation_step_bodies: list[QLabel] = []
        self.allocation_step_gates: list[QLabel] = []
        for index in range(4):
            step_card = QFrame()
            step_card.setObjectName("allocationStep")
            step_layout = QVBoxLayout(step_card)
            title = _text("", "allocationStepIndex")
            amount = _text("", "allocationStepAmount")
            body = _text("", "macroConclusionText")
            gate = _text("", "macroAsOf")
            step_layout.addWidget(title)
            step_layout.addWidget(amount)
            step_layout.addWidget(body)
            step_layout.addWidget(gate)
            self.allocation_step_titles.append(title)
            self.allocation_step_amounts.append(amount)
            self.allocation_step_bodies.append(body)
            self.allocation_step_gates.append(gate)
            steps_grid.addWidget(step_card, 0, index)
        allocation_layout.addLayout(steps_grid)

        rule_grid = QGridLayout()
        rebalance = QFrame()
        rebalance.setObjectName("macroConclusion")
        rebalance_layout = QVBoxLayout(rebalance)
        rebalance_layout.addWidget(_text("什么时候再平衡", "macroSectionTitle"))
        self.allocation_rules_text = _text("", "macroConclusionText")
        rebalance_layout.addWidget(self.allocation_rules_text)
        rule_grid.addWidget(rebalance, 0, 0)

        triggers = QFrame()
        triggers.setObjectName("macroBottleneck")
        triggers_layout = QVBoxLayout(triggers)
        triggers_layout.addWidget(_text("什么时候加风险 / 降风险", "macroSectionTitle"))
        self.allocation_triggers_text = _text("", "macroConclusionText")
        triggers_layout.addWidget(self.allocation_triggers_text)
        rule_grid.addWidget(triggers, 0, 1)
        allocation_layout.addLayout(rule_grid)

        guardrails = QFrame()
        guardrails.setObjectName("macroGuardrail")
        guardrails_layout = QVBoxLayout(guardrails)
        guardrails_layout.addWidget(_text("长期组合护栏", "macroSectionTitle"))
        self.allocation_guardrails_text = _text("", "macroConclusionText")
        guardrails_layout.addWidget(self.allocation_guardrails_text)
        allocation_layout.addWidget(guardrails)
        allocation_layout.addStretch()
        allocation_scroll.setWidget(allocation_content)
        self.allocation_page = allocation_scroll
        self.tabs.addTab(
            allocation_scroll,
            "长期配置" if validity.current_decision_allowed else "历史配置（已失效）",
        )

        self.profile_combo.currentIndexChanged.connect(self._render_allocation_plan)
        self.investable_amount.valueChanged.connect(self._render_allocation_plan)
        self._render_allocation_plan()

        dashboard = QWidget()
        dashboard_layout = QVBoxLayout(dashboard)
        title_row = QHBoxLayout()
        title_row.addWidget(_text("宏观投资仪表盘", "macroTitle"))
        title_row.addStretch()
        title_row.addWidget(
            _text(
                f"历史基线 · {analysis.snapshot.as_of.isoformat()} · {analysis.snapshot.label}",
                "macroAsOf",
            )
        )
        dashboard_layout.addLayout(title_row)
        score_grid = QGridLayout()
        flow = analysis.capital_flow
        view = analysis.investment_view
        transfer = analysis.cost_transfer
        cards = (
            MacroScoreCard("资本流量", flow.volume_score, flow.volume_label),
            MacroScoreCard(
                "资本流向",
                max(0, min(100, 50 + round(flow.direction_score / 2))),
                flow.direction_label,
            ),
            MacroScoreCard("资本流速", flow.speed_score, flow.speed_label),
            MacroScoreCard("实体传导", flow.transmission_score, flow.transmission_label),
            MacroScoreCard(
                "成本转嫁压力",
                transfer.pressure_score,
                transfer.pressure_label,
                "risk",
            ),
            MacroScoreCard(
                "权益风险偏好",
                view.risk_appetite_score,
                view.risk_appetite_label,
            ),
        )
        for index, card in enumerate(cards):
            score_grid.addWidget(card, index // 3, index % 3)
        dashboard_layout.addLayout(score_grid)

        conclusion = QFrame()
        conclusion.setObjectName("macroConclusion")
        conclusion_layout = QVBoxLayout(conclusion)
        conclusion_layout.addWidget(
            _text(
                "当前资产配置结论"
                if validity.current_decision_allowed
                else "历史模型回放 · 不用于当前决策",
                "macroSectionTitle",
            )
        )
        conclusion_layout.addWidget(_text(view.equity_exposure, "macroConclusionText"))
        for item in view.decision_summary:
            conclusion_layout.addWidget(_text(f"• {item}", "macroConclusionText"))
        dashboard_layout.addWidget(conclusion)
        bottleneck = QFrame()
        bottleneck.setObjectName("macroBottleneck")
        bottleneck_layout = QVBoxLayout(bottleneck)
        bottleneck_layout.addWidget(_text("当前传导堵点", "macroSectionTitle"))
        for item in flow.bottlenecks:
            bottleneck_layout.addWidget(_text(f"• {item}", "macroConclusionText"))
        dashboard_layout.addWidget(bottleneck)
        dashboard_layout.addStretch()
        self.tabs.addTab(
            dashboard,
            "宏观仪表盘" if validity.current_decision_allowed else "历史仪表盘",
        )

        flow_page = QWidget()
        flow_layout = QVBoxLayout(flow_page)
        flow_layout.addWidget(
            _text(
                "资本三流工程口径：流量回答“钱有多少”，流向回答“流到哪里”，"
                "流速回答“周转是否活跃”；实体传导单列，避免把宽货币直接当作景气。",
                "macroTheoryNote",
            )
        )
        self.flow_map = FlowMapWidget(analysis)
        flow_layout.addWidget(self.flow_map)
        paths = _table(("路径", "维度", "状态/分数", "资金来源", "通道", "承接部门", "投资含义"))
        paths.setRowCount(len(flow.paths))
        for row, path in enumerate(flow.paths):
            _set_row(
                paths,
                row,
                (
                    path.name,
                    path.dimension,
                    f"{path.status} / {path.score}",
                    path.source,
                    path.channel,
                    path.destination,
                    path.investment_effect,
                ),
            )
        self.paths_table = paths
        flow_layout.addWidget(paths, 1)
        self.tabs.addTab(flow_page, f"资本三流路径（{len(flow.paths)}）")

        transfer_page = QWidget()
        transfer_layout = QVBoxLayout(transfer_page)
        transfer_layout.addWidget(
            _text(
                "成本转嫁工程口径：识别资本化或周期调整中的代价从哪里产生、"
                "通过什么制度/价格/资产负债表通道转移、由谁承担、谁相对受益。"
                "以下分数是求衡的可验证代理，不是理论作者给出的计量公式。",
                "macroTheoryNote",
            )
        )
        self.transfer_map = CostTransferMapWidget(analysis)
        transfer_layout.addWidget(self.transfer_map)
        chains = _table(
            (
                "成本转嫁链",
                "压力",
                "成本来源",
                "传导通道",
                "承接主体",
                "相对受益者",
                "投资含义",
                "验证指标",
                "反转条件",
            )
        )
        chain_rows = transfer.chains
        chains.setRowCount(len(chain_rows))
        for row, chain in enumerate(chain_rows):
            _set_row(
                chains,
                row,
                (
                    chain.name,
                    f"{chain.pressure_score}/100",
                    chain.source,
                    chain.channel,
                    chain.bearer,
                    chain.beneficiary,
                    chain.investment_effect,
                    chain.confirmation,
                    chain.reversal_conditions,
                ),
            )
        self.chains_table = chains
        transfer_layout.addWidget(chains, 1)
        self.tabs.addTab(transfer_page, f"成本转嫁链（{len(chain_rows)}）")

        sectors = _table(("行业", "建议", "置信度", "配置逻辑", "确认条件", "主要风险"))
        sector_rows = view.sectors
        sectors.setRowCount(len(sector_rows))
        for row, sector in enumerate(sector_rows):
            _set_row(
                sectors,
                row,
                (
                    sector.sector,
                    sector.stance_label,
                    f"{sector.confidence}/100",
                    sector.rationale,
                    sector.confirmation,
                    sector.risk,
                ),
            )
        self.sectors_table = sectors
        self.tabs.addTab(sectors, f"行业配置（{len(sector_rows)}）")

        snapshot = analysis.snapshot
        sources = _table(("类型", "指标/方法", "统计期", "来源", "链接"))
        rows = [
            ("数据", metric.name, metric.period, metric.source_name, metric.source_url)
            for metric in snapshot.metrics
        ]
        rows.extend(("方法", name, "—", name, url) for name, url in snapshot.methodology_sources)
        sources.setRowCount(len(rows))
        for row, source_values in enumerate(rows):
            _set_row(sources, row, source_values)
        self.sources_table = sources
        self.tabs.addTab(sources, f"数据与来源（{len(rows)}）")

        plan_view = QTextBrowser()
        plan_view.setMarkdown(self._plan_text(result))
        plan_view.setReadOnly(True)
        plan_view.setObjectName("summaryView")
        plan_view.setAccessibleName("宏观 Agent 工作计划")
        self.plan_view = plan_view
        self.tabs.addTab(plan_view, "Agent 计划")

        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _open_table_url(table: QTableWidget, row: int, column: int) -> None:
        item = table.item(row, column)
        if item is not None and item.text().startswith("https://"):
            QDesktopServices.openUrl(QUrl(item.text()))

    def _render_allocation_plan(self, *_: object) -> None:
        profile = str(self.profile_combo.currentData())
        plan = self._allocation_plans[profile]
        amount = round(self.investable_amount.value())
        self.allocation_summary.setText(
            f"{plan.label} · {plan.horizon}\n{plan.suitability}\n{plan.drawdown_tolerance}"
        )
        target_label = (
            "当前宏观目标"
            if self.result_validity.current_decision_allowed
            else "历史宏观目标"
        )
        self.allocation_equity.setText(
            f"权益战略中枢 {plan.equity_strategic_pct}%\n"
            f"{target_label} {plan.equity_target_pct}%\n"
            f"策略版本 {plan.strategy_version}"
        )
        self.allocation_bar.set_plan(plan)
        self.allocation_table.setRowCount(len(plan.targets))
        for row, target in enumerate(plan.targets):
            target_amount = round(amount * target.target_pct / 100)
            _set_row(
                self.allocation_table,
                row,
                (
                    target.label,
                    f"{target.strategic_pct}%",
                    f"{target.target_pct}%",
                    f"{target.minimum_pct}%–{target.maximum_pct}%",
                    f"¥{target_amount:,.0f}",
                    target.action_label,
                    target.vehicles,
                    f"{target.purpose}；宏观：{target.macro_rationale}；"
                    f"主要风险：{target.primary_risk}",
                ),
            )
        for index, step in enumerate(plan.build_steps):
            step_amount = round(amount * step.portfolio_pct / 100)
            self.allocation_step_titles[index].setText(
                f"第 {step.order} 批 · {step.timing} · {step.portfolio_pct}%"
            )
            self.allocation_step_amounts[index].setText(f"¥{step_amount:,.0f}")
            self.allocation_step_bodies[index].setText(step.instruction)
            self.allocation_step_gates[index].setText(f"执行前：{step.gate}")
        self.allocation_rules_text.setText(
            "\n".join(f"{index}. {rule}" for index, rule in enumerate(plan.rebalance_rules, 1))
        )
        self.allocation_triggers_text.setText(
            "加风险\n"
            + "\n".join(f"• {item}" for item in plan.increase_risk_triggers)
            + "\n\n降风险\n"
            + "\n".join(f"• {item}" for item in plan.decrease_risk_triggers)
        )
        self.allocation_guardrails_text.setText(
            plan.prerequisite + "\n" + "\n".join(f"• {item}" for item in plan.guardrails)
        )

    @staticmethod
    def _plan_text(result: MacroResearchResult) -> str:
        plan = result.plan
        lines = [
            "# 宏观 Agent 计划",
            f"计划状态：{plan.get('status', 'unknown')}",
            f"隔离工作区：{result.workspace}",
            "",
            "## 子智能体任务",
        ]
        tasks = plan.get("tasks", [])
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                skills = ", ".join(str(item) for item in task.get("skills", []))
                lines.append(
                    f"- {task.get('id')} · {task.get('status')} · {task.get('agent')}"
                    f" · skills=[{skills}]"
                )
        return "\n".join(lines)
