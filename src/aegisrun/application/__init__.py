"""Framework-free application commands shared by desktop and future sidecar adapters."""

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

__all__ = [
    "AdvisorChatRequest",
    "BacktestRequest",
    "CandidateScreenRequest",
    "CandidateScreenResult",
    "InvestmentAgentTaskRequest",
    "InvestmentChatRequest",
    "ResearchRequest",
    "SectorContextRequest",
    "answer_advisor_chat",
    "answer_general_investment_chat",
    "execute_backtest",
    "execute_candidate_screen",
    "execute_investment_agent",
    "execute_macro_research",
    "execute_research",
    "execute_sector_context",
    "market_data_provider",
    "verify_deepseek_connection",
]
