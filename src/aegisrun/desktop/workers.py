from __future__ import annotations

import asyncio

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from aegisrun.agents.investment_runtime import InvestmentAgentRunResult
from aegisrun.application.requests import (
    AdvisorChatRequest,
    BacktestRequest,
    CandidateScreenRequest,
    CandidateScreenResult,
    InvestmentAgentTaskRequest,
    InvestmentChatRequest,
    ResearchRequest,
    SectorContextRequest,
)
from aegisrun.application.services import (
    answer_advisor_chat,
    answer_general_investment_chat,
    execute_backtest,
    execute_candidate_screen,
    execute_investment_agent,
    execute_macro_research,
    execute_research,
    execute_sector_context,
    market_data_provider,
    verify_deepseek_connection,
)
from aegisrun.macro.pipeline import MacroResearchResult
from aegisrun.marketdata.providers import MarketDataProvider
from aegisrun.research.advisor_chat import AdvisorAnswer
from aegisrun.research.backtest import BacktestReport
from aegisrun.research.deepseek import DEFAULT_DEEPSEEK_MODEL
from aegisrun.research.market_context import MarketTrendContext
from aegisrun.research.service import ResearchResult


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class ConnectionTestSignals(QObject):
    succeeded = Signal(str)
    failed = Signal(str)


class DeepSeekConnectionTask(QRunnable):
    def __init__(self, api_key: str, model: str = DEFAULT_DEEPSEEK_MODEL) -> None:
        super().__init__()
        self._api_key = api_key
        self._model = model
        self.signals = ConnectionTestSignals()

    async def _execute(self) -> str:
        return await verify_deepseek_connection(self._api_key, self._model)

    @Slot()
    def run(self) -> None:
        try:
            model = asyncio.run(self._execute())
            self.signals.succeeded.emit(model)
        except Exception as error:
            self.signals.failed.emit(str(error))


class ResearchTask(QRunnable):
    def __init__(self, request: ResearchRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    def _provider(self) -> MarketDataProvider:
        return market_data_provider(self.request.source, self.request.tushare_token)

    async def _execute(self, provider: MarketDataProvider) -> ResearchResult:
        return await execute_research(
            self.request,
            on_progress=self.signals.progress.emit,
            provider=provider,
        )

    @Slot()
    def run(self) -> None:
        try:
            provider = self._provider()
            result = asyncio.run(self._execute(provider))
            self.signals.succeeded.emit(result)
        except Exception as error:
            self.signals.failed.emit(str(error))


class AdvisorChatTask(QRunnable):
    def __init__(self, request: AdvisorChatRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    async def _execute(self) -> AdvisorAnswer:
        return await answer_advisor_chat(self.request)

    @Slot()
    def run(self) -> None:
        try:
            answer = asyncio.run(self._execute())
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                return
        else:
            try:
                self.signals.succeeded.emit(answer)
            except RuntimeError:
                return


class InvestmentChatTask(QRunnable):
    def __init__(self, request: InvestmentChatRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    async def _execute(self) -> AdvisorAnswer:
        return await answer_general_investment_chat(self.request)

    @Slot()
    def run(self) -> None:
        try:
            answer = asyncio.run(self._execute())
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                return
        else:
            try:
                self.signals.succeeded.emit(answer)
            except RuntimeError:
                return


class InvestmentAgentTask(QRunnable):
    def __init__(self, request: InvestmentAgentTaskRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    async def _execute(self) -> InvestmentAgentRunResult:
        return await execute_investment_agent(
            self.request, on_progress=self.signals.progress.emit
        )

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(self._execute())
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                return
        else:
            try:
                self.signals.succeeded.emit(result)
            except RuntimeError:
                return


class SectorContextTask(QRunnable):
    def __init__(self, request: SectorContextRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result: MarketTrendContext = execute_sector_context(self.request)
            self.signals.succeeded.emit(result)
        except Exception as error:
            self.signals.failed.emit(str(error))


class CandidateScreenTask(QRunnable):
    def __init__(self, request: CandidateScreenRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    async def _execute(self) -> CandidateScreenResult:
        return await execute_candidate_screen(
            self.request, on_progress=self.signals.progress.emit
        )

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(asyncio.run(self._execute()))
        except Exception as error:
            self.signals.failed.emit(str(error))


class BacktestTask(QRunnable):
    def __init__(self, request: BacktestRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            report: BacktestReport = execute_backtest(self.request)
            self.signals.succeeded.emit(report)
        except Exception as error:
            self.signals.failed.emit(str(error))


class MacroTask(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = TaskSignals()

    async def _execute(self) -> MacroResearchResult:
        return await execute_macro_research()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(asyncio.run(self._execute()))
        except Exception as error:
            self.signals.failed.emit(str(error))
