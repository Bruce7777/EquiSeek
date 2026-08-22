from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QPoint, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from aegisrun.agents.investment_runtime import (
    InvestmentAgentRunRequest,
    InvestmentAgentRuntime,
)
from aegisrun.application.services import market_data_provider
from aegisrun.desktop.credentials import CredentialStore
from aegisrun.desktop.macro_dialog import MacroDialog
from aegisrun.desktop.main_window import MainWindow
from aegisrun.desktop.portfolio_dialog import BacktestDialog
from aegisrun.macro.pipeline import run_macro_research
from aegisrun.macro.providers import BundledOfficialMacroProvider
from aegisrun.marketdata.baostock_provider import BaoStockProvider
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.marketdata.timeframes import Timeframe
from aegisrun.portfolio.models import PortfolioBook, Position
from aegisrun.research.advisor_chat import AdvisorAnswer, build_advisor_evidence
from aegisrun.research.backtest import backtest_report_digest, walk_forward_backtest
from aegisrun.research.deepseek import DeepSeekClient, DeepSeekConfig
from aegisrun.research.service import run_research

# ruff: noqa: E501
STYLE_SHEET = """
QWidget { background: #071019; color: #DCE6EC; font-size: 13px; }
QLabel { background: transparent; }
QMainWindow, QMenuBar, QMenu { background: #071019; }
QMenuBar::item:selected, QMenu::item:selected { background: #17313D; }
#pageTitle { font-size: 25px; font-weight: 700; color: #F2F7FA; }
#researchEyebrow { color: #35D0A0; font-size: 10px; font-weight: 800; letter-spacing: 1px; }
#pageSubtitle, #footerText, #mutedText { color: #8399A8; }
#complianceBanner { background: #122A31; border: 1px solid #245664; border-radius: 7px; color: #A9DCE2; padding: 9px 12px; }
#controlPanel, #chartPanel { background: #0B1721; border: 1px solid #1C2E3A; border-radius: 9px; }
#marketEvidencePanel { background: transparent; border: 0; }
#decisionPanel { background: #08141D; border: 1px solid #294552; border-radius: 10px; }
#decisionPanelTitle { color: #F2F7FA; font-size: 14px; font-weight: 750; }
#fieldLabel, #metricLabel { color: #8095A3; font-size: 11px; font-weight: 600; }
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox, #indicatorSelector { background: #0D1C27; border: 1px solid #29404F; border-radius: 6px; padding: 7px 9px; min-height: 20px; selection-background-color: #176C59; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, #indicatorSelector:focus { border: 1px solid #35D0A0; }
#indicatorSelector:hover { border-color: #35D0A0; color: #F2F7FA; }
#indicatorSelector::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; right: 7px; }
QComboBox QAbstractItemView { background: #0D1C27; selection-background-color: #176C59; }
QTableWidget { background: #0B1721; alternate-background-color: #0D1C27; border: 1px solid #29404F; gridline-color: #1C2E3A; }
QHeaderView::section { background: #10202B; color: #AFC1CB; border: 0; border-right: 1px solid #29404F; padding: 7px; }
#marketTimeframeTable { font-size: 11px; }
#marketTimeframeTable QHeaderView::section { padding: 1px; font-size: 10px; }
QPushButton { background: #10202B; color: #C7D5DD; border: 1px solid #2B4351; border-radius: 6px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { border-color: #35D0A0; color: #F2F7FA; }
#primaryButton { background: #35D0A0; color: #04100D; border: 1px solid #35D0A0; }
#primaryButton:hover { background: #52DDB3; }
#primaryButton:disabled { background: #315047; color: #879C95; border-color: #315047; }
#secondaryButton { background: #10202B; color: #C7D5DD; border: 1px solid #2B4351; }
#secondaryButton:hover { border-color: #35D0A0; color: #F2F7FA; }
#quietButton { background: transparent; border-color: transparent; color: #91A5B1; padding: 6px 9px; }
#quietButton:hover { background: #10202B; border-color: #29404F; color: #EAF3F6; }
#sourceNotice { background: #0B1721; border-left: 3px solid #35D0A0; color: #AFC1CB; padding: 8px 11px; }
#sourceNotice[synthetic="true"] { background: #2A2010; border-left-color: #EABF5A; color: #F0D58F; }
#statusPill { background: #15212A; border: 1px solid #2B3B47; border-radius: 12px; color: #94A8B5; padding: 5px 10px; }
#statusPill[active="true"] { background: #0D2A24; border-color: #257B65; color: #73E2BE; }
#metricCard { background: #0B1721; border: 1px solid #1C2E3A; border-radius: 8px; }
#metricValue { color: #F1F6F8; font-family: Menlo, Consolas, monospace; font-size: 17px; font-weight: 650; }
#metricValue[tone="positive"] { color: #F06B71; }
#metricValue[tone="negative"] { color: #43C590; }
#metricValue[tone="danger"] { color: #F0A45D; }
QTabWidget::pane { background: #0B1721; border: 1px solid #1C2E3A; border-radius: 7px; }
QTabBar::tab { background: #0A151E; color: #8FA3AF; padding: 9px 15px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #DDF9EF; border-bottom-color: #35D0A0; }
QTabWidget#workspaceTabs::pane { border: 0; background: #071019; }
#summaryView { background: #0B1721; border: 0; color: #C5D3DB; padding: 10px; font-family: "SF Mono", Menlo, Consolas, monospace; line-height: 1.5; }
#holdingAdvisor { background: #F7F8F6; color: #1F2926; }
#advisorTopBar { background: #FFFFFF; border-bottom: 1px solid #E5E9E5; }
#advisorPageTitle { color: #17201D; font-size: 20px; font-weight: 750; }
#advisorPageSubtitle { color: #7B8681; font-size: 12px; }
#advisorLocalBadge { background: #F2F7F4; border: 1px solid #D4E5DD; border-radius: 11px; color: #31745E; padding: 5px 9px; font-size: 10px; font-weight: 700; }
#advisorConversation { background: #FAFBF9; border: 0; }
#advisorContextBar { background: #F0F3F0; border: 1px solid #E0E5E1; border-radius: 9px; }
#advisorFieldCaption { color: #73807A; font-size: 11px; font-weight: 700; }
#advisorContextStatus { color: #65716C; }
QComboBox#advisorPositionCombo, QComboBox#advisorSkillCombo, QComboBox#advisorTraceFilter { background: #FFFFFF; color: #27312E; border: 1px solid #D8DEDA; border-radius: 7px; padding: 6px 10px; }
QComboBox#advisorPositionCombo:hover, QComboBox#advisorSkillCombo:hover, QComboBox#advisorTraceFilter:hover { border-color: #7BBDA7; }
#advisorConversationStack, #advisorWelcome { background: transparent; border: 0; }
#advisorWelcomeEyebrow { color: #35A983; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
#advisorWelcomeTitle { color: #17201D; font-size: 34px; font-weight: 800; line-height: 1.15; }
#advisorWelcomeSummary { color: #69756F; font-size: 14px; line-height: 1.55; }
#advisorTranscript { background: #FFFFFF; border: 1px solid #E2E7E3; border-radius: 11px; color: #2A3430; padding: 20px 24px; selection-background-color: #CFEDE2; }
#advisorComposer { background: #FFFFFF; border: 1px solid #DDE3DF; border-radius: 12px; }
#advisorQuestionInput { background: transparent; border: 0; color: #24302B; padding: 2px; selection-background-color: #CFEDE2; }
#advisorQuestionInput:focus { border: 0; }
#advisorComposerHint { color: #98A19D; font-size: 11px; }
#advisorModeBadge { background: #EEF2EF; border: 1px solid #D8DFDA; border-radius: 11px; color: #67736D; padding: 5px 10px; font-weight: 700; }
#advisorModeBadge[active="true"] { background: #E4F5EE; border-color: #ABD8C7; color: #22785F; }
#advisorQuietButton, #advisorSecondaryButton, #advisorWorkspaceButton { background: #FFFFFF; color: #53605A; border: 1px solid #D9DFDB; border-radius: 7px; padding: 6px 10px; }
#advisorQuietButton:hover, #advisorSecondaryButton:hover, #advisorWorkspaceButton:hover { border-color: #73B9A1; color: #1F6F58; }
#advisorQuietButton[active="true"] { background: #E9F5F0; border-color: #A8D8C7; color: #1F6F58; }
#advisorInlineButton { background: transparent; color: #39745F; border: 0; padding: 4px 6px; font-size: 11px; font-weight: 650; }
#advisorInlineButton:hover { background: #E5F2ED; color: #145D47; border-radius: 5px; }
#advisorInlineButton:disabled { background: transparent; color: #A5AEAA; }
#advisorSendButton { background: #32C99A; color: #09261D; border: 1px solid #32C99A; border-radius: 8px; padding: 7px 17px; font-weight: 750; }
#advisorSendButton:hover { background: #45D4A7; }
#advisorSendButton:disabled { background: #C6D8D1; color: #87968F; border-color: #C6D8D1; }
#advisorSuggestion { background: #FFFFFF; color: #54615B; border: 1px solid #DCE2DE; border-radius: 8px; padding: 7px 11px; font-weight: 550; }
#advisorSuggestion:hover { background: #F2FAF7; border-color: #75BFA5; color: #176B52; }
#advisorRunPanel { background: #F2F4F1; border-left: 1px solid #DFE4E0; }
#advisorSplitter::handle { background: #DFE4E0; width: 1px; }
#advisorPanelTitle { color: #27312D; font-size: 14px; font-weight: 750; }
#advisorTraceBadge { background: #FFFFFF; color: #76817C; border: 1px solid #DCE1DD; border-radius: 9px; padding: 3px 7px; font-size: 10px; }
QTabWidget#advisorRunTabs::pane { background: #F2F4F1; border: 0; border-top: 1px solid #DFE4E0; }
QTabWidget#advisorRunTabs QWidget { background: #F2F4F1; color: #53605A; }
QTabWidget#advisorRunTabs QTabBar::tab { background: transparent; color: #7B8680; padding: 8px 12px; border-bottom: 2px solid transparent; }
QTabWidget#advisorRunTabs QTabBar::tab:selected { color: #1F6F58; border-bottom-color: #35B88D; }
#advisorUsedSkill { background: #E7F5EF; color: #226B55; border: 1px solid #C6E4D8; border-radius: 8px; padding: 8px 9px; font-size: 11px; font-weight: 650; }
#advisorRunGoal { color: #35413C; font-size: 12px; font-weight: 650; }
#advisorSideText { color: #66736D; line-height: 1.5; }
#advisorTaskList, #advisorArtifactList, #advisorAgentList { background: #F2F4F1; border: 0; outline: 0; color: #53605A; }
#advisorTaskList::item, #advisorArtifactList::item, #advisorAgentList::item { border-bottom: 1px solid #E0E5E1; padding: 9px 5px; }
#advisorTaskList::item:selected, #advisorArtifactList::item:selected, #advisorAgentList::item:selected { background: #E3F2EC; color: #1D654F; border-radius: 5px; }
#advisorPlanGoal { background: #FFFFFF; color: #26332E; border: 1px solid #DEE5E0; border-radius: 8px; padding: 10px; font-weight: 650; }
#advisorPlanStats { color: #28735B; font-size: 11px; font-weight: 750; }
#advisorTraceDetail { background: #FFFFFF; color: #66736D; border: 1px solid #E0E5E1; border-radius: 7px; padding: 8px; font-size: 11px; }
#advisorRunMeta { color: #8B9690; font-size: 10px; }
#advisorRunProgress { background: #DDE4DF; border: 0; border-radius: 2px; max-height: 4px; }
#advisorRunProgress::chunk { background: #35B88D; border-radius: 2px; }
#investmentDecision, #decisionBody { background: #08141D; border: 0; }
#decisionHero { background: #0D2725; border: 1px solid #1F6D59; border-radius: 9px; }
#decisionAction { background: #35D0A0; color: #04100D; border-radius: 7px; padding: 10px 13px; font-size: 20px; font-weight: 800; min-width: 58px; }
#decisionAction[action="sell"], #decisionAction[action="reduce"], #decisionAction[action="avoid"] { background: #F06B71; color: #190608; }
#decisionAction[action="wait"], #decisionAction[action="hold"] { background: #EABF5A; color: #1D1604; }
#decisionSymbol { color: #F2F7FA; font-size: 15px; font-weight: 700; }
#decisionConfidence, #decisionDirection { color: #A9C6CF; }
#decisionCard, #evidenceRow { background: #0B1B25; border: 1px solid #203946; border-radius: 7px; }
#decisionCaption { color: #7F97A5; font-size: 11px; font-weight: 700; }
#decisionMetricValue { color: #F1F6F8; font-family: Menlo, Consolas, monospace; font-size: 17px; font-weight: 700; }
#decisionSectionTitle, #macroSectionTitle { color: #E8F5F6; font-size: 14px; font-weight: 750; }
#decisionBodyText { color: #B9CBD4; }
#evidenceBullet { color: #35D0A0; font-size: 17px; }
#macroTitle { color: #F2F7FA; font-size: 23px; font-weight: 750; }
#macroAsOf { color: #8198A6; }
#macroScoreCard { background: #0B1B25; border: 1px solid #203946; border-radius: 8px; }
#macroScoreCard[tone="risk"] { border-color: #70444B; }
#macroCardTitle { color: #8FA6B3; font-size: 11px; font-weight: 700; }
#macroCardScore { color: #F2F7FA; font-size: 20px; font-weight: 800; }
#macroCardSummary { color: #AFC2CB; }
#macroProgress { background: #142631; border: 0; border-radius: 3px; max-height: 6px; }
#macroProgress::chunk { background: #35D0A0; border-radius: 3px; }
#macroConclusion { background: #0C2724; border: 1px solid #236754; border-radius: 8px; }
#macroBottleneck { background: #2A1C1E; border: 1px solid #70444B; border-radius: 8px; }
#macroConclusionText { color: #C8D8DE; }
#macroTheoryNote { background: #122A31; border-left: 3px solid #35D0A0; color: #B8D9DE; padding: 9px 12px; }
#macroAllocationControl { background: #0B1B25; border: 1px solid #294552; border-radius: 8px; }
#macroAllocationHero { background: #0C2724; border: 1px solid #28705C; border-radius: 9px; }
#macroAllocationSummary { color: #E9F7F3; font-size: 15px; font-weight: 700; }
#macroAllocationEquity { color: #73E2BE; font-family: Menlo, Consolas, monospace; font-size: 13px; font-weight: 700; }
#allocationStep { background: #0B1B25; border: 1px solid #203946; border-radius: 8px; }
#allocationStepIndex { color: #8FA6B3; font-size: 11px; font-weight: 700; }
#allocationStepAmount { color: #F1F6F8; font-family: Menlo, Consolas, monospace; font-size: 18px; font-weight: 800; }
#macroGuardrail { background: #122A31; border: 1px solid #245664; border-radius: 8px; }
#macroResearchDialog { background: #F6F7F5; color: #26312D; }
#macroResearchDialog QLabel { color: #3E4A45; }
#macroResearchDialog #macroTitle { color: #17201D; font-size: 22px; font-weight: 780; }
#macroResearchDialog #macroSectionTitle { color: #26342F; }
#macroResearchDialog #macroAsOf { color: #7A8781; }
#macroResearchDialog #macroTheoryNote { background: #EDF5F1; border-left: 3px solid #35B88D; color: #466158; }
#macroResearchDialog #macroDataBoundary { background: #FFFFFF; border: 1px solid #DCE3DF; border-radius: 9px; color: #5B6862; padding: 9px 12px; }
#macroResearchDialog #macroHistoryWarning { background: #FFF4E9; border-left: 3px solid #DD8842; color: #7B4828; padding: 9px 12px; }
#macroValidityHero { background: #FFF4E9; border: 1px solid #E9B77D; border-radius: 12px; }
#macroValidityHero[status="current"] { background: #EAF7F1; border-color: #8ACDB4; }
#macroValidityHero[status="unverified"] { background: #FFF7E0; border-color: #E4C66C; }
#macroResearchDialog #macroValidityEyebrow { color: #A45D2B; font-size: 10px; font-weight: 800; letter-spacing: 1px; }
#macroValidityHero[status="current"] #macroValidityEyebrow { color: #24745A; }
#macroResearchDialog #macroValidityTitle { color: #8F321F; font-size: 25px; font-weight: 820; }
#macroValidityHero[status="current"] #macroValidityTitle { color: #185E49; }
#macroResearchDialog #macroValidityReason { color: #684B3C; font-size: 13px; }
#macroResearchDialog #macroValidityMeta { color: #6C5C52; font-family: Menlo, Consolas, monospace; font-size: 11px; }
#macroResearchDialog #macroValidityCard { background: #FFFFFF; border: 1px solid #DDE4E0; border-radius: 10px; }
#macroResearchDialog #macroValidityValue { color: #1E2B26; font-size: 20px; font-weight: 800; }
#macroResearchDialog QTabWidget#macroResearchTabs::pane { background: #FFFFFF; border: 1px solid #DDE3DF; border-radius: 9px; }
#macroResearchDialog QTabWidget#macroResearchTabs QWidget { background: #FFFFFF; color: #34413C; }
#macroResearchDialog QTabWidget#macroResearchTabs QTabBar { background: #F6F7F5; }
#macroResearchDialog QTabWidget#macroResearchTabs QTabBar::tab { background: transparent; color: #7A8680; padding: 9px 14px; }
#macroResearchDialog QTabWidget#macroResearchTabs QTabBar::tab:selected { color: #176B52; border-bottom-color: #35B88D; }
#macroResearchDialog QTableWidget { background: #FFFFFF; alternate-background-color: #F7F9F7; color: #34413C; border: 1px solid #DCE3DF; gridline-color: #E8ECE9; }
#macroResearchDialog QHeaderView::section { background: #F0F3F1; color: #5E6B65; border-right: 1px solid #DDE3DF; }
#macroResearchDialog #macroAllocationControl, #macroResearchDialog #allocationStep { background: #F8FAF8; border-color: #DCE3DF; }
#macroResearchDialog #macroAllocationHero, #macroResearchDialog #macroConclusion { background: #EBF6F1; border-color: #A9D5C4; }
#macroResearchDialog #macroAllocationSummary, #macroResearchDialog #macroConclusionText { color: #33453E; }
#macroResearchDialog #macroAllocationEquity { color: #1F7B5E; }
#macroResearchDialog #macroBottleneck { background: #FFF3F1; border-color: #E5B5AE; }
#macroResearchDialog #macroGuardrail { background: #EEF4F1; border-color: #CBDDD5; }
#macroResearchDialog QComboBox, #macroResearchDialog QDoubleSpinBox { background: #FFFFFF; color: #31403A; border-color: #CBD6D0; }
#macroResearchDialog QComboBox:disabled, #macroResearchDialog QDoubleSpinBox:disabled { background: #EEF1EF; color: #929C97; border-color: #DDE3DF; }
#macroResearchDialog QDialogButtonBox QPushButton { background: #FFFFFF; color: #53605A; border-color: #D2DBD6; }
#marketContextPanel { background: #08141D; border: 1px solid #24404D; border-radius: 9px; }
#marketContextHeading { color: #F2F7FA; font-size: 15px; font-weight: 750; }
#marketContextSummary { background: #0B1B25; border: 1px solid #203946; border-radius: 7px; }
#marketContextTitle { color: #E8F5F6; font-size: 13px; font-weight: 750; }
#marketContextStatus { color: #9DB4BF; font-size: 11px; }
#confluenceBadge { background: #182731; border: 1px solid #405462; border-radius: 11px; color: #B5C7D0; padding: 5px 10px; font-weight: 700; }
#confluenceBadge[status="full_aligned"], #confluenceBadge[status="market_aligned_sector_pending"] { background: #0D2A24; border-color: #257B65; color: #73E2BE; }
#confluenceBadge[status="market_divergent"], #confluenceBadge[status="sector_divergent"], #confluenceBadge[status="market_unavailable"] { background: #2A1C1E; border-color: #70444B; color: #F39BA0; }
#contextError { background: #2A1C1E; border-left: 3px solid #F06B71; color: #F3B2B5; padding: 6px 9px; }
QSplitter::handle { background: #14242F; width: 5px; }
QScrollBar:vertical { background: #09141D; width: 10px; }
QScrollBar::handle:vertical { background: #2A3D49; border-radius: 5px; min-height: 26px; }
QStatusBar { color: #8399A8; border-top: 1px solid #182A35; }
#skillManagerTitle { color: #F2F7FA; font-size: 22px; font-weight: 750; }
#skillManagerNote, #skillManagerCount { color: #90A5B1; }
#skillManagerList, #skillManagerDetail { background: #0B1721; border: 1px solid #29404F; border-radius: 8px; color: #DCE6EC; padding: 8px; }
#skillManagerList::item { border-bottom: 1px solid #1C2E3A; padding: 10px 8px; }
#skillManagerList::item:selected { background: #12382F; color: #DDF9EF; border-radius: 6px; }
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EquiSeek 求衡智能投研平台桌面客户端")
    parser.add_argument("--smoke-test", action="store_true", help="渲染离线演示后自动退出")
    parser.add_argument("--screenshot", type=Path, help="冒烟模式下保存窗口截图")
    parser.add_argument(
        "--live-smoke-test",
        action="store_true",
        help="使用冻结应用执行 BaoStock 与可选 DeepSeek 真实链路后退出",
    )
    parser.add_argument(
        "--dependency-smoke-test",
        action="store_true",
        help="验证冻结应用可导入 BaoStock 及其运行依赖后退出",
    )
    parser.add_argument(
        "--gui-live-smoke-test",
        action="store_true",
        help="显示真实主窗口并触发开始分析按钮，完成后保存 GUI 验收证据",
    )
    parser.add_argument(
        "--backtest-gui-smoke-test",
        action="store_true",
        help="渲染可配置策略验证结果窗口并保存 GUI 验收证据",
    )
    parser.add_argument(
        "--macro-gui-smoke-test",
        action="store_true",
        help="渲染资本三流与成本转嫁宏观分析窗口并保存 GUI 验收证据",
    )
    parser.add_argument(
        "--advisor-gui-smoke-test",
        action="store_true",
        help="渲染求衡投研助手页面、执行本地证据回答并保存 GUI 验收证据",
    )
    parser.add_argument("--diagnostic-output", type=Path, help="真实链路自检结果 JSON 路径")
    return parser


def create_application(arguments: list[str] | None = None) -> QApplication:
    QCoreApplication.setOrganizationName("EquiSeek")
    QCoreApplication.setOrganizationDomain("equiseek.ai")
    QCoreApplication.setApplicationName("EquiSeek")
    app = QApplication(arguments if arguments is not None else sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    return app


def _render_smoke_result(window: MainWindow) -> None:
    window.source_combo.setCurrentIndex(window.source_combo.findData("demo"))
    end = date(2026, 8, 11)
    result = asyncio.run(
        run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            end - timedelta(days=2_500),
            end,
            AdjustmentMode.QFQ,
        )
    )
    window.render_result(result)
    window.summary_tabs.setCurrentWidget(window.decision_summary)


def _write_diagnostic(path: Path | None, payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path is None:
        print(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _run_live_smoke(output: Path | None) -> int:
    async def execute() -> dict[str, object]:
        end = date.today()
        key = CredentialStore().get_deepseek_api_key()
        model = DeepSeekClient(DeepSeekConfig(api_key=key)) if key else None
        provider = market_data_provider("baostock", None)
        try:
            result = await run_research(
                provider,
                "600519.SH",
                end - timedelta(days=2_500),
                end,
                AdjustmentMode.QFQ,
                model,
            )
        finally:
            if model is not None:
                await model.close()
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        return {
            "status": result.plan.get("status") if result.plan else None,
            "source": result.data.source,
            "symbol": result.data.symbol,
            "bars": len(result.data.bars),
            "as_of": result.data.as_of.isoformat(),
            "deepseek_configured": bool(key),
            "deepseek_model": model.config.model if model is not None else None,
            "model_summary_present": bool(result.model_summary),
            "model_warning": result.model_warning,
            "investment_action": result.investment_advice.action.value,
            "advice_confidence": result.investment_advice.confidence,
            "market_context_status": result.market_context.status,
            "benchmark_symbol": result.market_context.benchmark.instrument.symbol,
            "benchmark_available": result.market_context.benchmark.available,
            "cache_status": result.data.cache_status,
            "cache_hit_bars": result.data.cache_hit_bars,
            "cache_added_bars": result.data.cache_added_bars,
            "network_rows": result.data.network_rows,
            "data_lineage": {
                "source": result.data.source,
                "symbol": result.data.symbol,
                "adjustment": result.data.adjustment.value,
            },
            "forecast_directions": [
                forecast.direction for forecast in result.investment_advice.forecasts
            ],
        }

    try:
        payload = asyncio.run(execute())
    except Exception as error:
        _write_diagnostic(
            output,
            {"status": "failed", "error_type": type(error).__name__, "message": str(error)},
        )
        return 2
    _write_diagnostic(output, payload)
    return 0


def _run_dependency_smoke(output: Path | None) -> int:
    try:
        provider = BaoStockProvider()
        version = str(getattr(provider.api, "__version__", "unknown"))
    except Exception as error:
        _write_diagnostic(
            output,
            {"status": "failed", "error_type": type(error).__name__, "message": str(error)},
        )
        return 2
    _write_diagnostic(
        output,
        {"status": "succeeded", "baostock_imported": True, "baostock_version": version},
    )
    return 0


def _save_gui_screenshots(
    app: QApplication, window: MainWindow, screenshot: Path | None
) -> dict[str, str]:
    if screenshot is None:
        return {}
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    market_path = screenshot.with_name(f"{screenshot.stem}-market{screenshot.suffix}")
    window.market_context_widget.tabs.setCurrentIndex(0)
    app.processEvents()
    if not window.grab().save(str(market_path)):
        raise RuntimeError(f"无法保存 GUI 截图：{market_path}")
    paths["market"] = str(market_path)
    if window._last_result is not None and window._last_result.market_context.sector is not None:
        sector_path = screenshot.with_name(f"{screenshot.stem}-sector{screenshot.suffix}")
        window.market_context_widget.tabs.setCurrentIndex(1)
        app.processEvents()
        if not window.grab().save(str(sector_path)):
            raise RuntimeError(f"无法保存 GUI 截图：{sector_path}")
        paths["sector"] = str(sector_path)
    for name, widget in (
        ("decision", window.decision_summary),
        ("facts", window.fact_summary),
        ("ai", window.ai_summary),
        ("strategy", window.strategy_summary),
        ("plan", window.plan_summary),
    ):
        path = screenshot.with_name(f"{screenshot.stem}-{name}{screenshot.suffix}")
        window.summary_tabs.setCurrentWidget(widget)
        app.processEvents()
        if not window.grab().save(str(path)):
            raise RuntimeError(f"无法保存 GUI 截图：{path}")
        paths[name] = str(path)
    return paths


def _run_gui_live_smoke(
    app: QApplication,
    window: MainWindow,
    screenshot: Path | None,
    output: Path | None,
) -> int:
    original_chart_settings = {
        key: (window.settings.contains(key), window.settings.value(key))
        for key in ("chart/timeframe", "chart/indicators")
    }
    source_index = window.source_combo.findData("baostock")
    window.source_combo.setCurrentIndex(source_index)
    window.symbol_input.setText("600519.SH")
    window.settings.setValue("research/use_ai", True)
    window.timeframe_combo.setCurrentIndex(window.timeframe_combo.findData(Timeframe.MONTHLY.value))
    window.indicator_selector.set_selected(("MACD", "RSI", "WR"), emit=True)
    window._refresh_ai_status()
    window.show()
    window.raise_()
    window.activateWindow()
    app.processEvents()

    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    timeout.setSingleShot(True)
    state = {"exit_code": 2, "finished": False, "sector_requested": False}

    def finish(payload: dict[str, object], exit_code: int) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        poll.stop()
        timeout.stop()
        try:
            payload["screenshots"] = _save_gui_screenshots(app, window, screenshot)
        except Exception as error:
            payload["screenshot_error"] = str(error)
            exit_code = 2
        for key, (existed, value) in original_chart_settings.items():
            if existed:
                window.settings.setValue(key, value)
            else:
                window.settings.remove(key)
        _write_diagnostic(output, payload)
        state["exit_code"] = exit_code
        loop.quit()

    def inspect_result() -> None:
        result = window._last_result
        if result is None:
            if not window._tasks and window.statusBar().currentMessage() == "分析失败":
                finish(
                    {
                        "status": "failed",
                        "window_visible": window.isVisible(),
                        "message": window.statusBar().currentMessage(),
                    },
                    2,
                )
            return
        if result.market_context.sector is None and not state["sector_requested"]:
            for index in range(window.market_context_widget.sector_combo.count()):
                instrument = window.market_context_widget.sector_combo.itemData(index)
                if getattr(instrument, "symbol", None) == "000932.SH":
                    window.market_context_widget.sector_combo.setCurrentIndex(index)
                    state["sector_requested"] = True
                    window.market_context_widget.load_sector_button.click()
                    return
            finish({"status": "failed", "message": "消费板块代理未注册"}, 2)
            return
        if state["sector_requested"] and result.market_context.sector is None:
            if not window._sector_tasks and window.market_context_widget.error_label.isVisible():
                finish(
                    {
                        "status": "failed",
                        "message": window.market_context_widget.error_label.text(),
                    },
                    2,
                )
            return
        plan_text = window.plan_summary.toPlainText()
        evidence_top = window.market_evidence_panel.mapTo(window.research_page, QPoint()).y()
        decision_top = window.decision_panel.mapTo(window.research_page, QPoint()).y()
        payload: dict[str, object] = {
            "status": result.plan.get("status") if result.plan else None,
            "window_visible": window.isVisible(),
            "button_enabled": window.analyze_button.isEnabled(),
            "source": result.data.source,
            "symbol": result.data.symbol,
            "bars": len(result.data.bars),
            "as_of": result.data.as_of.isoformat(),
            "fact_summary_length": len(window.fact_summary.toPlainText()),
            "model_summary_present": bool(result.model_summary),
            "model_call_completed": bool(result.model_summary or result.model_warning),
            "ai_summary_length": len(window.ai_summary.toPlainText()),
            "model_warning": result.model_warning,
            "selected_model": window._deepseek_model(),
            "decision_panel_is_right": (
                window.research_splitter.widget(1) is window.decision_panel
            ),
            "decision_panel_top_aligned": decision_top == evidence_top,
            "decision_panel_width": window.decision_panel.width(),
            "plan_ui_succeeded": "计划状态：succeeded" in plan_text,
            "strategy_ui_present": bool(window.strategy_summary.toPlainText()),
            "decision_ui_present": all(
                item in window.decision_summary.toPlainText()
                for item in ("建议动作", "方向预测", "失效条件")
            ),
            "investment_action": result.investment_advice.action.value,
            "advice_confidence": result.investment_advice.confidence,
            "market_context_status": result.market_context.status,
            "benchmark_symbol": result.market_context.benchmark.instrument.symbol,
            "benchmark_available": result.market_context.benchmark.available,
            "benchmark_chart_visible": (
                window.market_context_widget.benchmark_pane.price_chart.chart_data is not None
            ),
            "benchmark_macd_visible": (
                window.market_context_widget.benchmark_pane.macd_chart.chart_data is not None
            ),
            "benchmark_wr_visible": (
                window.market_context_widget.benchmark_pane.wr_chart.chart_data is not None
            ),
            "sector_symbol": (
                result.market_context.sector.instrument.symbol
                if result.market_context.sector is not None
                else None
            ),
            "sector_available": (
                result.market_context.sector.available
                if result.market_context.sector is not None
                else False
            ),
            "sector_chart_visible": (
                window.market_context_widget.sector_pane.price_chart.chart_data is not None
            ),
            "cache_status": result.data.cache_status,
            "cache_hit_bars": result.data.cache_hit_bars,
            "cache_added_bars": result.data.cache_added_bars,
            "network_rows": result.data.network_rows,
            "forecast_directions": [
                forecast.direction for forecast in result.investment_advice.forecasts
            ],
            "metrics_rendered": all(card.number.text() != "—" for card in window.metrics.values()),
            "chart_timeframe": (
                window.chart_data.timeframe.value if window.chart_data is not None else None
            ),
            "chart_bars": len(window.chart_data.bars) if window.chart_data is not None else 0,
            "chart_latest_complete": (
                window.chart_data.latest_complete if window.chart_data is not None else None
            ),
            "selected_indicators": list(window.indicator_selector.selected_indicators),
            "visible_indicator_charts": sum(
                not chart.isHidden() for chart in window.indicator_charts
            ),
        }
        passed = all(
            (
                payload["status"] == "succeeded",
                payload["window_visible"],
                payload["button_enabled"],
                # A remote response rejected by the investment-output guard is a
                # successful safe fallback, not a broken DeepSeek connection.
                payload["model_call_completed"],
                payload["decision_panel_is_right"],
                payload["decision_panel_top_aligned"],
                payload["plan_ui_succeeded"],
                payload["strategy_ui_present"],
                payload["decision_ui_present"],
                payload["benchmark_available"],
                payload["benchmark_chart_visible"],
                payload["benchmark_macd_visible"],
                payload["benchmark_wr_visible"],
                payload["sector_symbol"] == "000932.SH",
                payload["sector_available"],
                payload["sector_chart_visible"],
                payload["metrics_rendered"],
                payload["chart_timeframe"] == Timeframe.MONTHLY.value,
                payload["selected_indicators"] == ["MACD", "RSI", "WR"],
                payload["visible_indicator_charts"] == 3,
            )
        )
        finish(payload, 0 if passed else 2)

    def timed_out() -> None:
        finish(
            {
                "status": "timeout",
                "window_visible": window.isVisible(),
                "button_enabled": window.analyze_button.isEnabled(),
                "message": window.statusBar().currentMessage(),
            },
            2,
        )

    poll.timeout.connect(inspect_result)
    timeout.timeout.connect(timed_out)
    poll.start(200)
    timeout.start(120_000)
    QTimer.singleShot(500, window.analyze_button.click)
    loop.exec()
    window.close()
    app.processEvents()
    return int(state["exit_code"])


def _run_backtest_gui_smoke(
    app: QApplication,
    window: MainWindow,
    screenshot: Path | None,
    output: Path | None,
) -> int:
    try:
        end = date(2026, 8, 11)
        data = DemoMarketDataProvider().fetch_daily(
            "600519.SH",
            date(2018, 1, 1),
            end,
            AdjustmentMode.QFQ,
        )
        report = walk_forward_backtest(
            data.bars,
            date(2025, 8, 1),
            date(2026, 8, 1),
            symbol=data.symbol,
        )
        dialog = BacktestDialog(report, window)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        app.processEvents()
        screenshot_saved = True
        screenshots: dict[str, str] = {}
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            dialog.tabs.setCurrentIndex(0)
            app.processEvents()
            screenshot_saved = dialog.grab().save(str(screenshot))
            screenshots["summary"] = str(screenshot)
            for index, name in ((1, "statistics"), (2, "trades"), (3, "signals")):
                path = screenshot.with_name(f"{screenshot.stem}-{name}{screenshot.suffix}")
                dialog.tabs.setCurrentIndex(index)
                app.processEvents()
                screenshot_saved = dialog.grab().save(str(path)) and screenshot_saved
                screenshots[name] = str(path)
        payload: dict[str, object] = {
            "status": "succeeded" if screenshot_saved else "failed",
            "window_visible": dialog.isVisible(),
            "symbol": report.symbol,
            "signals": len(report.signals),
            "trades": len(report.trades),
            "statistics_rows": dialog.statistics_table.rowCount(),
            "tabs": dialog.tabs.count(),
            "lookahead_safe": report.lookahead_safe,
            "report_sha256": backtest_report_digest(report),
            "screenshots": screenshots,
        }
        passed = all(
            (
                payload["status"] == "succeeded",
                payload["window_visible"],
                payload["statistics_rows"] == 6,
                payload["tabs"] == 4,
                payload["lookahead_safe"],
            )
        )
        _write_diagnostic(output, payload)
        dialog.close()
        window.close()
        app.processEvents()
        return 0 if passed else 2
    except Exception as error:
        _write_diagnostic(
            output,
            {"status": "failed", "error_type": type(error).__name__, "message": str(error)},
        )
        window.close()
        return 2


def _run_macro_gui_smoke(
    app: QApplication,
    window: MainWindow,
    screenshot: Path | None,
    output: Path | None,
) -> int:
    try:
        result = asyncio.run(run_macro_research(BundledOfficialMacroProvider()))
        dialog = MacroDialog(result, window)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        app.processEvents()
        screenshots: dict[str, str] = {}
        screenshot_saved = True
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            for index, name in enumerate(
                (
                    "freshness",
                    "allocation",
                    "dashboard",
                    "capital-flows",
                    "cost-chains",
                    "sectors",
                    "sources",
                    "plan",
                )
            ):
                path = screenshot.with_name(f"{screenshot.stem}-{name}{screenshot.suffix}")
                dialog.tabs.setCurrentIndex(index)
                app.processEvents()
                screenshot_saved = dialog.grab().save(str(path)) and screenshot_saved
                screenshots[name] = str(path)
        capital_flow_paths = dialog.paths_table.rowCount()
        cost_transfer_chains = dialog.chains_table.rowCount()
        default_allocation = next(
            plan
            for plan in result.analysis.investment_view.allocation_plans
            if plan.profile == result.analysis.investment_view.default_allocation_profile
        )
        allocation_total = sum(target.target_pct for target in default_allocation.targets)
        payload: dict[str, object] = {
            "status": "succeeded" if screenshot_saved else "failed",
            "window_visible": dialog.isVisible(),
            "tabs": dialog.tabs.count(),
            "allocation_rows": dialog.allocation_table.rowCount(),
            "allocation_profile": dialog.profile_combo.currentData(),
            "allocation_total": allocation_total,
            "sector_rows": dialog.sectors_table.rowCount(),
            "capital_flow_paths": capital_flow_paths,
            "cost_transfer_chains": cost_transfer_chains,
            "source_rows": dialog.sources_table.rowCount(),
            "plan_succeeded": "计划状态：succeeded" in dialog.plan_view.toPlainText(),
            "risk_appetite": result.analysis.investment_view.risk_appetite_label,
            "validity": result.validity.status,
            "current_decision_allowed": result.validity.current_decision_allowed,
            "freshness_sources": dialog.freshness_table.rowCount(),
            "screenshots": screenshots,
        }
        passed = all(
            (
                payload["status"] == "succeeded",
                payload["window_visible"],
                payload["tabs"] == 8,
                payload["allocation_rows"] == 7,
                payload["allocation_profile"] == "balanced",
                allocation_total == 100,
                payload["sector_rows"] == 5,
                capital_flow_paths >= 5,
                cost_transfer_chains >= 6,
                payload["source_rows"] == 37,
                payload["freshness_sources"] == 4,
                payload["current_decision_allowed"] is False,
                payload["plan_succeeded"],
            )
        )
        _write_diagnostic(output, payload)
        dialog.close()
        window.close()
        app.processEvents()
        return 0 if passed else 2
    except Exception as error:
        _write_diagnostic(
            output,
            {"status": "failed", "error_type": type(error).__name__, "message": str(error)},
        )
        window.close()
        return 2


def _run_advisor_gui_smoke(
    app: QApplication,
    window: MainWindow,
    screenshot: Path | None,
    output: Path | None,
) -> int:
    original_ai_setting = (
        window.settings.contains("research/use_ai"),
        window.settings.value("research/use_ai"),
    )
    try:
        end = date(2026, 8, 11)
        position = Position(
            "600519.SH",
            100,
            125.5,
            name="贵州茅台",
            opened_on=end - timedelta(days=180),
            industry="主要消费",
        )
        result = asyncio.run(
            run_research(
                DemoMarketDataProvider(),
                position.symbol,
                end - timedelta(days=2_500),
                end,
                AdjustmentMode.QFQ,
                position=position,
            )
        )
        window.settings.setValue("research/use_ai", False)
        window.render_result(result)
        window.workspace_tabs.setCurrentWidget(window.advisor_widget)
        window.advisor_widget.set_positions((position,), position.symbol)
        window.advisor_widget.set_evidence(build_advisor_evidence(result, position))
        agent_question = f"研究 {position.symbol} 什么时候可以买入，说明完整判断流程"
        window.advisor_widget.append_user(agent_question)
        progress: list[dict[str, object]] = []
        agent_result = asyncio.run(
            InvestmentAgentRuntime(
                window.portfolio_repository.path.parent / "smoke-agent-workspaces",
                window._skill_workspace,
            ).run(
                InvestmentAgentRunRequest(
                    question=agent_question,
                    intent="analyze_security",
                    thread_id="smoke-investment-agent",
                    portfolio=PortfolioBook(positions=(position,)),
                    source="demo",
                    start_date=end - timedelta(days=2_500),
                    end_date=end,
                    adjustment=AdjustmentMode.QFQ,
                    symbol=position.symbol,
                    memory=window._conversation_store.load_memory(),
                    active_skills=window._skill_workspace.select_for_turn(
                        agent_question,
                        defaults=("investment-decision-engine",),
                    ).packages,
                ),
                on_progress=progress.append,
            )
        )
        for event in progress:
            window.advisor_widget.update_agent_progress(event)
        window.advisor_widget.finish_agent_run(agent_result)
        window.advisor_widget.append_answer(
            AdvisorAnswer(agent_result.answer, agent_result.answer_mode, agent_result.warning)
        )
        window.show()
        window.raise_()
        window.activateWindow()
        app.processEvents()
        screenshot_saved = True
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            screenshot_saved = window.grab().save(str(screenshot))
        transcript = window.advisor_widget.transcript.toPlainText()
        payload: dict[str, object] = {
            "status": "succeeded" if screenshot_saved else "failed",
            "window_visible": window.isVisible(),
            "workspace_tabs": window.workspace_tabs.count(),
            "selected_page": window.workspace_tabs.tabText(window.workspace_tabs.currentIndex()),
            "symbol": position.symbol,
            "as_of": result.data.as_of.isoformat(),
            "rule_action": result.investment_advice.action_label,
            "turns": len(window.advisor_widget.turns),
            "local_answer": "本地 Agent" in transcript,
            "macd_visible": "MACD" in transcript,
            "wr_visible": "WR" in transcript,
            "privacy_notice_visible": "持仓数量" in window.advisor_widget.privacy_notice.text()
            and "不提供主机 Shell" in window.advisor_widget.privacy_notice.text(),
            "task_rows": window.advisor_widget.task_list.count(),
            "artifact_rows": window.advisor_widget.artifact_list.count(),
            "plan_rows": window.advisor_widget.agent_list.count(),
            "plan_stats": window.advisor_widget.plan_stats.text(),
            "html_artifact": any(
                artifact.name == "investment-agent-report.html"
                and artifact.media_type == "text/html"
                for artifact in agent_result.artifacts
            ),
            "trace_artifact": any(
                artifact.name == "investment-agent-trace.md" for artifact in agent_result.artifacts
            ),
            "skill_selector_visible": window.advisor_widget.skill_combo.isVisible(),
            "workspace_selector_visible": window.advisor_widget.workspace_button.isVisible(),
            "agent_splitter_visible": window.advisor_widget.body_splitter.isVisible(),
            "screenshot": str(screenshot) if screenshot is not None else "",
        }
        passed = all(
            (
                payload["status"] == "succeeded",
                payload["window_visible"],
                payload["workspace_tabs"] == 2,
                payload["selected_page"] == "求衡投研助手",
                payload["turns"] == 2,
                payload["local_answer"],
                payload["macd_visible"],
                payload["wr_visible"],
                payload["privacy_notice_visible"],
                window.advisor_widget.task_list.count() >= 1,
                window.advisor_widget.artifact_list.count() >= 1,
                window.advisor_widget.agent_list.count() >= 1,
                payload["html_artifact"],
                payload["trace_artifact"],
                payload["skill_selector_visible"],
                payload["workspace_selector_visible"],
                payload["agent_splitter_visible"],
            )
        )
        _write_diagnostic(output, payload)
        return 0 if passed else 2
    except Exception as error:
        _write_diagnostic(
            output,
            {"status": "failed", "error_type": type(error).__name__, "message": str(error)},
        )
        return 2
    finally:
        existed, value = original_ai_setting
        if existed:
            window.settings.setValue("research/use_ai", value)
        else:
            window.settings.remove("research/use_ai")
        window.close()
        app.processEvents()


def main() -> int:
    parser = build_parser()
    options, qt_arguments = parser.parse_known_args()
    if options.dependency_smoke_test:
        return _run_dependency_smoke(options.diagnostic_output)
    if options.live_smoke_test:
        return _run_live_smoke(options.diagnostic_output)
    if os.getenv("QT_QPA_PLATFORM") is None and options.smoke_test and sys.platform != "darwin":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = create_application([sys.argv[0], *qt_arguments])
    window = MainWindow(
        settings=QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "AegisRun", "AegisRun Research"
        )
    )
    window.show()
    if options.gui_live_smoke_test:
        return _run_gui_live_smoke(
            app,
            window,
            options.screenshot,
            options.diagnostic_output,
        )
    if options.backtest_gui_smoke_test:
        return _run_backtest_gui_smoke(
            app,
            window,
            options.screenshot,
            options.diagnostic_output,
        )
    if options.macro_gui_smoke_test:
        return _run_macro_gui_smoke(
            app,
            window,
            options.screenshot,
            options.diagnostic_output,
        )
    if options.advisor_gui_smoke_test:
        return _run_advisor_gui_smoke(
            app,
            window,
            options.screenshot,
            options.diagnostic_output,
        )
    if options.smoke_test:
        _render_smoke_result(window)
        app.processEvents()
        if options.screenshot:
            options.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(options.screenshot)):
                return 2
        window.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
