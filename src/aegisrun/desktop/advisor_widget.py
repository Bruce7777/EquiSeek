from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aegisrun.agents.investment_runtime import InvestmentAgentRunResult
from aegisrun.portfolio.models import Position
from aegisrun.research.advisor_chat import AdvisorAnswer, AdvisorEvidence, AdvisorTurn


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!>|])", r"\\\1", value)


class AdvisorQuestionEdit(QPlainTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            event.accept()
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class HoldingAdvisorWidget(QWidget):
    question_submitted = Signal(str)
    analysis_requested = Signal(str)
    position_changed = Signal(str)
    clear_requested = Signal()
    conversation_cleared = Signal()
    workspace_choose_requested = Signal()
    workspace_reset_requested = Signal()
    skill_manager_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("holdingAdvisor")
        self.setAccessibleName("对话式投资研究 Agent 页面")
        self._evidence: AdvisorEvidence | None = None
        self._turns: list[AdvisorTurn] = []
        self._busy = False
        self._workspace_path = ""
        self._workspace_root = ""
        self._run_steps: dict[int, QListWidgetItem] = {}
        self._trace_payloads: list[dict[str, object]] = []
        self._build_ui()

    @property
    def evidence(self) -> AdvisorEvidence | None:
        return self._evidence

    @property
    def turns(self) -> tuple[AdvisorTurn, ...]:
        return tuple(self._turns)

    @property
    def selected_symbol(self) -> str:
        return str(self.position_combo.currentData() or "")

    @property
    def selected_skill_name(self) -> str:
        return str(self.skill_combo.currentData() or "")

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def _build_ui(self) -> None:
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("advisorTopBar")
        header = QHBoxLayout(top_bar)
        header.setContentsMargins(24, 14, 20, 12)
        header.setSpacing(10)
        title = QLabel("求衡投研助手")
        title.setObjectName("advisorPageTitle")
        header.addWidget(title)
        subtitle = QLabel("可追溯的策略研究工作台")
        subtitle.setObjectName("advisorPageSubtitle")
        header.addWidget(subtitle)
        header.addStretch()
        local_badge = QLabel("本地开源 · 无需登录")
        local_badge.setObjectName("advisorLocalBadge")
        header.addWidget(local_badge)
        self.mode_badge = QLabel("就绪")
        self.mode_badge.setObjectName("advisorModeBadge")
        header.addWidget(self.mode_badge)
        self.details_button = QPushButton("隐藏详情")
        self.details_button.setObjectName("advisorQuietButton")
        self.details_button.setAccessibleName("显示或隐藏求衡投研助手 执行详情")
        self.details_button.setToolTip("显示或隐藏右侧执行详情（Ctrl+Shift+D）")
        self.details_button.setShortcut("Ctrl+Shift+D")
        self.details_button.clicked.connect(self.toggle_run_panel)
        header.addWidget(self.details_button)
        self.copy_answer_button = QPushButton("复制回答")
        self.copy_answer_button.setObjectName("advisorQuietButton")
        self.copy_answer_button.setAccessibleName("复制求衡投研助手 最后回答")
        self.copy_answer_button.clicked.connect(self._copy_latest_answer)
        header.addWidget(self.copy_answer_button)
        self.clear_button = QPushButton("新任务")
        self.clear_button.setObjectName("advisorQuietButton")
        self.clear_button.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        header.addWidget(self.clear_button)
        page.addWidget(top_bar)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("advisorSplitter")
        self.body_splitter.setChildrenCollapsible(False)

        conversation = QFrame()
        conversation.setObjectName("advisorConversation")
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(22, 14, 14, 18)
        conversation_layout.setSpacing(12)

        context = QFrame()
        context.setObjectName("advisorContextBar")
        context_layout = QHBoxLayout(context)
        context_layout.setContentsMargins(12, 8, 10, 8)
        context_layout.setSpacing(8)
        context_caption = QLabel("研究对象")
        context_caption.setObjectName("advisorFieldCaption")
        context_layout.addWidget(context_caption)
        self.position_combo = QComboBox()
        self.position_combo.setObjectName("advisorPositionCombo")
        self.position_combo.setAccessibleName("求衡投研助手 证券选择")
        self.position_combo.setMinimumWidth(190)
        self.position_combo.currentIndexChanged.connect(self._emit_position_changed)
        context_layout.addWidget(self.position_combo)
        self.context_label = QLabel("通用研究线程")
        self.context_label.setObjectName("advisorContextStatus")
        self.context_label.setWordWrap(True)
        context_layout.addWidget(self.context_label, 1)
        self.analyze_button = QPushButton("更新证据")
        self.analyze_button.setObjectName("advisorSecondaryButton")
        self.analyze_button.clicked.connect(self._request_analysis)
        context_layout.addWidget(self.analyze_button)
        conversation_layout.addWidget(context)

        self.conversation_stack = QStackedWidget()
        self.conversation_stack.setObjectName("advisorConversationStack")
        self.welcome_page = self._build_welcome_page()
        self.conversation_stack.addWidget(self.welcome_page)
        self.transcript = QTextBrowser()
        self.transcript.setObjectName("advisorTranscript")
        self.transcript.setAccessibleName("求衡投研助手 对话记录")
        self.transcript.setOpenExternalLinks(True)
        self.conversation_stack.addWidget(self.transcript)
        conversation_layout.addWidget(self.conversation_stack, 1)

        composer = QFrame()
        composer.setObjectName("advisorComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(13, 11, 11, 9)
        composer_layout.setSpacing(7)
        self.question_input = AdvisorQuestionEdit()
        self.question_input.setObjectName("advisorQuestionInput")
        self.question_input.setAccessibleName("输入求衡投研助手 研究目标")
        self.question_input.setPlaceholderText(
            "描述研究目标，Agent 会展示所用 Skill、工具、证据和规则门控…"
        )
        self.question_input.submit_requested.connect(self.submit)
        self.question_input.setMaximumHeight(92)
        composer_layout.addWidget(self.question_input)

        composer_footer = QHBoxLayout()
        composer_footer.setSpacing(7)
        self.skill_combo = QComboBox()
        self.skill_combo.setObjectName("advisorSkillCombo")
        self.skill_combo.setAccessibleName("选择本轮投资 Skill")
        self.skill_combo.setMinimumWidth(180)
        self.skill_combo.setToolTip("自动选择，或明确指定一个内置/用户 Skill")
        self.skill_combo.addItem("自动选择 Skill", "")
        self.skill_combo.currentIndexChanged.connect(self._refresh_composer_scope)
        composer_footer.addWidget(self.skill_combo)

        self.workspace_button = QPushButton("工作区 · 默认")
        self.workspace_button.setObjectName("advisorWorkspaceButton")
        self.workspace_button.setAccessibleName("选择求衡投研助手 工作区")
        workspace_menu = QMenu(self.workspace_button)
        choose_action = QAction("选择工作区…", self)
        choose_action.triggered.connect(
            lambda _checked=False: self.workspace_choose_requested.emit()
        )
        workspace_menu.addAction(choose_action)
        reset_action = QAction("恢复默认工作区", self)
        reset_action.triggered.connect(lambda _checked=False: self.workspace_reset_requested.emit())
        workspace_menu.addAction(reset_action)
        workspace_menu.addSeparator()
        open_root_action = QAction("在 Finder 中打开", self)
        open_root_action.triggered.connect(self._open_workspace_root)
        workspace_menu.addAction(open_root_action)
        self.workspace_button.setMenu(workspace_menu)
        composer_footer.addWidget(self.workspace_button)
        self.composer_scope = QLabel("Skill 与工作区可在每轮发送前调整")
        self.composer_scope.setObjectName("advisorComposerHint")
        self.composer_scope.setWordWrap(True)
        composer_footer.addWidget(self.composer_scope, 1)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("advisorSendButton")
        self.send_button.clicked.connect(self.submit)
        composer_footer.addWidget(self.send_button)
        composer_layout.addLayout(composer_footer)
        conversation_layout.addWidget(composer)
        self.body_splitter.addWidget(conversation)

        self.run_panel = self._build_run_panel()
        self.body_splitter.addWidget(self.run_panel)
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setStretchFactor(1, 0)
        self.body_splitter.setSizes([930, 360])
        page.addWidget(self.body_splitter, 1)

        self._refresh_state()
        self._render_transcript()

    def _build_welcome_page(self) -> QWidget:
        welcome = QWidget()
        welcome.setObjectName("advisorWelcome")
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(54, 24, 54, 24)
        layout.addStretch(2)
        eyebrow = QLabel("EQUISEEK RESEARCH ASSISTANT")
        eyebrow.setObjectName("advisorWelcomeEyebrow")
        layout.addWidget(eyebrow)
        headline = QLabel("把投资问题，变成\n可追溯的研究任务")
        headline.setObjectName("advisorWelcomeTitle")
        layout.addWidget(headline)
        summary = QLabel(
            "选择自己的 Skill 和工作区。Agent 会公开执行步骤、数据来源、规则门控与成果，"
            "但不会展示或保存模型的隐藏思维链。"
        )
        summary.setObjectName("advisorWelcomeSummary")
        summary.setWordWrap(True)
        summary.setMaximumWidth(650)
        layout.addWidget(summary)
        layout.addSpacing(16)
        row_one = QHBoxLayout()
        row_two = QHBoxLayout()
        for index, prompt in enumerate(
            (
                "研究 600050.SH 的买入条件",
                "从自选池筛选低回撤候选",
                "解释宏观数据来源与截止日",
                "联网核对最新公告再评估风险",
            )
        ):
            button = QPushButton(prompt)
            button.setObjectName("advisorSuggestion")
            button.clicked.connect(lambda _checked=False, value=prompt: self.submit(value))
            (row_one if index < 2 else row_two).addWidget(button)
        row_one.addStretch()
        row_two.addStretch()
        layout.addLayout(row_one)
        layout.addLayout(row_two)
        layout.addStretch(3)
        return welcome

    def _build_run_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("advisorRunPanel")
        run_layout = QVBoxLayout(panel)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.setSpacing(0)
        panel_header = QHBoxLayout()
        panel_header.setContentsMargins(14, 12, 12, 9)
        detail_title = QLabel("执行详情")
        detail_title.setObjectName("advisorPanelTitle")
        panel_header.addWidget(detail_title)
        panel_header.addStretch()
        self.trace_badge = QLabel("尚未运行")
        self.trace_badge.setObjectName("advisorTraceBadge")
        panel_header.addWidget(self.trace_badge)
        run_layout.addLayout(panel_header)

        self.run_tabs = QTabWidget()
        self.run_tabs.setObjectName("advisorRunTabs")
        plan_page = QWidget()
        plan_layout = QVBoxLayout(plan_page)
        plan_layout.setContentsMargins(12, 11, 12, 12)
        plan_layout.setSpacing(8)
        plan_caption = QLabel("当前 Goal")
        plan_caption.setObjectName("advisorFieldCaption")
        plan_layout.addWidget(plan_caption)
        self.plan_goal = QLabel("发送目标后，Agent 会拆解计划并公开子智能体分工。")
        self.plan_goal.setObjectName("advisorPlanGoal")
        self.plan_goal.setWordWrap(True)
        plan_layout.addWidget(self.plan_goal)
        self.plan_stats = QLabel("0 个任务 · 0 个子智能体")
        self.plan_stats.setObjectName("advisorPlanStats")
        plan_layout.addWidget(self.plan_stats)
        self.agent_list = QListWidget()
        self.agent_list.setObjectName("advisorAgentList")
        self.agent_list.setAccessibleName("求衡投研助手 任务计划与子智能体分工")
        plan_layout.addWidget(self.agent_list, 1)
        plan_note = QLabel("这里展示可审阅的任务拆分、依赖和执行状态，不展示模型隐藏思维链。")
        plan_note.setObjectName("advisorRunMeta")
        plan_note.setWordWrap(True)
        plan_layout.addWidget(plan_note)
        self.run_tabs.addTab(plan_page, "计划")

        trace_page = QWidget()
        trace_layout = QVBoxLayout(trace_page)
        trace_layout.setContentsMargins(12, 11, 12, 12)
        trace_layout.setSpacing(8)
        self.used_skill_label = QLabel("本轮 Skill · 等待选择")
        self.used_skill_label.setObjectName("advisorUsedSkill")
        self.used_skill_label.setWordWrap(True)
        trace_layout.addWidget(self.used_skill_label)
        self.run_goal = QLabel("发送研究目标后，这里展示完整可审阅 Trace。")
        self.run_goal.setObjectName("advisorRunGoal")
        self.run_goal.setWordWrap(True)
        trace_layout.addWidget(self.run_goal)
        self.run_progress = QProgressBar()
        self.run_progress.setObjectName("advisorRunProgress")
        self.run_progress.setRange(0, 1)
        self.run_progress.setValue(0)
        self.run_progress.setTextVisible(False)
        trace_layout.addWidget(self.run_progress)
        trace_toolbar = QHBoxLayout()
        trace_toolbar.setSpacing(7)
        trace_caption = QLabel("查看")
        trace_caption.setObjectName("advisorFieldCaption")
        trace_toolbar.addWidget(trace_caption)
        self.trace_filter_combo = QComboBox()
        self.trace_filter_combo.setObjectName("advisorTraceFilter")
        self.trace_filter_combo.setAccessibleName("筛选求衡投研助手 Trace 步骤")
        self.trace_filter_combo.addItem("全部步骤", "all")
        self.trace_filter_combo.addItem("规划与 Skill", "planning")
        self.trace_filter_combo.addItem("数据与工具", "tools")
        self.trace_filter_combo.addItem("研究任务", "research")
        self.trace_filter_combo.addItem("决策与风控", "decision")
        self.trace_filter_combo.currentIndexChanged.connect(self._apply_trace_filter)
        trace_toolbar.addWidget(self.trace_filter_combo, 1)
        self.copy_trace_button = QPushButton("复制摘要")
        self.copy_trace_button.setObjectName("advisorInlineButton")
        self.copy_trace_button.setAccessibleName("复制完整 Trace 摘要")
        self.copy_trace_button.clicked.connect(self._copy_trace_summary)
        trace_toolbar.addWidget(self.copy_trace_button)
        trace_layout.addLayout(trace_toolbar)
        self.task_list = QListWidget()
        self.task_list.setObjectName("advisorTaskList")
        self.task_list.setAccessibleName("求衡投研助手 可审阅执行 Trace")
        self.task_list.itemSelectionChanged.connect(self._show_trace_detail)
        self.task_list.itemDoubleClicked.connect(self._open_trace_evidence)
        trace_layout.addWidget(self.task_list, 1)
        self.trace_detail = QLabel("选择一个步骤查看工具理由、Skill 和证据位置。")
        self.trace_detail.setObjectName("advisorTraceDetail")
        self.trace_detail.setWordWrap(True)
        trace_layout.addWidget(self.trace_detail)
        trace_actions = QHBoxLayout()
        trace_actions.addStretch()
        self.open_evidence_button = QPushButton("打开证据")
        self.open_evidence_button.setObjectName("advisorInlineButton")
        self.open_evidence_button.setAccessibleName("打开当前 Trace 步骤的证据文件")
        self.open_evidence_button.setEnabled(False)
        self.open_evidence_button.clicked.connect(self._open_selected_trace_evidence)
        trace_actions.addWidget(self.open_evidence_button)
        trace_layout.addLayout(trace_actions)
        self.run_meta = QLabel("本地 SQLite 状态 · 每轮隔离工作区")
        self.run_meta.setObjectName("advisorRunMeta")
        self.run_meta.setWordWrap(True)
        trace_layout.addWidget(self.run_meta)
        self.run_tabs.addTab(trace_page, "Trace")

        artifact_page = QWidget()
        artifact_layout = QVBoxLayout(artifact_page)
        artifact_layout.setContentsMargins(12, 11, 12, 12)
        self.artifact_list = QListWidget()
        self.artifact_list.setObjectName("advisorArtifactList")
        self.artifact_list.setAccessibleName("求衡投研助手 工作区成果")
        self.artifact_list.itemDoubleClicked.connect(self._open_artifact)
        artifact_layout.addWidget(self.artifact_list, 1)
        self.open_workspace_button = QPushButton("打开本次运行目录")
        self.open_workspace_button.setObjectName("advisorSecondaryButton")
        self.open_workspace_button.setEnabled(False)
        self.open_workspace_button.clicked.connect(self._open_workspace)
        artifact_layout.addWidget(self.open_workspace_button)
        self.run_tabs.addTab(artifact_page, "成果")

        context_page = QWidget()
        context_layout = QVBoxLayout(context_page)
        context_layout.setContentsMargins(12, 11, 12, 12)
        self.skill_summary = QLabel(
            "Skill 按需加载；用户目录中的同名 Skill 优先，可关闭全部内置 Skill。"
        )
        self.skill_summary.setObjectName("advisorSideText")
        self.skill_summary.setWordWrap(True)
        context_layout.addWidget(self.skill_summary)
        manage_skills_button = QPushButton("管理 Skill")
        manage_skills_button.setObjectName("advisorSecondaryButton")
        manage_skills_button.clicked.connect(
            lambda _checked=False: self.skill_manager_requested.emit()
        )
        context_layout.addWidget(manage_skills_button)
        self.privacy_notice = QLabel(
            "长期记忆只保存明确表达的风险偏好、投资周期和策略偏好。持仓数量、成本、"
            "备注与凭据不会进入长期记忆；联网搜索和模型调用均需单独配置。文件工具只能"
            "访问本轮隔离工作区，不提供主机 Shell。"
        )
        self.privacy_notice.setObjectName("advisorSideText")
        self.privacy_notice.setWordWrap(True)
        context_layout.addWidget(self.privacy_notice)
        context_layout.addStretch()
        self.run_tabs.addTab(context_page, "上下文")
        run_layout.addWidget(self.run_tabs)
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(420)
        return panel

    def set_available_skills(self, skills: Sequence[object]) -> None:
        current = self.selected_skill_name
        self.skill_combo.blockSignals(True)
        self.skill_combo.clear()
        self.skill_combo.addItem("自动选择 Skill", "")
        for skill in skills:
            name = str(getattr(skill, "name", ""))
            if not name:
                continue
            provider = str(getattr(skill, "provider", ""))
            label = f"我的 Skill · {name}" if provider.startswith("user-") else f"内置 · {name}"
            self.skill_combo.addItem(label, name)
            index = self.skill_combo.count() - 1
            self.skill_combo.setItemData(
                index,
                f"{getattr(skill, 'description', '')}\n来源：{provider} · "
                f"版本：{getattr(skill, 'version', '')}",
                Qt.ItemDataRole.ToolTipRole,
            )
        index = self.skill_combo.findData(current)
        self.skill_combo.setCurrentIndex(max(0, index))
        self.skill_combo.blockSignals(False)
        self._refresh_composer_scope()

    def set_workspace_root(self, path: str, *, is_default: bool) -> None:
        self._workspace_root = path
        label = "默认工作区" if is_default else Path(path).name or "自选工作区"
        self.workspace_button.setText(f"工作区 · {label}")
        self.workspace_button.setToolTip(path)
        self._refresh_composer_scope()

    def set_positions(self, positions: Sequence[Position], preferred_symbol: str = "") -> None:
        current = preferred_symbol or self.selected_symbol
        self.position_combo.blockSignals(True)
        self.position_combo.clear()
        self.position_combo.addItem("通用研究", "")
        for position in positions:
            name = f" · {position.name}" if position.name else ""
            self.position_combo.addItem(f"{position.symbol}{name}", position.symbol)
        index = self.position_combo.findData(current)
        self.position_combo.setCurrentIndex(
            index if index >= 0 and (current or not positions) else (1 if positions else 0)
        )
        self.position_combo.blockSignals(False)
        self.analyze_button.setEnabled(bool(self.selected_symbol))
        self._refresh_state()

    def set_evidence(self, evidence: AdvisorEvidence | None, reason: str = "") -> None:
        changed = self._evidence != evidence
        self._evidence = evidence
        if changed:
            self._turns.clear()
            self._reset_run_view()
        if evidence is None:
            self.context_label.setText(reason or "通用研究线程 · 未绑定证券证据")
            if not self._busy:
                self.mode_badge.setText("就绪")
            self.mode_badge.setProperty("active", False)
        else:
            self.context_label.setText(
                f"{evidence.symbol} · {evidence.as_of} · {evidence.rule_action} "
                f"{evidence.rule_confidence}/100"
            )
            if not self._busy:
                self.mode_badge.setText("证据就绪")
            self.mode_badge.setProperty("active", True)
        self.mode_badge.style().unpolish(self.mode_badge)
        self.mode_badge.style().polish(self.mode_badge)
        self._refresh_state()
        self._render_transcript()

    def submit(self, prompt: str | bool = "") -> None:
        question = (
            prompt if isinstance(prompt, str) and prompt else self.question_input.toPlainText()
        )
        if not question.strip() or self._busy:
            return
        self.question_submitted.emit(question)

    def set_turns(self, turns: Sequence[AdvisorTurn]) -> None:
        self._turns = list(turns)
        self._refresh_state()
        self._render_transcript()

    def append_user(self, content: str) -> None:
        self._turns.append(AdvisorTurn("user", content))
        self.question_input.clear()
        self._render_transcript()

    def append_answer(self, answer: AdvisorAnswer) -> None:
        mode = "DeepSeek Agent" if answer.mode == "deepseek" else "本地 Agent"
        warning = f"\n\n> 运行提示：{answer.warning}" if answer.warning else ""
        self._turns.append(
            AdvisorTurn("assistant", f"{answer.text}{warning}\n\n_回答模式：{mode}_")
        )
        self.set_busy(False)
        self._render_transcript()

    def append_error(self, message: str) -> None:
        self._turns.append(
            AdvisorTurn(
                "assistant",
                f"本次运行失败：{message}\n\n工作区记录已尽量保留，请检查设置后重试。",
            )
        )
        self.set_busy(False)
        self._render_transcript()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.mode_badge.setText(
            "运行中" if busy else ("证据就绪" if self._evidence is not None else "就绪")
        )
        self.position_combo.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy and bool(self.selected_symbol))
        self._refresh_state()

    def begin_agent_run(self, payload: dict[str, object]) -> None:
        self.task_list.clear()
        self.artifact_list.clear()
        self._trace_payloads.clear()
        self._run_steps.clear()
        self._workspace_path = str(payload.get("workspace", ""))
        goal = str(payload.get("goal", "Agent 研究任务"))
        self.run_goal.setText(goal)
        self.plan_goal.setText(goal)
        self.plan_stats.setText("正在规划任务…")
        self.agent_list.clear()
        self.agent_list.addItem("investment-lead-agent\n规划中 · 正在选择 Skill 与工具")
        self.run_progress.setRange(0, 0)
        self.run_meta.setText(f"运行 {payload.get('run_id', '—')} · 状态持续写入本地")
        skill = self.selected_skill_name
        self.used_skill_label.setText(
            f"本轮 Skill · {skill}" if skill else "本轮 Skill · Agent 自动选择"
        )
        self.trace_badge.setText("运行中")
        self.trace_filter_combo.setCurrentIndex(0)
        self.trace_filter_combo.setEnabled(False)
        self.copy_trace_button.setEnabled(False)
        self.open_evidence_button.setEnabled(False)
        self.open_workspace_button.setEnabled(bool(self._workspace_path))
        self.run_tabs.setCurrentIndex(0)
        self.set_busy(True)

    def update_agent_progress(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if kind == "run-started":
            self.begin_agent_run(payload)
            return
        if kind == "step-started":
            index = int(payload.get("index", len(self._run_steps) + 1))
            title = str(payload.get("title", payload.get("tool", "执行步骤")))
            item = QListWidgetItem(f"{index:02d}  进行中  {title}")
            item.setData(Qt.ItemDataRole.UserRole, dict(payload))
            self.task_list.addItem(item)
            self._run_steps[index] = item
            self.run_progress.setRange(0, int(payload.get("total", 1)))
            self.run_progress.setValue(max(0, index - 1))
            self.task_list.setCurrentItem(item)
            return
        if kind == "step-ended":
            index_value = payload.get("index", 0)
            index = index_value if isinstance(index_value, int) else 0
            current_item = self._run_steps.get(index)
            if current_item is not None:
                status = "完成" if payload.get("status") == "succeeded" else "失败"
                title = str(payload.get("title", payload.get("tool", "执行步骤")))
                current_item.setText(f"{index:02d}  {status}  {title}")
                current_item.setData(Qt.ItemDataRole.UserRole, dict(payload))
                current_item.setToolTip(str(payload.get("detail", "")))
            self.run_progress.setValue(index)

    def finish_agent_run(self, result: InvestmentAgentRunResult) -> None:
        self._workspace_path = result.workspace
        self._trace_payloads.clear()
        for index, step in enumerate(result.trace, start=1):
            self._trace_payloads.append(
                {
                    "index": index,
                    "stage": step.stage,
                    "title": step.title,
                    "status": step.status,
                    "summary": step.summary,
                    "skills": list(step.skill_names),
                    "tool": step.tool_name,
                    "evidence_path": step.evidence_path,
                    "agent": step.agent_name,
                    "depends_on": list(step.depends_on),
                }
            )
        self.trace_filter_combo.setEnabled(bool(self._trace_payloads))
        self.copy_trace_button.setEnabled(bool(self._trace_payloads))
        self._apply_trace_filter()

        self.agent_list.clear()
        agent_names: list[str] = []
        for step in result.trace:
            agent = step.agent_name or (
                "investment-lead-agent"
                if step.stage in {"goal", "skill", "tool", "guardrail"}
                else "research-pipeline"
            )
            agent_names.append(agent)
            dependency = " → 依赖 " + "、".join(step.depends_on) if step.depends_on else ""
            status = {
                "succeeded": "完成",
                "passed": "通过",
                "failed": "失败",
                "skipped": "跳过",
                "blocked": "阻断",
            }.get(step.status, step.status)
            item = QListWidgetItem(f"{agent}\n{status} · {step.title}{dependency}")
            item.setToolTip(step.summary)
            self.agent_list.addItem(item)
        unique_agents = tuple(dict.fromkeys(agent_names))
        self.plan_stats.setText(
            f"{len(result.trace)} 个任务 · {len(unique_agents)} 个子智能体/执行角色"
        )

        self.artifact_list.clear()
        for artifact in result.artifacts:
            item = QListWidgetItem(f"{artifact.name}  ·  {artifact.size_bytes / 1024:.1f} KiB")
            item.setData(Qt.ItemDataRole.UserRole, artifact.path)
            item.setToolTip("双击打开")
            self.artifact_list.addItem(item)

        skill_labels = [
            f"{item['name']}（{item['provider']} · {item['version']}）"
            for item in result.active_skills
        ]
        skill_text = "、".join(skill_labels) or "无（仅平台固定能力）"
        trace_skill_names = list(
            dict.fromkeys(name for step in result.trace for name in step.skill_names)
        )
        trace_skill_text = "、".join(trace_skill_names) or "无"
        self.used_skill_label.setText(f"本轮 Skill · {trace_skill_text}")
        self.skill_summary.setText(
            f"本轮入口 Skill\n{skill_text}\n\n研究流水线 Skill\n{trace_skill_text}\n\n工具链\n"
            + (" → ".join(result.tool_calls) or "本轮未调用工具")
            + "\n\n用户 Skill 可从输入框下方直接选择；同名用户版本优先于内置版本。"
        )
        self.open_workspace_button.setEnabled(True)
        self.run_progress.setRange(0, 1)
        self.run_progress.setValue(1)
        self.trace_badge.setText(f"{len(result.trace)} 步")
        self.run_meta.setText(
            f"运行 {result.run_id} · {result.status} · {len(result.artifacts)} 个成果"
        )
        self.run_tabs.setTabText(0, f"计划 {len(unique_agents)}")
        self.run_tabs.setTabText(1, f"Trace {len(result.trace)}")
        self.run_tabs.setTabText(2, f"成果 {len(result.artifacts)}")
        self.set_busy(False)

    def clear_conversation(self, *, notify: bool = True) -> None:
        self._turns.clear()
        self._reset_run_view()
        self._render_transcript()
        self._refresh_state()
        if notify:
            self.conversation_cleared.emit()

    def _emit_position_changed(self) -> None:
        self.position_changed.emit(self.selected_symbol)

    def _request_analysis(self) -> None:
        if self.selected_symbol:
            self.analysis_requested.emit(self.selected_symbol)

    def _refresh_state(self) -> None:
        self.send_button.setEnabled(not self._busy)
        self.question_input.setEnabled(not self._busy)
        self.skill_combo.setEnabled(not self._busy)
        self.workspace_button.setEnabled(not self._busy)
        self.clear_button.setEnabled(bool(self._turns) and not self._busy)
        self.copy_answer_button.setEnabled(
            any(turn.role == "assistant" for turn in self._turns) and not self._busy
        )

    def _render_transcript(self) -> None:
        if not self._turns:
            self.conversation_stack.setCurrentWidget(self.welcome_page)
            self.transcript.clear()
            return
        self.conversation_stack.setCurrentWidget(self.transcript)
        chunks: list[str] = []
        for turn in self._turns:
            if turn.role == "user":
                chunks.append(f"### 你\n\n{_escape_markdown(turn.content)}")
            else:
                chunks.append(f"### Agent\n\n{turn.content}")
        self.transcript.setMarkdown("\n\n---\n\n".join(chunks))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _show_trace_detail(self) -> None:
        item = self.task_list.currentItem()
        payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(payload, dict):
            self.trace_detail.setText("选择一个步骤查看工具理由、Skill 和证据位置。")
            self.open_evidence_button.setEnabled(False)
            return
        skills = payload.get("skills", [])
        skill_text = "、".join(str(value) for value in skills) if isinstance(skills, list) else ""
        lines = [
            str(payload.get("summary") or payload.get("detail") or "暂无详细说明"),
            f"Skill：{skill_text or '平台固定能力'}",
        ]
        if payload.get("tool"):
            lines.append(f"工具：{payload['tool']}")
        if payload.get("agent"):
            lines.append(f"子智能体：{payload['agent']}")
        dependencies = payload.get("depends_on", [])
        if isinstance(dependencies, list) and dependencies:
            lines.append("依赖：" + "、".join(str(value) for value in dependencies))
        if payload.get("evidence_path"):
            lines.append("证据文件已就绪，可直接打开")
        self.trace_detail.setText("\n".join(lines))
        evidence_path = Path(str(payload.get("evidence_path", "")))
        self.open_evidence_button.setEnabled(evidence_path.is_file())

    def _open_trace_evidence(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        path = Path(str(payload.get("evidence_path", "")))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_selected_trace_evidence(self) -> None:
        item = self.task_list.currentItem()
        if item is not None:
            self._open_trace_evidence(item)

    def toggle_run_panel(self) -> None:
        visible = not self.run_panel.isVisible()
        self.run_panel.setVisible(visible)
        self.details_button.setText("隐藏详情" if visible else "显示详情")
        self.details_button.setProperty("active", visible)
        self.details_button.style().unpolish(self.details_button)
        self.details_button.style().polish(self.details_button)

    def _refresh_composer_scope(self) -> None:
        if not hasattr(self, "composer_scope"):
            return
        skill = self.selected_skill_name
        skill_text = f"已指定 {skill}" if skill else "Agent 自动选择 Skill"
        workspace = self.workspace_button.text().removeprefix("工作区 · ")
        self.composer_scope.setText(f"{skill_text} · {workspace}")
        tooltip = self.skill_combo.currentData(Qt.ItemDataRole.ToolTipRole)
        self.composer_scope.setToolTip(str(tooltip or self._workspace_root))

    @staticmethod
    def _trace_group(stage: str) -> str:
        if stage in {"goal", "skill"}:
            return "planning"
        if stage in {"tool", "evidence", "calculation", "artifact"}:
            return "tools"
        if stage == "research-task":
            return "research"
        if stage in {"decision-gate", "decision", "trigger", "scenario", "guardrail"}:
            return "decision"
        return "all"

    def _apply_trace_filter(self) -> None:
        if not hasattr(self, "trace_filter_combo"):
            return
        selected = str(self.trace_filter_combo.currentData() or "all")
        self.task_list.clear()
        for payload in self._trace_payloads:
            if selected != "all" and self._trace_group(str(payload.get("stage", ""))) != selected:
                continue
            index_value = payload.get("index", 0)
            index = index_value if isinstance(index_value, int) else 0
            status = {
                "succeeded": "完成",
                "failed": "失败",
                "skipped": "跳过",
                "passed": "通过",
                "blocked": "阻断",
            }.get(str(payload.get("status", "")), str(payload.get("status", "")))
            item = QListWidgetItem(f"{index:02d}  {status}  {payload.get('title', '')}")
            item.setData(Qt.ItemDataRole.UserRole, payload)
            item.setToolTip(str(payload.get("summary", "")))
            self.task_list.addItem(item)
        if self.task_list.count():
            self.task_list.setCurrentRow(0)
        else:
            self.trace_detail.setText("当前筛选条件下没有 Trace 步骤。")
            self.open_evidence_button.setEnabled(False)

    def _copy_latest_answer(self) -> None:
        answer = next(
            (turn.content for turn in reversed(self._turns) if turn.role == "assistant"), ""
        )
        if not answer:
            return
        QApplication.clipboard().setText(answer)
        self._show_temporary_button_text(self.copy_answer_button, "已复制", "复制回答")

    def _copy_trace_summary(self) -> None:
        if not self._trace_payloads:
            return
        lines = [
            f"{str(payload.get('index', '0')).zfill(2)}. [{payload.get('status', '')}] "
            f"{payload.get('title', '')} — {payload.get('summary', '')}"
            for payload in self._trace_payloads
        ]
        QApplication.clipboard().setText("\n".join(lines))
        self._show_temporary_button_text(self.copy_trace_button, "已复制", "复制摘要")

    @staticmethod
    def _show_temporary_button_text(button: QPushButton, temporary: str, original: str) -> None:
        button.setText(temporary)
        QTimer.singleShot(1_500, lambda: button.setText(original))

    def _reset_run_view(self) -> None:
        if not hasattr(self, "task_list"):
            return
        self._trace_payloads.clear()
        self._run_steps.clear()
        self._workspace_path = ""
        self.task_list.clear()
        self.agent_list.clear()
        self.artifact_list.clear()
        self.trace_filter_combo.setCurrentIndex(0)
        self.trace_filter_combo.setEnabled(False)
        self.copy_trace_button.setEnabled(False)
        self.open_evidence_button.setEnabled(False)
        self.run_goal.setText("发送研究目标后，这里展示完整可审阅 Trace。")
        self.plan_goal.setText("发送目标后，Agent 会拆解计划并公开子智能体分工。")
        self.plan_stats.setText("0 个任务 · 0 个子智能体")
        self.used_skill_label.setText("本轮 Skill · 等待选择")
        self.trace_detail.setText("选择一个步骤查看工具理由、Skill 和证据位置。")
        self.trace_badge.setText("尚未运行")
        self.run_progress.setRange(0, 1)
        self.run_progress.setValue(0)
        self.run_meta.setText("本地 SQLite 状态 · 每轮隔离工作区")
        self.run_tabs.setTabText(0, "计划")
        self.run_tabs.setTabText(1, "Trace")
        self.run_tabs.setTabText(2, "成果")
        self.open_workspace_button.setEnabled(False)

    def _open_artifact(self, item: QListWidgetItem) -> None:
        path = Path(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_workspace(self) -> None:
        path = Path(self._workspace_path)
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_workspace_root(self) -> None:
        path = Path(self._workspace_root)
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
