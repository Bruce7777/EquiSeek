from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTabWidget,
)

from aegisrun.agents.investment_runtime import (  # noqa: E402
    InvestmentAgentArtifact,
    InvestmentAgentRunResult,
    InvestmentAgentTraceStep,
)
from aegisrun.desktop.advisor_widget import HoldingAdvisorWidget  # noqa: E402
from aegisrun.desktop.app import build_parser  # noqa: E402
from aegisrun.desktop.credentials import CredentialStore  # noqa: E402
from aegisrun.desktop.macro_dialog import MacroDialog  # noqa: E402
from aegisrun.desktop.main_window import MainWindow  # noqa: E402
from aegisrun.desktop.portfolio_dialog import (  # noqa: E402
    BacktestConfigDialog,
    BacktestDialog,
    CandidateResultsDialog,
    PortfolioDialog,
)
from aegisrun.desktop.settings_dialog import SettingsDialog  # noqa: E402
from aegisrun.desktop.skill_manager_dialog import SkillManagerDialog  # noqa: E402
from aegisrun.desktop.workers import (  # noqa: E402
    AdvisorChatRequest,
    AdvisorChatTask,
    ResearchRequest,
    ResearchTask,
    SectorContextRequest,
    SectorContextTask,
)
from aegisrun.macro.freshness import (  # noqa: E402
    MacroSourceCheck,
    OfficialMacroFreshnessVerifier,
    assess_macro_freshness,
)
from aegisrun.macro.pipeline import run_macro_research  # noqa: E402
from aegisrun.macro.providers import BundledOfficialMacroProvider  # noqa: E402
from aegisrun.marketdata.models import AdjustmentMode, PriceBar  # noqa: E402
from aegisrun.marketdata.providers import DemoMarketDataProvider  # noqa: E402
from aegisrun.marketdata.timeframes import Timeframe  # noqa: E402
from aegisrun.portfolio.models import Position, WatchItem  # noqa: E402
from aegisrun.portfolio.repository import PortfolioRepository  # noqa: E402
from aegisrun.research.advisor_chat import AdvisorAnswer  # noqa: E402
from aegisrun.research.backtest import walk_forward_backtest  # noqa: E402
from aegisrun.research.deepseek import DEEPSEEK_V4_FLASH, DEEPSEEK_V4_PRO  # noqa: E402
from aegisrun.research.market_context import load_market_trend, sector_proxy_for  # noqa: E402
from aegisrun.research.service import attach_sector_context, run_research  # noqa: E402
from aegisrun.research.signals import (  # noqa: E402
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)


class EmptyKeyring:
    @staticmethod
    def get_password(service: str, name: str) -> None:
        return None


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_for_advisor_tasks(window: MainWindow, timeout_ms: int = 15_000) -> None:
    app = application()
    elapsed = 0
    while window._advisor_tasks and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    app.processEvents()
    assert not window._advisor_tasks


def test_investment_agent_interaction_controls_are_progressive_and_copyable(
    tmp_path,
) -> None:
    app = application()
    widget = HoldingAdvisorWidget()
    widget.resize(1280, 760)
    widget.show()
    app.processEvents()

    widget.set_available_skills(
        (
            SimpleNamespace(
                name="my-entry-skill",
                provider="user-local",
                version="1.2.0",
                description="用户自己的投资研究入口",
            ),
        )
    )
    widget.skill_combo.setCurrentIndex(widget.skill_combo.findData("my-entry-skill"))
    widget.set_workspace_root(str(tmp_path), is_default=False)
    assert "已指定 my-entry-skill" in widget.composer_scope.text()
    assert tmp_path.name in widget.composer_scope.text()

    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"source":"unit-test"}', encoding="utf-8")
    report = tmp_path / "investment-agent-report.md"
    report.write_text("# report", encoding="utf-8")
    result = InvestmentAgentRunResult(
        run_id="run-progressive-ui",
        status="succeeded",
        answer="等待买入触发条件。",
        answer_mode="local",
        workspace=str(tmp_path),
        artifacts=(
            InvestmentAgentArtifact(
                name=report.name,
                path=str(report),
                media_type="text/markdown",
                size_bytes=report.stat().st_size,
            ),
        ),
        active_skills=({"name": "my-entry-skill", "provider": "user-local", "version": "1.2.0"},),
        tool_calls=("market.analyze",),
        trace=(
            InvestmentAgentTraceStep("goal", "理解目标", "succeeded", "识别买入时机"),
            InvestmentAgentTraceStep(
                "skill",
                "加载 Skill",
                "succeeded",
                "使用用户 Skill",
                ("my-entry-skill",),
            ),
            InvestmentAgentTraceStep(
                "evidence",
                "读取行情",
                "succeeded",
                "BaoStock 前复权日线",
                ("my-entry-skill",),
                "market.analyze",
                str(evidence),
            ),
            InvestmentAgentTraceStep(
                "research-task",
                "计算 WR",
                "succeeded",
                "WR10 为中性区",
                agent_name="indicator-agent",
                depends_on=("market-data",),
            ),
            InvestmentAgentTraceStep("decision-gate", "检查共振", "passed", "多周期尚未形成共振"),
        ),
    )
    widget.finish_agent_run(result)
    app.processEvents()

    assert "5 个任务" in widget.plan_stats.text()
    assert "indicator-agent" in widget.agent_list.item(3).text()
    assert "依赖 market-data" in widget.agent_list.item(3).text()
    assert widget.task_list.count() == 5
    assert widget.copy_trace_button.isEnabled()
    decision_index = widget.trace_filter_combo.findData("decision")
    widget.trace_filter_combo.setCurrentIndex(decision_index)
    assert widget.task_list.count() == 1
    assert "检查共振" in widget.task_list.item(0).text()

    tools_index = widget.trace_filter_combo.findData("tools")
    widget.trace_filter_combo.setCurrentIndex(tools_index)
    assert widget.task_list.count() == 1
    assert widget.open_evidence_button.isEnabled()

    widget.copy_trace_button.click()
    assert "理解目标" in QApplication.clipboard().text()
    assert "检查共振" in QApplication.clipboard().text()

    widget.append_answer(AdvisorAnswer("最后回答正文", "local"))
    widget.copy_answer_button.click()
    assert "最后回答正文" in QApplication.clipboard().text()

    assert widget.run_panel.isVisible()
    widget.details_button.click()
    assert widget.run_panel.isHidden()
    assert widget.details_button.text() == "显示详情"
    widget.details_button.click()
    assert widget.run_panel.isVisible()

    widget.clear_conversation(notify=False)
    assert widget.task_list.count() == 0
    assert widget.artifact_list.count() == 0
    assert widget.trace_badge.text() == "尚未运行"
    assert widget.copy_trace_button.isEnabled() is False
    assert widget.copy_answer_button.isEnabled() is False
    widget.close()


def test_skill_manager_exposes_local_sources_and_invocation(tmp_path) -> None:
    application()
    user_root = tmp_path / "skills"
    package_root = user_root / "my-skill"
    package_root.mkdir(parents=True)
    skill = SimpleNamespace(
        name="my-skill",
        provider="user-local",
        version="1.2.0",
        description="本地用户研究 Skill",
        allowed_agents=("advice-agent",),
        allowed_tools=(),
        network_required=False,
        manifest_sha256="ab" * 32,
        package_root=package_root,
    )

    dialog = SkillManagerDialog((skill,), user_root)

    assert dialog.skill_list.count() == 1
    assert "我的 Skill" in dialog.skill_list.item(0).text()
    assert "完全本地" in dialog.findChild(QLabel, "skillManagerNote").text()
    assert "1 个用户 Skill" in dialog.count_label.text()
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == "/my-skill "
    dialog.close()


def test_desktop_parser_supports_frozen_live_smoke() -> None:
    options = build_parser().parse_args(
        [
            "--live-smoke-test",
            "--dependency-smoke-test",
            "--backtest-gui-smoke-test",
            "--macro-gui-smoke-test",
            "--advisor-gui-smoke-test",
            "--diagnostic-output",
            "build/live-smoke.json",
        ]
    )

    assert options.live_smoke_test is True
    assert options.dependency_smoke_test is True
    assert options.backtest_gui_smoke_test is True
    assert options.macro_gui_smoke_test is True
    assert options.advisor_gui_smoke_test is True
    assert str(options.diagnostic_output) == "build/live-smoke.json"


def test_main_window_offers_all_data_sources_and_renders_synthetic_warning(tmp_path) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", False)
    portfolio = PortfolioRepository(tmp_path / "portfolio.json")
    position = Position("600519.SH", 10, 100, industry="主要消费")
    portfolio.upsert_position(position)
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, portfolio)
    window.resize(1500, 907)
    window.show()
    app.processEvents()

    source_keys = [
        window.source_combo.itemData(index) for index in range(window.source_combo.count())
    ]
    assert source_keys == ["baostock", "tushare", "demo"]

    end = date(2026, 8, 11)
    result = asyncio.run(
        run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            end - timedelta(days=420),
            end,
            AdjustmentMode.QFQ,
            position=position,
        )
    )
    window.source_combo.setCurrentIndex(2)
    window.render_result(result)
    app.processEvents()

    assert window.metrics["收盘价"].number.text() != "—"
    assert "非真实行情" in window.source_notice.text()
    assert "投资动作与方向预测" in window.fact_summary.toPlainText()
    assert "建议动作" in window.decision_summary.toPlainText()
    assert window.decision_summary.objectName() == "investmentDecision"
    assert window.decision_summary.decision_path_table.rowCount() == 5
    assert window.decision_summary.timeframe_table.rowCount() == 3
    assert window.summary_tabs.currentWidget() is window.decision_summary
    assert "多周期技术结构" in window.strategy_summary.toPlainText()
    assert "本地持仓状态" in window.strategy_summary.toPlainText()
    assert window.backtest_button.isEnabled()
    assert "计划状态：succeeded" in window.plan_summary.toPlainText()
    assert "隔离工作区" in window.plan_summary.toPlainText()
    assert "market-data-agent" in window.plan_summary.toPlainText()
    assert "market-context-agent" in window.plan_summary.toPlainText()
    assert "a-share-market-data" in window.plan_summary.toPlainText()
    assert "Agent Runtime：local" in window.plan_summary.toPlainText()
    assert window.ai_summary.toPlainText().startswith("本次未启用")
    assert window.market_context_widget.tabs.count() == 2
    assert window.workspace_tabs.count() == 2
    assert window.workspace_tabs.tabText(0) == "个股研究"
    assert window.workspace_tabs.tabText(1) == "投研助手"
    assert window.advisor_widget.position_combo.count() == 2
    assert window.advisor_widget.position_combo.currentData() == "600519.SH"
    assert window.advisor_widget.evidence is not None
    assert window.advisor_widget.send_button.isEnabled()
    window.advisor_widget.set_busy(True)
    assert window.advisor_widget.position_combo.isEnabled() is False
    assert window.advisor_widget.analyze_button.isEnabled() is False
    window.advisor_widget.set_busy(False)
    assert window.market_context_widget.tabs.currentIndex() == 0
    assert window.market_context_widget.benchmark_pane.price_chart.chart_data is not None
    assert window.market_context_widget.benchmark_pane.macd_chart.chart_data is not None
    assert window.market_context_widget.benchmark_pane.wr_chart.chart_data is not None
    assert result.market_context.benchmark.instrument.symbol == "000001.SH"
    table = window.market_context_widget.benchmark_pane.timeframe_table
    assert table.rowCount() == 2
    assert table.columnCount() == 3
    assert table.height() >= 51
    assert table.visualItemRect(table.item(1, 2)).bottom() <= table.viewport().rect().bottom()
    table_bottom = table.mapTo(window.market_context_widget, table.rect().bottomLeft()).y()
    assert table_bottom <= window.market_context_widget.contentsRect().bottom()
    assert "大盘" in window.strategy_summary.toPlainText()

    assert window.timeframe_combo.currentData() == Timeframe.DAILY.value
    assert window.indicator_selector.selected_indicators == ("MA", "MACD", "WR")
    window.timeframe_combo.setCurrentIndex(window.timeframe_combo.findData(Timeframe.MONTHLY.value))
    window.indicator_selector.set_selected(("MACD", "RSI", "WR"), emit=True)
    app.processEvents()

    assert window.chart_data is not None
    assert window.chart_data.timeframe is Timeframe.MONTHLY
    assert window.metrics["日涨跌"].title.text() == "月线涨跌"
    assert [chart.mode for chart in window.indicator_charts if not chart.isHidden()] == [
        "MACD",
        "RSI",
        "WR",
    ]
    assert settings.value("chart/timeframe") == Timeframe.MONTHLY.value
    assert settings.value("chart/indicators") == "MACD,RSI,WR"

    window.open_advisor()
    window.advisor_widget.question_input.setPlainText("我现在应该卖出吗？")
    window.advisor_widget.send_button.click()
    wait_for_advisor_tasks(window)

    assert window.workspace_tabs.currentWidget() is window.advisor_widget
    assert [turn.role for turn in window.advisor_widget.turns] == ["user", "assistant"]
    assert result.investment_advice.action_label in window.advisor_widget.transcript.toPlainText()
    assert "MACD" in window.advisor_widget.transcript.toPlainText()
    assert "WR" in window.advisor_widget.transcript.toPlainText()

    original_evidence = window.advisor_widget.evidence
    assert original_evidence is not None
    stale_task = AdvisorChatTask(
        AdvisorChatRequest(original_evidence, (), "旧问题", deepseek_api_key=None)
    )
    window.advisor_widget.set_evidence(
        replace(original_evidence, as_of="2026-08-12", rule_action="更新动作")
    )
    window.advisor_widget.set_busy(True)
    window._advisor_succeeded(stale_task, AdvisorAnswer("旧回答", "local"))
    assert window.advisor_widget.turns == ()
    assert window.advisor_widget.send_button.isEnabled()
    window.close()

    restored = MainWindow(CredentialStore(EmptyKeyring()), settings, portfolio)
    assert restored.timeframe_combo.currentData() == Timeframe.MONTHLY.value
    assert restored.indicator_selector.selected_indicators == ("MACD", "RSI", "WR")
    restored.close()


def test_research_workspace_keeps_investment_conclusion_in_right_column(tmp_path) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, repository)
    window.resize(1500, 1040)
    window.show()
    app.processEvents()

    assert window.research_splitter.orientation() is Qt.Orientation.Horizontal
    assert window.research_splitter.widget(0) is window.market_evidence_panel
    assert window.research_splitter.widget(1) is window.decision_panel
    assert window.summary_tabs.parentWidget() is window.decision_panel

    evidence_top = window.market_evidence_panel.mapTo(window.research_page, QPoint()).y()
    decision_top = window.decision_panel.mapTo(window.research_page, QPoint()).y()
    context_top = window.market_context_widget.mapTo(window.research_page, QPoint()).y()
    assert decision_top == evidence_top
    assert decision_top < context_top

    window.resize(1060, 720)
    app.processEvents()
    left_width, right_width = window.research_splitter.sizes()
    assert left_width >= 550
    assert right_width >= 410
    assert window.decision_panel.isVisible()
    window.close()


def test_investment_agent_accepts_strategy_conversation_without_holding_evidence(
    tmp_path,
) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", False)
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, repository)
    window.show()
    app.processEvents()

    assert window.advisor_widget.evidence is None
    assert window.advisor_widget.send_button.isEnabled()
    window.advisor_widget.question_input.setPlainText("我是稳健型，投资周期为长线，请解释当前策略")
    window.advisor_widget.send_button.click()
    wait_for_advisor_tasks(window)

    assert [turn.role for turn in window.advisor_widget.turns] == ["user", "assistant"]
    assert "可以继续设计策略" in window.advisor_widget.transcript.toPlainText()
    assert "Agent" in window.advisor_widget.transcript.toPlainText()
    assert "持仓顾问" not in window.advisor_widget.transcript.toPlainText()
    assert window.advisor_widget.mode_badge.text() == "就绪"
    assert window._conversation_store.load_memory().risk_profile == "稳健"
    assert window._conversation_store.load_memory().horizon == "长线"
    window.close()


def test_investment_agent_enter_sends_and_explains_macro_snapshot_source(
    tmp_path, monkeypatch
) -> None:
    live_snapshot = replace(
        BundledOfficialMacroProvider().load(),
        version="cn-macro-official-live-test",
        as_of=date(2026, 8, 21),
    )

    class LiveProvider:
        def load(self):  # type: ignore[no-untyped-def]
            return live_snapshot

    monkeypatch.setattr(
        "aegisrun.agents.investment_runtime.default_macro_provider",
        lambda *, live=False: LiveProvider(),
    )

    async def verify_without_network(self, snapshot, *, today=None):
        del self, today
        checks = tuple(
            MacroSourceCheck(
                key,
                key.upper(),
                f"https://{key}.gov.cn/",
                "succeeded",
                date(2026, 8, 17),
                "test",
            )
            for key in ("nbs", "pboc", "safe", "mof")
        )
        return assess_macro_freshness(snapshot, checks, today=date(2026, 8, 21))

    monkeypatch.setattr(OfficialMacroFreshnessVerifier, "verify", verify_without_network)
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", False)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    window.show()
    window.advisor_widget.question_input.setFocus()
    window.advisor_widget.question_input.setPlainText("宏观分析的数据是实时的吗？")

    QTest.keyClick(window.advisor_widget.question_input, Qt.Key.Key_Return)
    # The complete macro dossier now persists a larger Markdown/HTML artifact and
    # can cross the old 5 s limit on a cold Qt test host.
    wait_for_advisor_tasks(window, timeout_ms=15_000)

    transcript = window.advisor_widget.transcript.toPlainText()
    assert [turn.role for turn in window.advisor_widget.turns] == ["user", "assistant"]
    assert "时效门禁" in transcript
    assert "cn-macro-official-live-test" in transcript
    assert "当前允许使用下方结构化宏观结论" in transcript
    assert window.advisor_widget.question_input.toPlainText() == ""

    window.advisor_widget.question_input.setPlainText("第一行")
    window.advisor_widget.question_input.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(
        window.advisor_widget.question_input,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ShiftModifier,
    )
    assert window.advisor_widget.question_input.toPlainText() == "第一行\n"
    assert len(window.advisor_widget.turns) == 2
    window.close()


def test_sector_proxy_is_loaded_on_demand_and_refreshes_confluence_advice(tmp_path) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    position = Position("600519.SH", 10, 100, industry="半导体")
    repository.upsert_position(position)
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, repository)
    end = date(2026, 8, 11)
    provider = DemoMarketDataProvider()
    result = asyncio.run(
        run_research(
            provider,
            "600519.SH",
            end - timedelta(days=2_500),
            end,
            AdjustmentMode.QFQ,
            position=position,
        )
    )
    window.render_result(result)
    window.market_context_widget.suggest_industry(position.industry)
    selected = window.market_context_widget.sector_combo.currentData()
    expected = sector_proxy_for(position.industry)
    assert expected is not None
    assert selected.symbol == expected.symbol

    emitted: list[object] = []
    window.market_context_widget.sector_load_requested.disconnect(window.start_sector_analysis)
    window.market_context_widget.sector_load_requested.connect(emitted.append)
    window.market_context_widget.load_sector_button.click()
    assert emitted == [expected]
    assert window.market_context_widget.load_sector_button.isEnabled() is False

    sector = load_market_trend(provider, expected, end - timedelta(days=2_500), end)
    updated = attach_sector_context(result, sector, position=position)
    window.render_result(updated)
    window.market_context_widget.set_sector_loading(False)
    app.processEvents()

    assert updated.market_context.sector is not None
    assert window.market_context_widget.sector_pane.price_chart.chart_data is not None
    assert window.market_context_widget.sector_pane.macd_chart.chart_data is not None
    assert window.market_context_widget.sector_pane.wr_chart.chart_data is not None
    assert "板块" in updated.deterministic_summary
    assert "大盘/板块调整" in window.decision_summary.toPlainText()
    assert updated.market_context.priority_label in window.decision_summary.toPlainText()
    window.close()


def test_sector_failure_closes_gate_and_stale_result_cannot_overwrite_analysis(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    position = Position("600519.SH", 10, 100, industry="主要消费")
    repository.upsert_position(position)
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, repository)
    end = date(2026, 8, 11)
    start = end - timedelta(days=2_500)
    result = asyncio.run(
        run_research(
            DemoMarketDataProvider(),
            position.symbol,
            start,
            end,
            AdjustmentMode.QFQ,
            position=position,
        )
    )
    window._last_request = ResearchRequest(
        "demo",
        position.symbol,
        start,
        end,
        AdjustmentMode.QFQ,
        None,
        None,
        False,
        position,
        position.industry,
    )
    window.render_result(result)
    sector = sector_proxy_for(position.industry)
    assert sector is not None

    stale = SectorContextTask(
        SectorContextRequest(
            "demo",
            result.data.symbol,
            result.data.as_of - timedelta(days=1),
            sector,
            start,
            end,
            None,
        )
    )
    original = window._last_result
    window._sector_analysis_failed(stale, "过期任务错误")
    assert window._last_result is original

    current = SectorContextTask(
        SectorContextRequest(
            "demo",
            result.data.symbol,
            result.data.as_of,
            sector,
            start,
            end,
            None,
        )
    )
    window._sector_tasks.add(current)
    window._sector_analysis_failed(current, "network unavailable")

    assert window._last_result is not None
    assert window._last_result.market_context.sector is not None
    assert window._last_result.market_context.sector.available is False
    assert window._last_result.market_context.buy_gate_open is False
    assert window._last_result.investment_advice.action.value not in {"buy", "add"}
    assert window.market_context_widget.error_label.isHidden() is False
    window.close()


def test_tushare_without_token_can_fall_back_to_public_data(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    window.source_combo.setCurrentIndex(window.source_combo.findData("tushare"))
    window._choose_missing_tushare_fallback = lambda: "baostock"  # type: ignore[method-assign]

    source = window._resolve_data_source("tushare", None)

    assert source == "baostock"
    assert window.source_combo.currentData() == "baostock"
    assert settings.value("research/source") == "baostock"
    assert "无需账号或 Token" in window.source_notice.text()
    window.close()


def test_invalid_chart_preferences_are_repaired_to_safe_defaults(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("chart/timeframe", "quarterly")
    settings.setValue("chart/indicators", "MA,UNKNOWN")

    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )

    assert window.timeframe_combo.currentData() == Timeframe.DAILY.value
    assert window.indicator_selector.selected_indicators == ("MA", "MACD", "WR")
    assert settings.value("chart/timeframe") == Timeframe.DAILY.value
    assert settings.value("chart/indicators") == "MA,MACD,WR"
    window.close()


def test_desktop_portfolio_and_watchlist_are_available_to_candidate_pool(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    repository.upsert_position(Position("600519.SH", 10, 1_200))
    repository.upsert_watch(WatchItem("000001.SZ", "平安银行"))
    window = MainWindow(CredentialStore(EmptyKeyring()), settings, repository)

    assert repository.load().symbols() == ("600519.SH", "000001.SZ")
    assert window.screen_button.text() == "扫描候选池"
    assert window.backtest_button.isEnabled() is False
    assert window.macro_button.text() == "宏观联网核验"
    assert "官方发布页" in window.macro_button.toolTip()
    assert window.macro_button.isEnabled() is True

    dialog = PortfolioDialog(repository)
    assert dialog.positions_table.rowCount() == 1
    assert dialog.watch_table.rowCount() == 1
    dialog.close()
    window.close()


def test_stock_research_does_not_apply_expired_macro_overlay(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def capture_run_research(*args, **kwargs):
        del args
        captured["macro_overlay"] = kwargs.get("macro_overlay")
        return "result"

    monkeypatch.setattr("aegisrun.application.services.run_research", capture_run_research)
    request = ResearchRequest(
        source="demo",
        symbol="600519.SH",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 21),
        adjustment=AdjustmentMode.QFQ,
        tushare_token=None,
        deepseek_api_key=None,
        use_ai=False,
        industry="白酒",
    )

    result = asyncio.run(ResearchTask(request)._execute(DemoMarketDataProvider()))

    assert result == "result"
    assert captured["macro_overlay"] is None


def test_macro_dialog_renders_report_sources_and_agent_plan(tmp_path) -> None:
    application()
    result = asyncio.run(
        run_macro_research(
            BundledOfficialMacroProvider(),
            workspace_root=tmp_path,
            run_id="macro-ui-test",
            verifier=OfficialMacroFreshnessVerifier(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, text="2026-08-17", request=request)
                )
            ),
            today=date(2026, 8, 21),
        )
    )

    dialog = MacroDialog(result)

    tabs = dialog.findChild(QTabWidget)
    tables = dialog.findChildren(QTableWidget)
    assert dialog.windowTitle() == "资本三流与成本转嫁宏观投资分析"
    assert tabs is not None and tabs.count() == 8
    assert dialog.tabs is tabs
    assert dialog.tabs.tabText(0) == "时效核验"
    assert dialog.tabs.tabText(1) == "历史配置（已失效）"
    assert dialog.validity_title.text() == "当前结论已失效"
    assert dialog.freshness_table.rowCount() == 4
    assert dialog.profile_combo.isEnabled() is False
    assert dialog.investable_amount.isEnabled() is False
    assert dialog.profile_combo.count() == 3
    assert dialog.profile_combo.currentData() == "balanced"
    assert dialog.allocation_table.rowCount() == 7
    assert "稳健平衡" in dialog.allocation_summary.text()
    assert dialog.allocation_table.item(0, 4).text() == "¥10,000"
    dialog.investable_amount.setValue(200_000)
    app = application()
    app.processEvents()
    assert any("¥80,000" in label.text() for label in dialog.allocation_step_amounts)
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData("growth"))
    app.processEvents()
    assert "成长进取" in dialog.allocation_summary.text()
    assert dialog.sectors_table.rowCount() == 5
    assert dialog.flow_map.minimumHeight() >= 250
    assert dialog.transfer_map.minimumHeight() >= 280
    assert dialog.chains_table.rowCount() >= 6
    assert dialog.sources_table.rowCount() == 37
    assert "联网核验" in dialog.provenance_notice.text()
    assert "未通过门禁" in dialog.provenance_notice.text()
    assert "2026-06-30" in dialog.provenance_notice.text()
    assert len(tables) == 6
    assert sorted(table.rowCount() for table in tables) == sorted(
        [
            7,
            5,
            len(result.analysis.capital_flow.paths),
            37,
            dialog.chains_table.rowCount(),
            4,
        ]
    )
    assert "当前宏观投资结论不可用" in result.report
    dialog.close()


def test_backtest_configuration_results_exports_and_candidate_failures(tmp_path) -> None:
    application()
    config = BacktestConfigDialog(
        date(2025, 1, 2),
        date(2026, 1, 2),
        date(2025, 6, 1),
    )
    config.horizons.setText("5，10, 20")
    config.cost_bps.setValue(12.5)
    options = config.value()
    assert options.horizons == (5, 10, 20)
    assert options.transaction_cost_bps == 12.5
    config.close()

    start = date(2026, 6, 1)
    bars = tuple(
        PriceBar(
            start + timedelta(days=index),
            100 + index,
            101 + index,
            99 + index,
            100.5 + index,
            100,
            10_000,
        )
        for index in range(30)
    )

    def analyzer(history: tuple[PriceBar, ...]) -> MultiTimeframeAnalysis:
        current = history[-1].trade_date
        action = (
            TimingAction.ENTRY_WATCH
            if current == date(2026, 6, 5)
            else TimingAction.EXIT_WATCH
            if current == date(2026, 6, 12)
            else TimingAction.WAIT
        )
        return MultiTimeframeAnalysis(
            version="ui-test",
            direction=Direction.BULLISH.value,
            direction_label=Direction.BULLISH.label,
            direction_score=6,
            regime="trend",
            macd={},
            wr={},
            risk_flags=(),
            timing=TimingDecision(action.value, action.label, 80, ("UI 测试",)),
            candidate_score=80,
        )

    report = walk_forward_backtest(
        bars,
        date(2026, 6, 1),
        date(2026, 6, 20),
        min_history_bars=2,
        analyzer=analyzer,
        symbol="600519.SH",
    )
    dialog = BacktestDialog(report)
    assert dialog.tabs.count() == 4
    assert dialog.statistics_table.rowCount() == 6
    assert dialog.trade_table.rowCount() == 1
    assert dialog.signal_table.rowCount() == 2
    assert dialog.signal_table.item(0, 3).text() == "上涨"
    dialog.export_json_to(tmp_path / "report.json")
    dialog.export_csv_to(tmp_path / "signals.csv")
    assert (tmp_path / "report.json").is_file()
    assert (tmp_path / "signals.csv").is_file()
    dialog.close()

    candidate_dialog = CandidateResultsDialog(
        (),
        (),
        failures={"000001.SZ": "上游暂时不可用"},
        strategy_label="稳健长线 · steady-long-term (user-1)",
    )
    assert candidate_dialog.tabs.count() == 3
    labels = {label.text() for label in candidate_dialog.findChildren(QLabel)}
    assert any("当前策略没有筛出候选" in text for text in labels)
    assert any("稳健长线 · steady-long-term (user-1)" in text for text in labels)
    candidate_table = next(
        table
        for table in candidate_dialog.findChildren(QTableWidget)
        if table.accessibleName() == "策略候选结果"
    )
    assert candidate_table.columnCount() == 14
    assert candidate_table.horizontalHeaderItem(11).text() == "策略分"
    failure_table = next(
        table
        for table in candidate_dialog.findChildren(QTableWidget)
        if table.accessibleName() == "候选池失败明细"
    )
    assert failure_table.rowCount() == 1
    assert failure_table.item(0, 0).text() == "000001.SZ"
    candidate_dialog.close()


def test_settings_explains_distinct_credentials_and_requires_key_for_test(
    tmp_path, monkeypatch: object
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # type: ignore[attr-defined]
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(CredentialStore(EmptyKeyring()), settings)

    assert dialog.deepseek_key.accessibleName() == "DeepSeek API Key"
    assert dialog.tushare_token.accessibleName() == "Tushare Token"
    assert dialog.tavily_key.accessibleName() == "Tavily Search Key"
    assert [
        dialog.deepseek_model.itemData(index) for index in range(dialog.deepseek_model.count())
    ] == [DEEPSEEK_V4_FLASH, DEEPSEEK_V4_PRO]
    dialog.include_builtin_skills.setChecked(False)
    dialog.user_skill_root.setText(str(tmp_path / "my-skills"))
    dialog._test_deepseek()
    assert dialog.connection_status.text() == "请先输入 DeepSeek API Key。"
    dialog.deepseek_model.setCurrentIndex(dialog.deepseek_model.findData(DEEPSEEK_V4_PRO))
    dialog._save()
    assert settings.value("research/deepseek_model") == DEEPSEEK_V4_PRO
    assert settings.value("skills/include_builtin", type=bool) is False
    assert settings.value("skills/user_root") == str(tmp_path / "my-skills")
    dialog.close()


def test_main_window_status_reflects_selected_deepseek_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-secret")
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", True)
    settings.setValue("research/deepseek_model", DEEPSEEK_V4_PRO)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    window.show()
    app.processEvents()

    assert window._deepseek_model() == DEEPSEEK_V4_PRO
    assert window.ai_status.text() == "DeepSeek V4 Pro · Key 已配置"
    window.close()


def test_main_window_uses_explicit_user_candidate_skill_when_builtins_are_disabled(
    tmp_path,
) -> None:
    application()
    skill_root = tmp_path / "skills"
    package_root = skill_root / "my-candidate-filter"
    package_root.mkdir(parents=True)
    (package_root / "SKILL.md").write_text(
        "---\n"
        "name: my-candidate-filter\n"
        "description: 用户候选策略\n"
        "resources:\n"
        "  - strategy.json\n"
        "---\n"
        "使用用户声明的候选池规则。\n",
        encoding="utf-8",
    )
    (package_root / "strategy.json").write_text(
        json.dumps(
            {
                "schema_version": "aegisrun-candidate-strategy/v1",
                "name": "用户稳健策略",
                "filters": {"min_confidence": 70},
                "ranking": {"mode": "legacy"},
                "max_results": 8,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("skills/include_builtin", False)
    settings.setValue("skills/user_root", str(skill_root))
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    package = window._skill_workspace.activate("my-candidate-filter")

    selected, strategy = window._candidate_strategy(package)

    assert selected is package
    assert selected.summary.provider == "user-1"
    assert strategy is not None
    assert strategy.name == "用户稳健策略"
    assert strategy.max_results == 8
    window.close()


def test_investment_agent_exposes_workspace_and_user_skill_selectors(
    tmp_path, monkeypatch: object
) -> None:
    application()
    skill_root = tmp_path / "skills"
    package_root = skill_root / "my-research-style"
    package_root.mkdir(parents=True)
    (package_root / "SKILL.md").write_text(
        "---\nname: my-research-style\ndescription: 用户研究风格\n---\n强调回撤与证据来源。\n",
        encoding="utf-8",
    )
    custom_workspace = tmp_path / "my-agent-workspace"
    custom_workspace.mkdir()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("skills/user_root", str(skill_root))
    settings.setValue("agent/workspace_root", str(custom_workspace))
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    captured: dict[str, object] = {}

    def start_agent(*args: object, **kwargs: object) -> None:
        captured["packages"] = args[5]

    monkeypatch.setattr(  # type: ignore[attr-defined]
        window, "_start_general_investment_chat", start_agent
    )
    skill_index = window.advisor_widget.skill_combo.findData("my-research-style")
    assert skill_index >= 0
    window.advisor_widget.skill_combo.setCurrentIndex(skill_index)
    window._submit_advisor_question("设计一个低回撤策略")

    packages = captured["packages"]
    assert isinstance(packages, tuple)
    assert packages[0].summary.name == "my-research-style"  # type: ignore[attr-defined]
    assert window.advisor_widget.workspace_root == str(custom_workspace)
    assert "my-agent-workspace" in window.advisor_widget.workspace_button.text()
    window._reset_investment_agent_workspace()
    assert window.advisor_widget.workspace_root.endswith("investment-agent-workspaces")
    window.close()


def test_investment_agent_routes_explicit_skill_to_candidate_scan(
    tmp_path, monkeypatch: object
) -> None:
    app = application()
    skill_root = tmp_path / "skills"
    package_root = skill_root / "my-candidate-filter"
    package_root.mkdir(parents=True)
    (package_root / "SKILL.md").write_text(
        "---\n"
        "name: my-candidate-filter\n"
        "description: 用户候选策略\n"
        "resources:\n"
        "  - strategy.json\n"
        "---\n"
        "使用用户声明的候选池规则。\n",
        encoding="utf-8",
    )
    (package_root / "strategy.json").write_text(
        json.dumps(
            {
                "schema_version": "aegisrun-candidate-strategy/v1",
                "name": "用户候选策略",
                "max_results": 5,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("skills/include_builtin", False)
    settings.setValue("skills/user_root", str(skill_root))
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    captured: dict[str, object] = {}

    def start_agent(*args: object, **kwargs: object) -> None:
        captured["packages"] = args[5]

    monkeypatch.setattr(  # type: ignore[attr-defined]
        window, "_start_general_investment_chat", start_agent
    )
    window._submit_advisor_question("/my-candidate-filter 筛选候选池")
    app.processEvents()

    packages = captured["packages"]
    assert isinstance(packages, tuple)
    package = packages[0]
    assert package is not None
    assert package.summary.name == "my-candidate-filter"  # type: ignore[attr-defined]
    assert package.summary.provider == "user-1"  # type: ignore[attr-defined]
    assert [turn.role for turn in window.advisor_widget.turns] == ["user"]
    window.close()


def test_investment_agent_reports_empty_candidate_pool(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", False)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    window._submit_advisor_question("筛选候选池")
    wait_for_advisor_tasks(window)

    transcript = window.advisor_widget.transcript.toPlainText()
    assert "候选池筛选未完成" in transcript
    assert [turn.role for turn in window.advisor_widget.turns] == ["user", "assistant"]
    window.close()


def test_clear_conversation_requires_confirmation_and_keeps_memory(
    tmp_path, monkeypatch: object
) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("research/use_ai", False)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    window._submit_advisor_question("我是稳健型，投资周期为长线，请解释策略")
    wait_for_advisor_tasks(window)
    thread_id = window._advisor_thread_id()
    assert window._conversation_store.load_thread(thread_id).turns
    assert window._conversation_store.load_memory().risk_profile == "稳健"

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window.advisor_widget.clear_button.click()
    app.processEvents()
    assert window._conversation_store.load_thread(thread_id).turns

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window.advisor_widget.clear_button.click()
    app.processEvents()

    assert window._conversation_store.load_thread(thread_id).turns == []
    assert window._conversation_store.load_memory().risk_profile == "稳健"
    assert window.advisor_widget.turns == ()
    assert "长期投资偏好已保留" in window.statusBar().currentMessage()
    window.close()


def test_corrupt_conversation_is_recoverable_from_agent_page(tmp_path) -> None:
    application()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        CredentialStore(EmptyKeyring()),
        settings,
        PortfolioRepository(tmp_path / "portfolio.json"),
    )
    thread_path = (
        tmp_path / "conversations" / "users" / "local-user" / "threads" / "investment-general.json"
    )
    thread_path.parent.mkdir(parents=True, exist_ok=True)
    thread_path.write_text('{"turns": "broken"}', encoding="utf-8")

    window._restore_advisor_conversation("")

    transcript = window.advisor_widget.transcript.toPlainText()
    assert "本地对话记录无法读取" in transcript
    assert "清空对话" in transcript
    assert window.advisor_widget.clear_button.isEnabled()
    window.close()
