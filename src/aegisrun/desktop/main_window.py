from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, Qt, QThreadPool
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aegisrun.agents.investment_conversation import (
    InvestmentConversationStore,
    InvestmentIntent,
    InvestmentIntentRouter,
    InvestmentMemory,
)
from aegisrun.agents.investment_runtime import (
    InvestmentAgentRunRequest,
    InvestmentAgentRunResult,
    SkillSelectionMode,
)
from aegisrun.application.requests import (
    BacktestRequest,
    CandidateScreenRequest,
    CandidateScreenResult,
    InvestmentAgentTaskRequest,
    InvestmentChatRequest,
    ResearchRequest,
    SectorContextRequest,
)
from aegisrun.desktop.advisor_widget import HoldingAdvisorWidget
from aegisrun.desktop.charts import (
    ChartData,
    IndicatorChartWidget,
    PriceChartWidget,
    build_chart_data,
)
from aegisrun.desktop.credentials import CredentialStore
from aegisrun.desktop.decision_widget import InvestmentDecisionWidget
from aegisrun.desktop.indicator_selector import MultiIndicatorSelector
from aegisrun.desktop.macro_dialog import MacroDialog
from aegisrun.desktop.market_context_widget import MarketContextWidget
from aegisrun.desktop.portfolio_dialog import (
    BacktestConfigDialog,
    BacktestDialog,
    CandidateResultsDialog,
    PortfolioDialog,
)
from aegisrun.desktop.settings_dialog import SettingsDialog
from aegisrun.desktop.skill_manager_dialog import SkillManagerDialog
from aegisrun.desktop.workers import (
    AdvisorChatTask,
    BacktestTask,
    CandidateScreenTask,
    InvestmentAgentTask,
    InvestmentChatTask,
    MacroTask,
    ResearchTask,
    SectorContextTask,
)
from aegisrun.macro.pipeline import MacroResearchResult
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.timeframes import Timeframe
from aegisrun.portfolio.analysis import CandidateInput, assess_holding, rank_strategy_candidates
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.portfolio.strategy_dsl import CandidateStrategy, candidate_strategy_from_skill
from aegisrun.research.advisor_chat import (
    AdvisorAnswer,
    AdvisorConversationContext,
    AdvisorTurn,
    build_advisor_evidence,
    validate_advisor_question,
)
from aegisrun.research.backtest import BacktestReport
from aegisrun.research.deepseek import deepseek_model_label, normalize_deepseek_model
from aegisrun.research.market_context import ContextInstrument, MarketTrendContext
from aegisrun.research.service import ResearchResult, attach_sector_context
from aegisrun.research.signals import build_signal_summary
from aegisrun.skills import (
    SkillPackage,
    SkillValidationError,
    SkillWorkspace,
    SkillWorkspacePolicy,
)

SOURCE_OPTIONS = (
    ("公开历史数据（BaoStock，无需账号）", "baostock"),
    ("Tushare 真实行情（需独立 Token）", "tushare"),
    ("离线模拟数据（无需账号，非真实行情）", "demo"),
)


def _value(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.2f}{suffix}"


class MetricCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(82)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(3)
        self.title = QLabel(title.upper())
        self.title.setObjectName("metricLabel")
        self.number = QLabel("—")
        self.number.setObjectName("metricValue")
        self.number.setAccessibleName(title)
        layout.addWidget(self.title)
        layout.addWidget(self.number)

    def set_title(self, title: str) -> None:
        self.title.setText(title.upper())
        self.number.setAccessibleName(title)

    def set_value(self, value: str, tone: str = "neutral") -> None:
        self.number.setText(value)
        self.number.setProperty("tone", tone)
        self.number.style().unpolish(self.number)
        self.number.style().polish(self.number)


class MainWindow(QMainWindow):
    def __init__(
        self,
        credentials: CredentialStore | None = None,
        settings: QSettings | None = None,
        portfolio_repository: PortfolioRepository | None = None,
    ) -> None:
        super().__init__()
        self.credentials = credentials or CredentialStore()
        self.settings = settings or QSettings()
        self.portfolio_repository = portfolio_repository or PortfolioRepository()
        self.thread_pool = QThreadPool.globalInstance()
        self._tasks: set[ResearchTask] = set()
        self._sector_tasks: set[SectorContextTask] = set()
        self._screen_tasks: set[CandidateScreenTask] = set()
        self._backtest_tasks: set[BacktestTask] = set()
        self._macro_tasks: set[MacroTask] = set()
        self._advisor_tasks: set[AdvisorChatTask | InvestmentChatTask | InvestmentAgentTask] = set()
        self._investment_router = InvestmentIntentRouter()
        self._conversation_store = InvestmentConversationStore(
            self.portfolio_repository.path.parent / "conversations"
        )
        self._skill_workspace_warning = ""
        self._skill_workspace = self._load_skill_workspace()
        self._last_result: ResearchResult | None = None
        self._last_request: ResearchRequest | None = None
        self._chart_data: ChartData | None = None
        self.setWindowTitle("EquiSeek 求衡 · 智能投研平台")
        self.resize(1500, 1040)
        self.setMinimumSize(1060, 720)
        self._build_menu()
        self._build_ui()
        self._restore_preferences()
        self._update_source_notice()
        self.statusBar().showMessage("就绪 · 默认使用无需 Token 的 BaoStock 公开历史数据")

    def _build_menu(self) -> None:
        settings_action = QAction("连接设置…", self)
        settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        settings_action.triggered.connect(self.open_settings)
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        about_action = QAction("关于与方法说明", self)
        about_action.triggered.connect(self._show_about)
        portfolio_action = QAction("持仓与自选…", self)
        portfolio_action.triggered.connect(self.open_portfolio)
        advisor_action = QAction("投研助手", self)
        advisor_action.triggered.connect(self.open_advisor)
        skills_action = QAction("管理 Skill…", self)
        skills_action.triggered.connect(self.open_skill_manager)
        screen_action = QAction("扫描本地候选池", self)
        screen_action.triggered.connect(self.start_candidate_screen)
        macro_action = QAction("宏观联网核验…", self)
        macro_action.triggered.connect(self.start_macro_analysis)
        app_menu = self.menuBar().addMenu("求衡")
        app_menu.addAction(settings_action)
        app_menu.addAction(portfolio_action)
        app_menu.addAction(advisor_action)
        app_menu.addAction(skills_action)
        app_menu.addAction(screen_action)
        app_menu.addAction(macro_action)
        app_menu.addSeparator()
        app_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(about_action)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("researchPage")
        page = QVBoxLayout(central)
        page.setContentsMargins(20, 16, 20, 16)
        page.setSpacing(12)

        title_row = QHBoxLayout()
        title_block = QVBoxLayout()
        eyebrow = QLabel("AI 投资决策工作台 · LOCAL")
        eyebrow.setObjectName("researchEyebrow")
        title_block.addWidget(eyebrow)
        title = QLabel("个股研究")
        title.setObjectName("pageTitle")
        subtitle = QLabel("先看结论，再核对行情证据、触发条件与 Agent 计划")
        subtitle.setObjectName("pageSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_row.addLayout(title_block)
        title_row.addStretch()
        self.ai_status = QLabel()
        self.ai_status.setObjectName("statusPill")
        title_row.addWidget(self.ai_status)
        advisor_button = QPushButton("投研助手")
        advisor_button.setObjectName("secondaryButton")
        advisor_button.clicked.connect(self.open_advisor)
        title_row.addWidget(advisor_button)
        portfolio_button = QPushButton("持仓与自选")
        portfolio_button.setObjectName("secondaryButton")
        portfolio_button.clicked.connect(self.open_portfolio)
        title_row.addWidget(portfolio_button)
        self.screen_button = QPushButton("扫描候选池")
        self.screen_button.setObjectName("secondaryButton")
        self.screen_button.clicked.connect(self.start_candidate_screen)
        title_row.addWidget(self.screen_button)
        self.backtest_button = QPushButton("策略回测")
        self.backtest_button.setObjectName("secondaryButton")
        self.backtest_button.setEnabled(False)
        self.backtest_button.clicked.connect(self.start_backtest)
        title_row.addWidget(self.backtest_button)
        self.macro_button = QPushButton("宏观联网核验")
        self.macro_button.setObjectName("secondaryButton")
        self.macro_button.setToolTip(
            "联网核验国家统计局、人民银行、外汇局和财政部官方发布页；过期结论自动失效"
        )
        self.macro_button.setAccessibleDescription(
            "核验宏观结构化基线是否仍可用于当前决策，并显示官方来源与时效门禁"
        )
        self.macro_button.clicked.connect(self.start_macro_analysis)
        title_row.addWidget(self.macro_button)
        settings_button = QPushButton("连接设置")
        settings_button.setObjectName("secondaryButton")
        settings_button.clicked.connect(self.open_settings)
        title_row.addWidget(settings_button)
        page.addLayout(title_row)

        warning = QLabel(
            "输出可回测的规则型投资建议与方向情景；不保证收益、不连接券商、不自动下单，实盘前请核对最新数据。"
        )
        warning.setObjectName("complianceBanner")
        warning.setWordWrap(True)
        page.addWidget(warning)

        controls = QFrame()
        controls.setObjectName("controlPanel")
        control_layout = QGridLayout(controls)
        control_layout.setContentsMargins(16, 13, 16, 13)
        control_layout.setHorizontalSpacing(12)
        control_layout.setVerticalSpacing(7)
        self.source_combo = QComboBox()
        self.source_combo.setObjectName("sourceCombo")
        for label, key in SOURCE_OPTIONS:
            self.source_combo.addItem(label, key)
        self.source_combo.currentIndexChanged.connect(self._update_source_notice)
        self.symbol_input = QLineEdit("600519.SH")
        self.symbol_input.setObjectName("symbolInput")
        self.symbol_input.setPlaceholderText("例如 600519.SH / 000001.SZ / 920001.BJ")
        self.symbol_input.setClearButtonEnabled(True)
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        today = date.today()
        self.start_date.setDate(
            QDate(
                (today - timedelta(days=2_500)).year,
                (today - timedelta(days=2_500)).month,
                (today - timedelta(days=2_500)).day,
            )
        )
        self.end_date.setDate(QDate(today.year, today.month, today.day))
        self.adjustment_combo = QComboBox()
        for adjustment in AdjustmentMode:
            self.adjustment_combo.addItem(adjustment.label, adjustment.value)
        self.adjustment_combo.setCurrentIndex(1)
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.setAccessibleName("图表周期")
        self.timeframe_combo.setToolTip("切换日线、周线或月线；周/月线由当前同源日 K 本地聚合")
        for timeframe in Timeframe:
            self.timeframe_combo.addItem(timeframe.label, timeframe.value)
        self.timeframe_combo.currentIndexChanged.connect(self._change_timeframe)
        self.indicator_selector = MultiIndicatorSelector()
        self.indicator_selector.selection_changed.connect(self._change_indicators)
        self.analyze_button = QPushButton("开始分析")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.clicked.connect(self.start_analysis)

        fields = (
            ("数据源", self.source_combo),
            ("股票代码", self.symbol_input),
            ("开始日期", self.start_date),
            ("结束日期", self.end_date),
            ("复权方式", self.adjustment_combo),
            ("图表周期", self.timeframe_combo),
            ("指标（最多3）", self.indicator_selector),
        )
        for column, (label, widget) in enumerate(fields):
            caption = QLabel(label)
            caption.setObjectName("fieldLabel")
            control_layout.addWidget(caption, 0, column)
            control_layout.addWidget(widget, 1, column)
        control_layout.setColumnStretch(0, 2)
        control_layout.setColumnStretch(1, 3)
        control_layout.setColumnStretch(len(fields) - 1, 2)
        control_layout.addWidget(self.analyze_button, 1, len(fields))
        page.addWidget(controls)

        self.source_notice = QLabel()
        self.source_notice.setObjectName("sourceNotice")
        self.source_notice.setWordWrap(True)
        page.addWidget(self.source_notice)

        self.research_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.research_splitter.setObjectName("researchSplitter")
        self.research_splitter.setAccessibleName("左侧行情证据与右侧核心投资决策")
        self.research_splitter.setToolTip("拖动分隔线调整行情证据区与投资决策区宽度")
        self.research_splitter.setChildrenCollapsible(False)

        self.market_evidence_panel = QFrame()
        self.market_evidence_panel.setObjectName("marketEvidencePanel")
        self.market_evidence_panel.setMinimumWidth(550)
        evidence_layout = QVBoxLayout(self.market_evidence_panel)
        evidence_layout.setContentsMargins(0, 0, 0, 0)
        evidence_layout.setSpacing(12)

        metric_grid = QGridLayout()
        metric_grid.setSpacing(9)
        metric_names = ("收盘价", "日涨跌", "MA5 / MA20", "MACD", "RSI6", "ATR20", "WR10")
        self.metrics: dict[str, MetricCard] = {}
        for column, name in enumerate(metric_names):
            card = MetricCard(name)
            self.metrics[name] = card
            metric_grid.addWidget(card, 0, column)
        evidence_layout.addLayout(metric_grid)

        self.market_context_widget = MarketContextWidget()
        self.market_context_widget.sector_load_requested.connect(self.start_sector_analysis)
        evidence_layout.addWidget(self.market_context_widget)

        chart_container = QFrame()
        chart_container.setObjectName("chartPanel")
        chart_layout = QVBoxLayout(chart_container)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(6)
        self.price_chart = PriceChartWidget()
        self.indicator_charts = [IndicatorChartWidget() for _ in range(3)]
        self.indicator_chart = self.indicator_charts[0]
        chart_layout.addWidget(self.price_chart, 2)
        for indicator_chart in self.indicator_charts:
            indicator_chart.hide()
            chart_layout.addWidget(indicator_chart, 1)
        self.chart_layout = chart_layout
        evidence_layout.addWidget(chart_container, 1)
        self.research_splitter.addWidget(self.market_evidence_panel)

        self.decision_panel = QFrame()
        self.decision_panel.setObjectName("decisionPanel")
        self.decision_panel.setMinimumWidth(410)
        self.decision_panel.setAccessibleName("核心投资决策区")
        decision_layout = QVBoxLayout(self.decision_panel)
        decision_layout.setContentsMargins(10, 10, 10, 10)
        decision_layout.setSpacing(7)
        decision_header = QHBoxLayout()
        decision_title = QLabel("核心投资决策")
        decision_title.setObjectName("decisionPanelTitle")
        decision_hint = QLabel("先看结论，再核对证据与计划")
        decision_hint.setObjectName("mutedText")
        decision_header.addWidget(decision_title)
        decision_header.addStretch()
        decision_header.addWidget(decision_hint)
        decision_layout.addLayout(decision_header)

        self.summary_tabs = QTabWidget()
        self.summary_tabs.setObjectName("summaryTabs")
        self.decision_summary = InvestmentDecisionWidget()
        self.summary_tabs.addTab(self.decision_summary, "投资结论")
        self.fact_summary = self._summary_view("规则化历史事实摘要")
        self.ai_summary = self._summary_view("DeepSeek 仅对结构化事实进行语言整理")
        self.summary_tabs.addTab(self.fact_summary, "事实摘要")
        self.summary_tabs.addTab(self.ai_summary, "AI 整理")
        self.strategy_summary = self._summary_view("多周期 MACD/WR 与当前持仓技术状态")
        self.summary_tabs.addTab(self.strategy_summary, "多周期/持仓")
        self.plan_summary = self._summary_view("分析工作计划与子任务状态")
        self.summary_tabs.addTab(self.plan_summary, "工作计划")
        decision_layout.addWidget(self.summary_tabs, 1)
        self.research_splitter.addWidget(self.decision_panel)
        self.research_splitter.setCollapsible(0, False)
        self.research_splitter.setCollapsible(1, False)
        self.research_splitter.setStretchFactor(0, 5)
        self.research_splitter.setStretchFactor(1, 3)
        self.research_splitter.setSizes([860, 580])
        page.addWidget(self.research_splitter, 1)

        footer = QLabel(
            "公式口径 canonical-cn-2026.08.1 / macd-wr-mtf-2026.08.3 / "
            "market-sector-confluence-2026.08.1 · 月/周未收盘 K 不进入正式信号"
        )
        footer.setObjectName("footerText")
        page.addWidget(footer)
        self.research_page = central
        self.advisor_widget = HoldingAdvisorWidget()
        self.advisor_widget.question_submitted.connect(self._submit_advisor_question)
        self.advisor_widget.analysis_requested.connect(self._analyze_advisor_position)
        self.advisor_widget.position_changed.connect(self._advisor_position_changed)
        self.advisor_widget.clear_requested.connect(self._confirm_clear_advisor_conversation)
        self.advisor_widget.conversation_cleared.connect(self._clear_advisor_conversation)
        self.advisor_widget.workspace_choose_requested.connect(
            self._choose_investment_agent_workspace
        )
        self.advisor_widget.workspace_reset_requested.connect(
            self._reset_investment_agent_workspace
        )
        self.advisor_widget.skill_manager_requested.connect(self.open_skill_manager)
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspaceTabs")
        self.workspace_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.workspace_tabs.addTab(self.research_page, "个股研究")
        self.workspace_tabs.addTab(self.advisor_widget, "投研助手")
        self.workspace_tabs.currentChanged.connect(self._workspace_tab_changed)
        self.setCentralWidget(self.workspace_tabs)
        self.setStatusBar(QStatusBar())

        analyze_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.research_page)
        analyze_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        analyze_shortcut.activated.connect(self.start_analysis)
        self._analyze_shortcut = analyze_shortcut
        QShortcut(QKeySequence("Ctrl+L"), self, self._focus_symbol)
        self._refresh_advisor_runtime_options()
        self._reload_advisor_positions()

    @staticmethod
    def _summary_view(accessible_name: str) -> QTextBrowser:
        view = QTextBrowser()
        view.setReadOnly(True)
        view.setObjectName("summaryView")
        view.setAccessibleName(accessible_name)
        view.setPlaceholderText("完成一次分析后显示")
        view.setOpenExternalLinks(True)
        return view

    @staticmethod
    def _set_markdown(view: QTextBrowser, text: str) -> None:
        view.setMarkdown(text)

    def _focus_symbol(self) -> None:
        self.symbol_input.setFocus()
        self.symbol_input.selectAll()

    def _restore_preferences(self) -> None:
        source = self.settings.value("research/source", "baostock", type=str)
        index = self.source_combo.findData(source)
        self.source_combo.setCurrentIndex(max(index, 0))
        symbol = self.settings.value("research/symbol", "600519.SH", type=str)
        self.symbol_input.setText(str(symbol))
        timeframe_value = self.settings.value("chart/timeframe", Timeframe.DAILY.value, type=str)
        timeframe_index = self.timeframe_combo.findData(timeframe_value)
        if timeframe_index < 0:
            timeframe_index = self.timeframe_combo.findData(Timeframe.DAILY.value)
            self.settings.setValue("chart/timeframe", Timeframe.DAILY.value)
        self.timeframe_combo.setCurrentIndex(timeframe_index)
        raw_indicators = str(self.settings.value("chart/indicators", "MA,MACD,WR", type=str))
        try:
            self.indicator_selector.set_selected(
                tuple(item for item in raw_indicators.split(",") if item)
            )
        except ValueError:
            self.indicator_selector.set_selected(("MA", "MACD", "WR"))
            self.settings.setValue("chart/indicators", "MA,MACD,WR")
        self._refresh_ai_status()

    def _refresh_ai_status(self) -> None:
        use_ai = bool(self.settings.value("research/use_ai", True, type=bool))
        has_key = bool(self.credentials.get_deepseek_api_key())
        if use_ai and has_key:
            self.ai_status.setText(f"{deepseek_model_label(self._deepseek_model())} · Key 已配置")
            self.ai_status.setProperty("active", True)
        elif use_ai:
            self.ai_status.setText("AI 未配置 · 规则摘要可用")
            self.ai_status.setProperty("active", False)
        else:
            self.ai_status.setText("AI 已关闭 · 规则摘要可用")
            self.ai_status.setProperty("active", False)
        self.ai_status.style().unpolish(self.ai_status)
        self.ai_status.style().polish(self.ai_status)

    def _deepseek_model(self) -> str:
        stored = self.settings.value("research/deepseek_model", "deepseek-v4-flash")
        model = normalize_deepseek_model(stored)
        if stored != model:
            self.settings.setValue("research/deepseek_model", model)
        return model

    def _update_source_notice(self) -> None:
        source = self.source_combo.currentData()
        if source == "demo":
            self.source_notice.setText(
                "模拟模式：数据由本地确定性算法合成，不代表任何真实证券、交易所或历史价格。"
            )
            self.source_notice.setProperty("synthetic", True)
        elif source == "tushare":
            if self.credentials.get_tushare_token():
                self.source_notice.setText(
                    "Tushare 模式：使用你自己的独立 Tushare Token 获取真实历史日线；"
                    "可用范围和频率由账户权限决定。真实行情按来源、股票和复权口径隔离保存，"
                    "后续查询只增量补齐缺口。"
                )
            else:
                self.source_notice.setText(
                    "尚未配置 Tushare Token。DeepSeek Key 不能获取行情；开始分析时可选择"
                    "无需账号的 BaoStock 公开历史数据，或明确使用非真实的离线模拟数据。"
                )
            self.source_notice.setProperty("synthetic", False)
        else:
            self.source_notice.setText(
                "公开历史数据：BaoStock 无需账号或 Token，可获取沪深股票真实历史日线；"
                "同时支持常用大盘和中证行业指数。接口可用性受上游服务影响。"
                "真实行情按来源、证券和复权口径隔离保存，"
                "后续查询只增量补齐缺口。"
            )
            self.source_notice.setProperty("synthetic", False)
        self.source_notice.style().unpolish(self.source_notice)
        self.source_notice.style().polish(self.source_notice)

    @property
    def chart_data(self) -> ChartData | None:
        return self._chart_data

    def _change_timeframe(self, _: int) -> None:
        value = str(self.timeframe_combo.currentData())
        self.settings.setValue("chart/timeframe", value)
        self._refresh_chart_view()
        self.market_context_widget.set_timeframe(Timeframe(value))

    def _change_indicators(self, selected: object) -> None:
        if not isinstance(selected, tuple):
            return
        self.settings.setValue("chart/indicators", ",".join(selected))
        self._refresh_chart_view()

    def _refresh_chart_view(self) -> None:
        result = self._last_result
        if result is None:
            return
        timeframe = Timeframe(str(self.timeframe_combo.currentData()))
        data = build_chart_data(result, timeframe)
        selected = self.indicator_selector.selected_indicators
        self._chart_data = data
        self.price_chart.set_overlays(selected)
        self.price_chart.set_chart_data(data)
        subchart_modes = [mode for mode in selected if mode in IndicatorChartWidget.MODES]
        for index, chart in enumerate(self.indicator_charts):
            if index < len(subchart_modes):
                chart.set_mode(subchart_modes[index])
                chart.set_chart_data(data)
                chart.show()
                self.chart_layout.setStretch(index + 1, 1)
            else:
                chart.hide()
                self.chart_layout.setStretch(index + 1, 0)
        self.chart_layout.setStretch(0, 3 if len(subchart_modes) < 3 else 2)
        self._render_chart_metrics(data)

    def _render_chart_metrics(self, data: ChartData) -> None:
        bars = data.bars
        latest = bars[-1]
        previous = bars[-2].close if len(bars) > 1 else latest.pre_close
        change = None if not previous else (latest.close / previous - 1) * 100
        tone = "positive" if change is not None and change >= 0 else "negative"
        indicators = data.indicators

        def latest_value(values: tuple[float | None, ...]) -> float | None:
            return next((value for value in reversed(values) if value is not None), None)

        label = data.timeframe.label
        self.metrics["收盘价"].set_title(f"{label}收盘价")
        self.metrics["日涨跌"].set_title(f"{label}涨跌")
        self.metrics["MA5 / MA20"].set_title(f"{label} MA5 / MA20")
        self.metrics["MACD"].set_title(f"{label} MACD")
        self.metrics["RSI6"].set_title(f"{label} RSI6")
        self.metrics["ATR20"].set_title(f"{label} ATR20")
        self.metrics["WR10"].set_title(f"{label} WR10")
        self.metrics["收盘价"].set_value(_value(latest.close))
        self.metrics["日涨跌"].set_value(_value(change, "%"), tone)
        self.metrics["MA5 / MA20"].set_value(
            f"{_value(latest_value(indicators.ma[5]))} / {_value(latest_value(indicators.ma[20]))}"
        )
        self.metrics["MACD"].set_value(_value(latest_value(indicators.macd)))
        self.metrics["RSI6"].set_value(_value(latest_value(indicators.rsi[6])))
        self.metrics["ATR20"].set_value(_value(latest_value(indicators.atr[20])))
        self.metrics["WR10"].set_value(_value(latest_value(indicators.wr[10])))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.credentials, self.settings, self)
        if dialog.exec():
            self._skill_workspace = self._load_skill_workspace()
            self._refresh_advisor_runtime_options()
            self._refresh_ai_status()
            self._update_source_notice()

    def _load_skill_workspace(self) -> SkillWorkspace:
        self._skill_workspace_warning = ""
        include_builtin = bool(self.settings.value("skills/include_builtin", True, type=bool))
        configured_root = str(self.settings.value("skills/user_root", "", type=str)).strip()
        user_skill_root = (
            Path(configured_root).expanduser()
            if configured_root
            else self.portfolio_repository.path.parent / "skills"
        )
        try:
            return SkillWorkspace(
                SkillWorkspacePolicy(
                    include_builtin=include_builtin,
                    user_roots=(user_skill_root,),
                )
            )
        except SkillValidationError as error:
            self._skill_workspace_warning = str(error)
            return SkillWorkspace(
                SkillWorkspacePolicy(include_builtin=include_builtin, user_roots=())
            )

    def _refresh_skill_workspace(self) -> tuple[object, ...]:
        self._skill_workspace = self._load_skill_workspace()
        self._refresh_advisor_runtime_options()
        return tuple(self._skill_workspace.list())

    def open_skill_manager(self) -> None:
        user_root = (
            self._skill_workspace.policy.user_roots[0]
            if self._skill_workspace.policy.user_roots
            else self.portfolio_repository.path.parent / "skills"
        )
        dialog = SkillManagerDialog(
            self._skill_workspace.list(),
            user_root,
            refresh=self._refresh_skill_workspace,
            parent=self,
        )
        dialog.exec()

    def _default_investment_agent_workspace(self) -> Path:
        return self.portfolio_repository.path.parent / "investment-agent-workspaces"

    def _investment_agent_workspace(self) -> Path:
        configured = str(self.settings.value("agent/workspace_root", "", type=str)).strip()
        return (
            Path(configured).expanduser()
            if configured
            else self._default_investment_agent_workspace()
        )

    def _refresh_advisor_runtime_options(self) -> None:
        self.advisor_widget.set_available_skills(self._skill_workspace.list())
        root = self._investment_agent_workspace()
        self.advisor_widget.set_workspace_root(
            str(root),
            is_default=root == self._default_investment_agent_workspace(),
        )

    def _choose_investment_agent_workspace(self) -> None:
        current = self._investment_agent_workspace()
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择求衡投研助手工作区",
            str(current.parent if not current.exists() else current),
        )
        if not selected:
            return
        root = Path(selected).expanduser().resolve()
        if not root.is_dir():
            QMessageBox.warning(self, "工作区不可用", "请选择一个已存在的文件夹。")
            return
        self.settings.setValue("agent/workspace_root", str(root))
        self.advisor_widget.set_workspace_root(str(root), is_default=False)
        self.statusBar().showMessage(f"求衡投研助手工作区已切换：{root}", 8000)

    def _reset_investment_agent_workspace(self) -> None:
        self.settings.remove("agent/workspace_root")
        root = self._default_investment_agent_workspace()
        self.advisor_widget.set_workspace_root(str(root), is_default=True)
        self.statusBar().showMessage("求衡投研助手已恢复默认工作区", 8000)

    def open_portfolio(self) -> None:
        try:
            PortfolioDialog(self.portfolio_repository, self).exec()
        except ValueError as error:
            QMessageBox.critical(self, "无法读取本地持仓", str(error))
        self._reload_advisor_positions()

    def open_advisor(self) -> None:
        self._reload_advisor_positions()
        self.workspace_tabs.setCurrentWidget(self.advisor_widget)

    def _workspace_tab_changed(self, _: int) -> None:
        if self.workspace_tabs.currentWidget() is self.advisor_widget:
            self._reload_advisor_positions()

    def _reload_advisor_positions(self, preferred_symbol: str = "") -> None:
        try:
            book = self.portfolio_repository.load()
        except ValueError as error:
            self.advisor_widget.set_positions(())
            self.advisor_widget.set_evidence(None, f"无法读取本地持仓：{error}")
            return
        preferred = preferred_symbol or self.advisor_widget.selected_symbol
        self.advisor_widget.set_positions(book.positions, preferred)
        self._advisor_position_changed(self.advisor_widget.selected_symbol)

    def _advisor_position_changed(self, symbol: str) -> None:
        result = self._last_result
        if not symbol:
            self.advisor_widget.set_evidence(None, "尚未登记持仓；仍可讨论策略或发起候选筛选")
            self._restore_advisor_conversation(symbol)
            return
        if result is None or result.data.symbol != symbol:
            self.advisor_widget.set_evidence(
                None,
                f"{symbol} 尚无当前分析证据；可点击“分析该持仓”，或继续讨论策略与筛选",
            )
            self._restore_advisor_conversation(symbol)
            return
        try:
            position = self.portfolio_repository.load().position(symbol)
            if position is None:
                raise ValueError("所选证券不在本地持仓中")
            evidence = build_advisor_evidence(result, position)
        except ValueError as error:
            self.advisor_widget.set_evidence(None, str(error))
            self._restore_advisor_conversation(symbol)
            return
        self.advisor_widget.set_evidence(evidence)
        self._restore_advisor_conversation(symbol)

    def _advisor_thread_id(self, symbol: str | None = None) -> str:
        selected = symbol or self.advisor_widget.selected_symbol or "general"
        return f"investment-{selected.lower()}"

    def _restore_advisor_conversation(self, symbol: str) -> None:
        try:
            state = self._conversation_store.load_thread(self._advisor_thread_id(symbol))
        except ValueError:
            self.advisor_widget.set_turns(
                (
                    AdvisorTurn(
                        "assistant",
                        "本地对话记录无法读取。为避免覆盖原文件，本次没有自动修复；"
                        "请点击“清空对话”确认重置当前线程。长期投资偏好不会被删除。",
                    ),
                )
            )
            self.statusBar().showMessage("求衡投研助手 本地对话记录损坏，请清空当前对话后重试")
            return
        self.advisor_widget.set_turns(
            tuple(AdvisorTurn(turn.role, turn.content) for turn in state.turns)
        )

    def _confirm_clear_advisor_conversation(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认清空当前对话",
            "将删除当前证券线程的本地对话记录，且无法撤销。"
            "长期保存的风险偏好、投资周期和策略偏好不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self._conversation_store.clear_thread(self._advisor_thread_id())
            except OSError as error:
                QMessageBox.critical(
                    self,
                    "无法清空当前对话",
                    f"本地对话记录删除失败，请检查用户数据目录权限：{error}",
                )
                return
            self.advisor_widget.clear_conversation(notify=False)
            self.statusBar().showMessage("求衡投研助手 当前对话已清空；长期投资偏好已保留", 8000)

    def _clear_advisor_conversation(self) -> None:
        self._conversation_store.clear_thread(self._advisor_thread_id())

    def _record_advisor_answer(self, answer: AdvisorAnswer) -> bool:
        self.advisor_widget.append_answer(answer)
        try:
            self._conversation_store.append(self._advisor_thread_id(), "assistant", answer.text)
        except (OSError, ValueError):
            self.statusBar().showMessage(
                "求衡投研助手 回答已显示，但本地对话记录保存失败；请检查用户数据目录",
                12_000,
            )
            return False
        return True

    def _local_agent_answer(self, text: str, *, warning: str | None = None) -> None:
        self._record_advisor_answer(AdvisorAnswer(text, "local", warning))

    def _analyze_advisor_position(self, symbol: str) -> None:
        if not symbol:
            return
        self.workspace_tabs.setCurrentWidget(self.research_page)
        self.symbol_input.setText(symbol)
        self.start_analysis()

    def _submit_advisor_question(self, raw_question: str) -> None:
        try:
            selection_prompt = raw_question
            selected_skill = self.advisor_widget.selected_skill_name
            if selected_skill and not raw_question.strip().startswith("/"):
                selection_prompt = f"/{selected_skill} {raw_question}"
            selection = self._skill_workspace.select_for_turn(
                selection_prompt,
                defaults=("investment-decision-engine",),
            )
            question = validate_advisor_question(selection.prompt)
            routed = self._investment_router.route(question)
        except (ValueError, SkillValidationError) as error:
            QMessageBox.warning(self, "问题无法发送", str(error))
            return
        evidence = self.advisor_widget.evidence
        history = self.advisor_widget.turns
        thread_id = self._advisor_thread_id()
        try:
            previous_state = self._conversation_store.load_thread(thread_id)
            self._conversation_store.load_memory()
            self._conversation_store.append(thread_id, "user", question, intent=routed.intent)
            memory = self._conversation_store.load_memory()
        except (OSError, ValueError):
            QMessageBox.warning(
                self,
                "无法保存求衡投研助手 对话",
                "本地对话或长期偏好文件无法读取/写入。为避免覆盖数据，本轮没有执行；"
                "如为当前线程损坏，请先点击“清空对话”确认重置。",
            )
            return
        self.advisor_widget.append_user(question)
        if routed.intent == "manage_skills":
            skills = self._skill_workspace.list()
            skill_root = (
                str(self._skill_workspace.policy.user_roots[0])
                if self._skill_workspace.policy.user_roots
                else "当前配置的用户 Skill 目录"
            )
            lines = [f"- `{item.name}` · {item.provider} · {item.description}" for item in skills]
            warning = (
                f"用户 Skill 目录读取失败，已按当前内置开关降级：{self._skill_workspace_warning}"
                if self._skill_workspace_warning
                else None
            )
            self._local_agent_answer(
                "## 当前可用 Skill\n\n"
                + ("\n".join(lines) if lines else "当前工作区没有启用任何 Skill。")
                + f"\n\n用户 Skill 放入 `{skill_root}/"
                "<name>/SKILL.md` 后，可用 "
                "`/name 问题` 显式调用；同名用户 Skill 会覆盖内置版本。关闭内置 Skill 后"
                "也可只使用自己的。",
                warning=warning,
            )
            return
        if routed.intent == "screen_candidates":
            self._start_general_investment_chat(
                question,
                history,
                routed.intent,
                previous_state.summary,
                memory,
                selection.packages,
                skill_selection_mode="explicit" if selection.explicit else "auto",
            )
            return
        if (
            routed.intent == "analyze_security"
            and routed.symbol
            and (evidence is None or evidence.symbol != routed.symbol)
        ):
            self._start_general_investment_chat(
                question,
                history,
                routed.intent,
                previous_state.summary,
                memory,
                selection.packages,
                symbol=routed.symbol,
                skill_selection_mode="explicit" if selection.explicit else "auto",
            )
            return
        if routed.intent in {"design_strategy", "general_research"}:
            self._start_general_investment_chat(
                question,
                history,
                routed.intent,
                previous_state.summary,
                memory,
                selection.packages,
                skill_selection_mode="explicit" if selection.explicit else "auto",
            )
            return
        if evidence is None:
            self._local_agent_answer(
                "当前问题需要具体证券研究证据。请直接输入股票代码（如 `600519.SH`）开始分析，"
                "或先使用“扫描我的候选池”。"
            )
            return
        self._start_general_investment_chat(
            question,
            history,
            routed.intent,
            previous_state.summary,
            memory,
            selection.packages,
            symbol=evidence.symbol,
            skill_selection_mode="explicit" if selection.explicit else "auto",
        )

    def _start_general_investment_chat(
        self,
        question: str,
        history: tuple[AdvisorTurn, ...],
        intent: InvestmentIntent,
        summary: str,
        memory: InvestmentMemory,
        active_skills: tuple[SkillPackage, ...],
        *,
        symbol: str | None = None,
        skill_selection_mode: SkillSelectionMode = "auto",
    ) -> None:
        use_ai = bool(self.settings.value("research/use_ai", True, type=bool))
        api_key = self.credentials.get_deepseek_api_key() if use_ai else None
        try:
            book = self.portfolio_repository.load()
        except ValueError as error:
            self._local_agent_answer(f"无法读取本地持仓与自选池：{error}")
            return
        if intent == "screen_candidates" and not book.symbols():
            self._local_agent_answer(
                "## 候选池筛选未完成\n\n"
                "本地候选池为空。请先在“持仓与自选”中添加标的，再重新发起筛选。"
            )
            return
        tushare_token = self.credentials.get_tushare_token()
        source = self._resolve_data_source(str(self.source_combo.currentData()), tushare_token)
        if source is None:
            self._local_agent_answer("本轮 Agent 未启动：请先选择可用行情源。")
            return
        start = date(
            self.start_date.date().year(),
            self.start_date.date().month(),
            self.start_date.date().day(),
        )
        end = date(
            self.end_date.date().year(),
            self.end_date.date().month(),
            self.end_date.date().day(),
        )
        task = InvestmentAgentTask(
            InvestmentAgentTaskRequest(
                run=InvestmentAgentRunRequest(
                    question=question,
                    intent=intent,
                    thread_id=self._advisor_thread_id(),
                    portfolio=book,
                    source=source,
                    start_date=start,
                    end_date=end,
                    adjustment=AdjustmentMode(str(self.adjustment_combo.currentData())),
                    tushare_token=tushare_token,
                    web_search_api_key=self.credentials.get_tavily_api_key(),
                    symbol=symbol,
                    evidence=self.advisor_widget.evidence,
                    memory=memory,
                    conversation_summary=summary,
                    active_skills=active_skills,
                    skill_selection_mode=skill_selection_mode,
                ),
                workspace_root=str(self._investment_agent_workspace()),
                skills=self._skill_workspace,
                deepseek_api_key=api_key,
                deepseek_model=self._deepseek_model(),
            )
        )
        self._advisor_tasks.add(task)
        task.signals.progress.connect(self.advisor_widget.update_agent_progress)
        task.signals.succeeded.connect(
            lambda result, current=task: self._investment_agent_succeeded(current, result)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._advisor_failed(current, message)
        )
        self.advisor_widget.set_busy(True)
        mode = "DeepSeek 动态规划" if api_key else "本地确定性规划"
        self.statusBar().showMessage(f"求衡投研助手 正在运行 · {mode}")
        self.thread_pool.start(task)

    def _investment_agent_succeeded(self, task: InvestmentAgentTask, value: object) -> None:
        self._advisor_tasks.discard(task)
        if not isinstance(value, InvestmentAgentRunResult):
            self._advisor_failed(task, "求衡投研助手 返回了未知运行结果")
            return
        self.advisor_widget.finish_agent_run(value)
        answer = AdvisorAnswer(value.answer, value.answer_mode, value.warning)
        persisted = self._record_advisor_answer(answer)
        if persisted:
            self.statusBar().showMessage(
                f"求衡投研助手 运行完成 · {len(value.tool_calls)} 个工具步骤 · "
                f"{len(value.artifacts)} 个成果",
                10_000,
            )

    def _start_legacy_general_chat(
        self,
        question: str,
        history: tuple[AdvisorTurn, ...],
        intent: InvestmentIntent,
        summary: str,
        memory: InvestmentMemory,
        active_skills: tuple[SkillPackage, ...],
    ) -> None:
        """Compatibility helper retained for direct unit coverage of the chat adapter."""

        task = InvestmentChatTask(
            InvestmentChatRequest(
                history=history,
                question=question,
                intent=intent,
                deepseek_api_key=self.credentials.get_deepseek_api_key(),
                deepseek_model=self._deepseek_model(),
                conversation=AdvisorConversationContext(summary, memory, active_skills),
            )
        )
        self._advisor_tasks.add(task)
        task.signals.succeeded.connect(
            lambda answer, current=task: self._general_advisor_succeeded(current, answer)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._advisor_failed(current, message)
        )
        self.advisor_widget.set_busy(True)
        self.statusBar().showMessage("求衡投研助手 正在执行兼容对话…")
        self.thread_pool.start(task)

    def _general_advisor_succeeded(self, task: InvestmentChatTask, value: object) -> None:
        self._advisor_tasks.discard(task)
        if not isinstance(value, AdvisorAnswer):
            self._advisor_failed(task, "求衡投研助手 返回了未知结果")
            return
        persisted = self._record_advisor_answer(value)
        mode = "DeepSeek 研究对话" if value.mode == "deepseek" else "本地安全回退"
        if persisted:
            self.statusBar().showMessage(f"求衡投研助手 回答完成 · {mode}", 8000)

    def _advisor_succeeded(self, task: AdvisorChatTask, value: object) -> None:
        self._advisor_tasks.discard(task)
        if self.advisor_widget.evidence != task.request.evidence:
            self.advisor_widget.set_busy(False)
            self.statusBar().showMessage("求衡投研助手 已丢弃过期回答，请按当前分析重新提问", 8000)
            return
        if not isinstance(value, AdvisorAnswer):
            self._advisor_failed(task, "求衡投研助手 返回了未知结果")
            return
        persisted = self._record_advisor_answer(value)
        mode = "DeepSeek 证据解释" if value.mode == "deepseek" else "本地规则回退"
        if persisted:
            self.statusBar().showMessage(f"求衡投研助手 回答完成 · {mode}", 8000)

    def _advisor_failed(
        self,
        task: AdvisorChatTask | InvestmentChatTask | InvestmentAgentTask,
        message: str,
    ) -> None:
        self._advisor_tasks.discard(task)
        self.advisor_widget.append_error(message)
        try:
            self._conversation_store.append(
                self._advisor_thread_id(), "assistant", f"本次回答失败：{message}"
            )
        except (OSError, ValueError):
            self.statusBar().showMessage("求衡投研助手 回答失败，且本地错误记录保存失败")
        else:
            self.statusBar().showMessage("求衡投研助手 回答失败")

    def _choose_missing_tushare_fallback(self) -> str | None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle("Tushare 需要独立 Token")
        dialog.setText("DeepSeek API Key 只用于 AI 文字整理，不能代替 Tushare Token 获取行情。")
        dialog.setInformativeText("请选择本次分析使用的数据来源：")
        public_button = dialog.addButton(
            "使用公开历史数据（推荐）", QMessageBox.ButtonRole.AcceptRole
        )
        demo_button = dialog.addButton("使用离线模拟数据", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton("取消并返回设置", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is public_button:
            return "baostock"
        if clicked is demo_button:
            return "demo"
        return None

    def _resolve_data_source(self, source: str, tushare_token: str | None) -> str | None:
        if source != "tushare" or tushare_token:
            return source
        fallback = self._choose_missing_tushare_fallback()
        if fallback is None:
            return None
        index = self.source_combo.findData(fallback)
        self.source_combo.setCurrentIndex(index)
        self.settings.setValue("research/source", fallback)
        self._update_source_notice()
        return fallback

    def start_analysis(self) -> None:
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            QMessageBox.warning(self, "缺少股票代码", "请输入 A 股股票代码。")
            return
        start = date(
            self.start_date.date().year(),
            self.start_date.date().month(),
            self.start_date.date().day(),
        )
        end = date(
            self.end_date.date().year(),
            self.end_date.date().month(),
            self.end_date.date().day(),
        )
        if start >= end:
            QMessageBox.warning(self, "日期范围无效", "开始日期必须早于结束日期。")
            return
        tushare_token = self.credentials.get_tushare_token()
        source = self._resolve_data_source(str(self.source_combo.currentData()), tushare_token)
        if source is None:
            return
        try:
            book = self.portfolio_repository.load()
            position = book.position(symbol)
        except ValueError as error:
            QMessageBox.critical(self, "无法读取本地持仓", str(error))
            return
        watch = next((item for item in book.watchlist if item.symbol == symbol), None)
        industry = position.industry if position is not None else watch.industry if watch else ""
        self.settings.setValue("research/source", source)
        self.settings.setValue("research/symbol", symbol)
        request = ResearchRequest(
            source=source,
            symbol=symbol,
            start_date=start,
            end_date=end,
            adjustment=AdjustmentMode(str(self.adjustment_combo.currentData())),
            tushare_token=tushare_token,
            deepseek_api_key=self.credentials.get_deepseek_api_key(),
            use_ai=bool(self.settings.value("research/use_ai", True, type=bool)),
            position=position,
            industry=industry,
            deepseek_model=self._deepseek_model(),
        )
        self._last_request = request
        self.market_context_widget.reset_sector()
        self.market_context_widget.suggest_industry(industry)
        self.market_context_widget.confluence_label.setText("正在加载个股与大盘…")
        task = ResearchTask(request)
        self._tasks.add(task)
        task.signals.succeeded.connect(
            lambda result, current=task: self._analysis_succeeded(current, result)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._analysis_failed(current, message)
        )
        task.signals.progress.connect(self._render_plan_progress)
        self.analyze_button.setEnabled(False)
        self.analyze_button.setText("分析中…")
        self.statusBar().showMessage("正在获取日线并计算指标…")
        self.thread_pool.start(task)

    def start_sector_analysis(self, value: object) -> None:
        if not isinstance(value, ContextInstrument):
            self.market_context_widget.show_error("板块趋势代理无效，请重新选择。")
            return
        if self._last_request is None or self._last_result is None:
            self.market_context_widget.show_error("请先完成一次个股分析，再加载板块指标。")
            return
        request = SectorContextRequest(
            source=self._last_request.source,
            stock_symbol=self._last_result.data.symbol,
            stock_as_of=self._last_result.data.as_of,
            instrument=value,
            start_date=self._last_request.start_date,
            end_date=self._last_request.end_date,
            tushare_token=self._last_request.tushare_token,
        )
        task = SectorContextTask(request)
        self._sector_tasks.add(task)
        task.signals.succeeded.connect(
            lambda result, current=task: self._sector_analysis_succeeded(current, result)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._sector_analysis_failed(current, message)
        )
        self.market_context_widget.set_sector_loading(True)
        self.statusBar().showMessage(f"正在加载 {value.name} 板块代理行情与指标…")
        self.thread_pool.start(task)

    def _sector_analysis_succeeded(self, task: SectorContextTask, value: object) -> None:
        self._sector_tasks.discard(task)
        if not self._sector_request_is_current(task):
            if not self._sector_tasks:
                self.market_context_widget.set_sector_loading(False)
            return
        if not isinstance(value, MarketTrendContext) or self._last_result is None:
            self._sector_analysis_failed(task, "板块分析返回了未知结果")
            return
        try:
            assert self._last_request is not None
            position = self._last_request.position
            updated = attach_sector_context(self._last_result, value, position=position)
        except (ValueError, RuntimeError) as error:
            self._sector_analysis_failed(task, str(error))
            return
        self.render_result(updated)
        self.market_context_widget.set_sector_loading(False)
        self.market_context_widget.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(
            f"板块分析完成 · {value.instrument.name} · {updated.market_context.priority_label}",
            12000,
        )

    def _sector_analysis_failed(self, task: SectorContextTask, message: str) -> None:
        self._sector_tasks.discard(task)
        if not self._sector_request_is_current(task):
            if not self._sector_tasks:
                self.market_context_widget.set_sector_loading(False)
            return
        assert self._last_result is not None
        try:
            assert self._last_request is not None
            position = self._last_request.position
            unavailable = MarketTrendContext.unavailable(task.request.instrument, message)
            self.render_result(
                attach_sector_context(self._last_result, unavailable, position=position)
            )
        except (ValueError, RuntimeError):
            pass
        self.market_context_widget.show_error(
            f"板块指标加载失败：{message}。可重试、切换数据源或选择其他板块代理。"
        )
        self.statusBar().showMessage("板块指标加载失败")

    def _sector_request_is_current(self, task: SectorContextTask) -> bool:
        request = task.request
        return bool(
            self._last_request is not None
            and self._last_result is not None
            and self._last_request.source == request.source
            and self._last_request.start_date == request.start_date
            and self._last_request.end_date == request.end_date
            and self._last_result.data.symbol == request.stock_symbol
            and self._last_result.data.as_of == request.stock_as_of
        )

    def start_candidate_screen(
        self,
        _checked: bool = False,
        *,
        skill_package: SkillPackage | None = None,
    ) -> bool:
        try:
            package, strategy = self._candidate_strategy(skill_package)
        except SkillValidationError as error:
            QMessageBox.warning(self, "筛选 Skill 无法执行", str(error))
            return False
        try:
            book = self.portfolio_repository.load()
        except ValueError as error:
            QMessageBox.critical(self, "无法读取本地持仓", str(error))
            return False
        symbols = book.symbols()
        if not symbols:
            QMessageBox.information(
                self,
                "候选池为空",
                "请先在“持仓与自选”中添加至少一个股票代码。",
            )
            return False
        if len(symbols) > 50:
            QMessageBox.warning(self, "候选池过大", "桌面端单次最多扫描 50 只股票。")
            symbols = symbols[:50]
        start = date(
            self.start_date.date().year(),
            self.start_date.date().month(),
            self.start_date.date().day(),
        )
        end = date(
            self.end_date.date().year(),
            self.end_date.date().month(),
            self.end_date.date().day(),
        )
        tushare_token = self.credentials.get_tushare_token()
        source = self._resolve_data_source(str(self.source_combo.currentData()), tushare_token)
        if source is None:
            return False
        industries = {item.symbol: item.industry for item in book.watchlist if item.industry}
        industries.update({item.symbol: item.industry for item in book.positions if item.industry})
        request = CandidateScreenRequest(
            source=source,
            symbols=symbols,
            start_date=start,
            end_date=end,
            adjustment=AdjustmentMode(str(self.adjustment_combo.currentData())),
            tushare_token=tushare_token,
            positions=book.positions,
            industries=tuple(industries.items()),
            strategy=strategy,
            strategy_skill_name=package.summary.name if package is not None else "",
            strategy_skill_provider=package.summary.provider if package is not None else "",
        )
        task = CandidateScreenTask(request)
        self._screen_tasks.add(task)
        task.signals.progress.connect(self._candidate_progress)
        task.signals.succeeded.connect(
            lambda result, current=task: self._candidate_succeeded(current, result)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._candidate_failed(current, message)
        )
        self.screen_button.setEnabled(False)
        self.screen_button.setText("扫描中…")
        self.thread_pool.start(task)
        return True

    def _candidate_strategy(
        self, package: SkillPackage | None
    ) -> tuple[SkillPackage | None, CandidateStrategy | None]:
        selected = package
        if selected is None:
            selection = self._skill_workspace.select_for_turn(
                "扫描候选池",
                defaults=("investment-decision-engine",),
            )
            selected = selection.packages[0] if selection.packages else None
        if selected is None:
            if not self._skill_workspace.policy.include_builtin:
                raise SkillValidationError(
                    "当前已关闭内置 Skill；请用 `/你的-skill 筛选候选池` 显式选择"
                    "包含 strategy.json 的用户 Skill"
                )
            return None, None
        return selected, candidate_strategy_from_skill(selected)

    def _candidate_progress(self, payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("kind") != "candidate-screen":
            return
        self.statusBar().showMessage(
            f"候选池扫描 {payload.get('current')}/{payload.get('total')} · {payload.get('symbol')}"
        )

    def _candidate_succeeded(self, task: CandidateScreenTask, value: object) -> None:
        self._screen_tasks.discard(task)
        self.screen_button.setEnabled(True)
        self.screen_button.setText("扫描候选池")
        if not isinstance(value, CandidateScreenResult):
            self._candidate_failed(task, "候选池任务返回了未知结果")
            return
        book = self.portfolio_repository.load()
        names = {item.symbol: item.name for item in book.positions}
        names.update({item.symbol: item.name for item in book.watchlist})
        industries = {item.symbol: item.industry for item in book.watchlist}
        industries.update({item.symbol: item.industry for item in book.positions})
        advice_by_symbol = {
            result.data.symbol: result.investment_advice for result in value.results
        }
        candidates = rank_strategy_candidates(
            [
                CandidateInput(
                    result.data.symbol,
                    names.get(result.data.symbol, ""),
                    result.strategy,
                    advice_by_symbol[result.data.symbol],
                    industries.get(result.data.symbol, ""),
                )
                for result in value.results
            ],
            task.request.strategy,
        )
        by_symbol = {result.data.symbol: result for result in value.results}
        holdings = tuple(
            by_symbol[position.symbol].holding_assessment
            or assess_holding(
                position,
                by_symbol[position.symbol].snapshot.latest_close,
                by_symbol[position.symbol].strategy,
                advice_by_symbol[position.symbol],
                bars=by_symbol[position.symbol].data.bars,
            )
            for position in book.positions
            if position.symbol in by_symbol
        )
        strategy_label = "平台基础排序"
        if task.request.strategy is not None:
            skill_identity = (
                f"{task.request.strategy_skill_name} ({task.request.strategy_skill_provider})"
                if task.request.strategy_skill_name
                else "未命名 Skill"
            )
            strategy_label = f"{task.request.strategy.name} · {skill_identity}"
        self.statusBar().showMessage(
            f"候选池扫描完成 · {strategy_label} · 成功 {len(value.results)} · "
            f"候选 {len(candidates)} · 失败 {len(value.failures)}",
            12_000,
        )
        CandidateResultsDialog(
            candidates,
            holdings,
            self,
            failures=value.failures,
            strategy_label=strategy_label,
        ).exec()

    def _candidate_failed(self, task: CandidateScreenTask, message: str) -> None:
        self._screen_tasks.discard(task)
        self.screen_button.setEnabled(True)
        self.screen_button.setText("扫描候选池")
        self.statusBar().showMessage("候选池扫描失败")
        QMessageBox.critical(self, "无法完成候选池扫描", message)

    def start_backtest(self) -> None:
        result = self._last_result
        if result is None:
            QMessageBox.information(self, "尚无数据", "请先完成一次股票分析。")
            return
        if len(result.data.bars) <= 521:
            QMessageBox.warning(
                self,
                "历史数据不足",
                "多周期回测至少需要约 520 个交易日作为月线 MACD 预热。",
            )
            return
        available_start = result.data.bars[520].trade_date
        default_start = max(
            available_start,
            result.data.as_of - timedelta(days=365),
        )
        config = BacktestConfigDialog(
            available_start,
            result.data.as_of,
            default_start,
            self,
        )
        if not config.exec():
            return
        request = BacktestRequest(result, config.options)
        task = BacktestTask(request)
        self._backtest_tasks.add(task)
        task.signals.succeeded.connect(
            lambda report, current=task: self._backtest_succeeded(current, report)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._backtest_failed(current, message)
        )
        self.backtest_button.setEnabled(False)
        self.backtest_button.setText("回测中…")
        self.statusBar().showMessage("正在逐交易日执行无未来函数回测…")
        self.thread_pool.start(task)

    def _backtest_succeeded(self, task: BacktestTask, value: object) -> None:
        self._backtest_tasks.discard(task)
        self.backtest_button.setEnabled(True)
        self.backtest_button.setText("策略回测")
        if not isinstance(value, BacktestReport):
            self._backtest_failed(task, "回测任务返回了未知结果")
            return
        self.statusBar().showMessage(
            f"回测完成 · {len(value.signals)} 个信号 · {len(value.trades)} 笔交易",
            12_000,
        )
        BacktestDialog(value, self).exec()

    def _backtest_failed(self, task: BacktestTask, message: str) -> None:
        self._backtest_tasks.discard(task)
        self.backtest_button.setEnabled(self._last_result is not None)
        self.backtest_button.setText("策略回测")
        self.statusBar().showMessage("策略回测失败")
        QMessageBox.critical(self, "无法完成策略回测", message)

    def start_macro_analysis(self) -> None:
        task = MacroTask()
        self._macro_tasks.add(task)
        task.signals.succeeded.connect(
            lambda result, current=task: self._macro_succeeded(current, result)
        )
        task.signals.failed.connect(
            lambda message, current=task: self._macro_failed(current, message)
        )
        self.macro_button.setEnabled(False)
        self.macro_button.setText("官方核验中…")
        self.statusBar().showMessage(
            "正在联网核验国家统计局、人民银行、外汇局和财政部官方发布页…"
        )
        self.thread_pool.start(task)

    def _macro_succeeded(self, task: MacroTask, value: object) -> None:
        self._macro_tasks.discard(task)
        self.macro_button.setEnabled(True)
        self.macro_button.setText("宏观联网核验")
        if not isinstance(value, MacroResearchResult):
            self._macro_failed(task, "宏观任务返回了未知结果")
            return
        self.statusBar().showMessage(
            f"宏观核验完成 · {value.validity.status_label} · "
            f"基线截止 {value.analysis.snapshot.as_of.isoformat()}",
            12_000,
        )
        MacroDialog(value, self).exec()

    def _macro_failed(self, task: MacroTask, message: str) -> None:
        self._macro_tasks.discard(task)
        self.macro_button.setEnabled(True)
        self.macro_button.setText("宏观联网核验")
        self.statusBar().showMessage("宏观分析失败")
        QMessageBox.critical(self, "无法完成宏观分析", message)

    def _analysis_succeeded(self, task: ResearchTask, result: object) -> None:
        self._tasks.discard(task)
        if not isinstance(result, ResearchResult):
            self._analysis_failed(task, "分析任务返回了未知结果")
            return
        self.render_result(result)
        self._finish_task()
        suffix = " · AI 整理已生成" if result.model_summary else " · 已生成规则摘要"
        self.statusBar().showMessage(f"完成 · {result.data.as_of.isoformat()}{suffix}", 12000)

    def _analysis_failed(self, task: ResearchTask, message: str) -> None:
        self._tasks.discard(task)
        self._finish_task()
        self.statusBar().showMessage("分析失败")
        QMessageBox.critical(self, "无法完成分析", message)

    def _finish_task(self) -> None:
        self.analyze_button.setEnabled(True)
        self.analyze_button.setText("开始分析")

    def render_result(self, result: ResearchResult) -> None:
        self._last_result = result
        self.backtest_button.setEnabled(True)
        snapshot = result.snapshot
        self._refresh_chart_view()
        self.market_context_widget.set_context(
            result.market_context,
            Timeframe(str(self.timeframe_combo.currentData())),
        )
        self.decision_summary.set_advice(result.investment_advice, result.strategy)
        self.summary_tabs.setCurrentWidget(self.decision_summary)
        self._set_markdown(self.fact_summary, result.deterministic_summary)
        strategy_text = build_signal_summary(result.strategy)
        context = result.market_context
        benchmark_direction = (
            context.benchmark.strategy.direction_label
            if context.benchmark.strategy is not None
            else f"不可用（{context.benchmark.error or '数据不足'}）"
        )
        sector_direction = "尚未按需加载"
        if context.sector is not None:
            sector_direction = (
                context.sector.strategy.direction_label
                if context.sector.strategy is not None
                else f"不可用（{context.sector.error or '数据不足'}）"
            )
        strategy_text += (
            "\n\n市场共振过滤\n"
            f"- 大盘：{context.benchmark.instrument.name} "
            f"({context.benchmark.instrument.symbol}) · {benchmark_direction}\n"
            f"- 板块：{context.sector.instrument.name if context.sector else '未选择'} · "
            f"{sector_direction}\n"
            f"- 同步结论：{context.status_label}\n"
            f"- 候选优先级：{context.priority_label}\n"
            f"- 规则：买入/加仓受大盘和已加载板块门控；卖出/减仓不被正向环境覆盖。"
        )
        try:
            position = self.portfolio_repository.load().position(result.data.symbol)
        except ValueError as error:
            strategy_text += f"\n\n本地持仓读取失败：{error}"
        else:
            if position is not None:
                assessment = result.holding_assessment or assess_holding(
                    position,
                    snapshot.latest_close,
                    result.strategy,
                    result.investment_advice,
                    bars=result.data.bars,
                )
                holding_days = (
                    str(assessment.holding_days) if assessment.holding_days is not None else "未知"
                )
                peak_close = (
                    f"{assessment.peak_close_since_entry:.4f}"
                    if assessment.peak_close_since_entry is not None
                    else "数据不足"
                )
                peak_drawdown = (
                    f"{assessment.drawdown_from_peak_pct:.2f}%"
                    if assessment.drawdown_from_peak_pct is not None
                    else "数据不足"
                )
                strategy_text += (
                    "\n\n本地持仓状态\n"
                    f"- 行业标签：{position.industry or '未填写（不做宏观行业调整）'}\n"
                    f"- 数量：{position.quantity:.2f}；单位成本：{position.cost_price:.4f}\n"
                    f"- 浮动盈亏：{assessment.unrealized_pnl:.2f}；"
                    f"收益率：{assessment.unrealized_return_pct:.2f}%\n"
                    f"- 持仓天数：{holding_days}；建仓后峰值：{peak_close}；"
                    f"峰值回撤：{peak_drawdown}\n"
                    f"- 技术状态：{assessment.status_label}\n"
                    f"- 持仓建议：{assessment.recommended_action_label}"
                    f"（规则置信度 {assessment.confidence}/100）\n"
                    f"- 退出优先级：{assessment.exit_priority_label}；"
                    f"下一触发：{assessment.next_trigger}\n"
                    f"- 宏观行业：{assessment.macro_stance_label or '未映射'}；"
                    f"置信度调整 {assessment.macro_confidence_adjustment:+d}\n"
                    f"- 失效条件：{assessment.invalidation_condition}\n"
                    "- 持仓数量、成本、日期和备注只在本机参与计算，未发送给 DeepSeek。"
                )
            else:
                strategy_text += "\n\n当前证券未登记为本地持仓。"
        self._set_markdown(self.strategy_summary, strategy_text)
        if result.model_summary:
            self._set_markdown(self.ai_summary, result.model_summary)
        elif result.model_warning:
            self._set_markdown(
                self.ai_summary,
                f"本次未采用 AI 整理。\n\n{result.model_warning}\n\n规则化事实摘要仍可正常使用。",
            )
        else:
            self._set_markdown(
                self.ai_summary,
                "本次未启用 DeepSeek。事实摘要由本地确定性规则生成，完整功能不依赖模型。",
            )
        if result.data.is_synthetic:
            self.source_notice.setText(
                "当前展示离线模拟数据（非真实行情）。不得据此判断任何真实证券的历史或未来表现。"
            )
            self.source_notice.setProperty("synthetic", True)
            self.source_notice.style().unpolish(self.source_notice)
            self.source_notice.style().polish(self.source_notice)
        else:
            cache_label = {
                "disabled": "本地缓存未启用",
                "hit": f"缓存完整命中 {result.data.cache_hit_bars} 根，未请求上游",
                "miss": f"首次缓存，新增 {result.data.cache_added_bars} 根",
                "partial": (
                    f"缓存命中 {result.data.cache_hit_bars} 根，增量补齐 "
                    f"{result.data.cache_added_bars} 根"
                ),
                "rebuilt": (
                    f"检测到同源复权历史变化，已重建该序列 {result.data.cache_added_bars} 根"
                ),
            }.get(result.data.cache_status, result.data.cache_status)
            self.source_notice.setText(
                f"真实历史行情 · 来源 {result.data.source} · 股票 {result.data.symbol} · "
                f"{result.data.adjustment.label}；{cache_label}。周/月图由该同源日 K 本地聚合，"
                f"大盘 {result.market_context.benchmark.instrument.symbol} 固定不复权并按同源隔离；"
                "板块代理按需加载，不同来源和复权口径不会混用。"
            )
            self.source_notice.setProperty("synthetic", False)
            self.source_notice.style().unpolish(self.source_notice)
            self.source_notice.style().polish(self.source_notice)
        self._reload_advisor_positions(result.data.symbol)
        if result.plan:
            self._render_plan_progress(result.plan)

    def _render_plan_progress(self, plan: object) -> None:
        if not isinstance(plan, dict):
            return
        tasks = plan.get("tasks", [])
        if not isinstance(tasks, list):
            return
        symbols = {
            "pending": "○",
            "running": "●",
            "waiting_approval": "◆",
            "succeeded": "✓",
            "failed": "×",
            "unknown_outcome": "!",
            "superseded": "↻",
            "skipped": "—",
            "cancelled": "—",
        }
        lines = [
            f"目标：{plan.get('goal', '')}",
            f"计划状态：{plan.get('status', '')} · 版本 {plan.get('version', '')}",
            "",
        ]
        for task in tasks:
            if not isinstance(task, dict):
                continue
            status = str(task.get("status", "pending"))
            summary = f" · {task['summary']}" if task.get("summary") else ""
            agent = f" · {task['agent']}" if task.get("agent") else ""
            lines.append(
                f"{symbols.get(status, '○')} {task.get('title', '')} [{status}]{agent}{summary}"
            )
            skills = task.get("skills", [])
            if isinstance(skills, list) and skills:
                lines.append(f"    Skills: {', '.join(str(skill) for skill in skills)}")
        runtime = plan.get("context", {})
        if isinstance(runtime, dict):
            agent_runtime = runtime.get("agent_runtime", {})
            if isinstance(agent_runtime, dict) and agent_runtime:
                lines.extend(
                    (
                        "",
                        f"Agent Runtime：{agent_runtime.get('mode', '')} · "
                        f"委派 {agent_runtime.get('delegations', 0)} 次",
                        "安全边界：本地受控执行；Skill 不自动执行脚本",
                    )
                )
        if self._last_result and self._last_result.workspace:
            lines.extend(("", f"隔离工作区：{self._last_result.workspace}"))
        self._set_markdown(self.plan_summary, "\n".join(lines))

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于 EquiSeek 求衡智能投研平台",
            "这是开源、本地优先的规则型投资决策研究工具。\n\n"
            "指标：MA、MACD、KDJ、RSI、ATR、BOLL、WR。\n"
            "策略：月/周/日 MACD 判断大方向，WR 决定买卖时机，"
            "并结合默认大盘、按需行业指数判断三层共振，输出方向预测、ATR 风险区间和失效条件。\n"
            "组合：持仓和自选仅保存在本机，可给出持有、加仓、减仓、卖出和候选排序。\n"
            "宏观：以资本流量/流向/流速和成本转嫁链生成行业偏配/低配建议。\n"
            "数据：BaoStock、用户自有 Tushare、离线模拟。\n"
            "模型：可选 DeepSeek V4 Flash / Pro，只解释本地规则结论，不改变动作。\n\n"
            "规则建议不保证收益；软件不连接券商、不自动下单。公开提供个性化投顾服务仍应遵守适用资质与监管要求。",
        )
