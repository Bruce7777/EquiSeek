from __future__ import annotations

import stat
from datetime import date, timedelta

from aegisrun.marketdata.models import PriceBar
from aegisrun.portfolio.analysis import (
    CandidateInput,
    assess_holding,
    rank_strategy_candidates,
)
from aegisrun.portfolio.models import Position, WatchItem
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.research.signals import (
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)


def analysis(score: int, action: TimingAction) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        version="test",
        direction=Direction.BULLISH.value,
        direction_label=Direction.BULLISH.label,
        direction_score=5,
        regime="trend",
        macd={},
        wr={},
        risk_flags=(),
        timing=TimingDecision(action.value, action.label, 80, ("测试触发",)),
        candidate_score=score,
    )


def test_portfolio_repository_round_trips_local_private_data(tmp_path) -> None:
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    repository.upsert_position(
        Position(
            "600519",
            100,
            1_200.5,
            "贵州茅台",
            date(2025, 1, 2),
            "长期观察",
            "白酒消费",
        )
    )
    repository.upsert_watch(WatchItem("000001.SZ", "平安银行", "估值观察", "银行"))

    restored = repository.load()

    assert restored.positions[0].symbol == "600519.SH"
    assert restored.watchlist[0].symbol == "000001.SZ"
    assert restored.position("600519") is not None
    assert restored.positions[0].industry == "白酒消费"
    assert restored.watchlist[0].industry == "银行"
    assert stat.S_IMODE(repository.path.stat().st_mode) == 0o600


def test_portfolio_repository_removes_codes_after_canonicalization(tmp_path) -> None:
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    repository.upsert_position(Position("600519.SH", 100, 1_200.5))
    repository.upsert_watch(WatchItem("000001.SZ"))

    after_position = repository.remove_position("600519")
    after_watch = repository.remove_watch("000001")

    assert after_position.positions == ()
    assert after_watch.watchlist == ()


def test_holding_assessment_calculates_pnl_and_uses_strategy_status() -> None:
    position = Position("600519.SH", 10, 100.0)

    result = assess_holding(position, 120.0, analysis(70, TimingAction.EXIT_WATCH))

    assert result.market_value == 1_200
    assert result.unrealized_pnl == 200
    assert result.unrealized_return_pct == 20
    assert result.status == TimingAction.EXIT_WATCH.value


def test_holding_assessment_uses_open_date_for_drawdown_and_exit_priority() -> None:
    start = date(2026, 1, 2)
    closes = (100.0, 120.0, 115.0, 96.0)
    bars = tuple(
        PriceBar(
            start + timedelta(days=index),
            close,
            close + 1,
            close - 1,
            close,
            100,
            close * 100,
        )
        for index, close in enumerate(closes)
    )
    position = Position("600519.SH", 10, 100.0, opened_on=start)

    result = assess_holding(
        position,
        96.0,
        analysis(70, TimingAction.EXIT_WATCH),
        bars=bars,
    )

    assert result.peak_close_since_entry == 120.0
    assert result.drawdown_from_peak_pct == -20.0
    assert result.breakeven_distance_pct == 4.17
    assert result.holding_days == 3
    assert result.exit_priority == "urgent"
    assert result.next_trigger


def test_candidate_ranking_is_deterministic_and_excludes_exit_window() -> None:
    ranked = rank_strategy_candidates(
        [
            CandidateInput("000001.SZ", "平安银行", analysis(65, TimingAction.WAIT)),
            CandidateInput("600519.SH", "贵州茅台", analysis(88, TimingAction.ENTRY_WATCH)),
            CandidateInput("000002.SZ", "万科A", analysis(90, TimingAction.EXIT_WATCH)),
        ]
    )

    assert [item.symbol for item in ranked] == ["600519.SH", "000001.SZ"]
    assert [item.rank for item in ranked] == [1, 2]
