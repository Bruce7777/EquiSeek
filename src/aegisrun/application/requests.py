from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from aegisrun.agents.investment_conversation import InvestmentIntent
from aegisrun.agents.investment_runtime import InvestmentAgentRunRequest
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.portfolio.models import Position
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.portfolio.strategy_dsl import CandidateStrategy
from aegisrun.research.advisor_chat import (
    AdvisorConversationContext,
    AdvisorEvidence,
    AdvisorTurn,
)
from aegisrun.research.backtest import BacktestOptions
from aegisrun.research.deepseek import (
    DEEPSEEK_OFFICIAL_PROVIDER,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
)
from aegisrun.research.market_context import ContextInstrument
from aegisrun.research.service import ResearchResult
from aegisrun.skills import SkillWorkspace


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    source: str
    symbol: str
    start_date: date
    end_date: date
    adjustment: AdjustmentMode
    tushare_token: str | None
    deepseek_api_key: str | None
    use_ai: bool
    position: Position | None = None
    industry: str = ""
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    workspace_root: str = ""


@dataclass(frozen=True, slots=True)
class AdvisorChatRequest:
    evidence: AdvisorEvidence
    history: tuple[AdvisorTurn, ...]
    question: str
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    conversation: AdvisorConversationContext | None = None


@dataclass(frozen=True, slots=True)
class InvestmentChatRequest:
    history: tuple[AdvisorTurn, ...]
    question: str
    intent: InvestmentIntent
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    conversation: AdvisorConversationContext | None = None


@dataclass(frozen=True, slots=True)
class InvestmentAgentTaskRequest:
    run: InvestmentAgentRunRequest
    workspace_root: str
    skills: SkillWorkspace
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    model_provider: str = DEEPSEEK_OFFICIAL_PROVIDER
    model_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    portfolio_manager: PortfolioRepository | None = None


@dataclass(frozen=True, slots=True)
class CandidateScreenRequest:
    source: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    adjustment: AdjustmentMode
    tushare_token: str | None
    positions: tuple[Position, ...] = ()
    industries: tuple[tuple[str, str], ...] = ()
    strategy: CandidateStrategy | None = None
    strategy_skill_name: str = ""
    strategy_skill_provider: str = ""


@dataclass(frozen=True, slots=True)
class CandidateScreenResult:
    results: tuple[ResearchResult, ...]
    failures: dict[str, str]


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    result: ResearchResult
    options: BacktestOptions


@dataclass(frozen=True, slots=True)
class SectorContextRequest:
    source: str
    stock_symbol: str
    stock_as_of: date
    instrument: ContextInstrument
    start_date: date
    end_date: date
    tushare_token: str | None
