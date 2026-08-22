from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aegisrun.marketdata.models import PriceBar
from aegisrun.portfolio.models import Position
from aegisrun.portfolio.strategy_dsl import CandidateStrategy
from aegisrun.research.advice import InvestmentAction, InvestmentAdvice
from aegisrun.research.signals import MultiTimeframeAnalysis, TimingAction

if TYPE_CHECKING:
    from aegisrun.macro.analysis import MacroOverlay


@dataclass(frozen=True, slots=True)
class HoldingAssessment:
    symbol: str
    latest_close: float
    market_value: float
    unrealized_pnl: float
    unrealized_return_pct: float
    status: str
    status_label: str
    reasons: tuple[str, ...]
    recommended_action: str = ""
    recommended_action_label: str = ""
    confidence: int = 0
    invalidation_condition: str = ""
    holding_days: int | None = None
    peak_close_since_entry: float | None = None
    drawdown_from_peak_pct: float | None = None
    breakeven_distance_pct: float | None = None
    exit_priority: str = "low"
    exit_priority_label: str = "低"
    next_trigger: str = ""
    industry: str = ""
    macro_stance_label: str = ""
    macro_confidence_adjustment: int = 0


@dataclass(frozen=True, slots=True)
class CandidateResult:
    rank: int
    symbol: str
    name: str
    score: int
    direction: str
    direction_label: str
    timing: str
    timing_label: str
    reasons: tuple[str, ...]
    action: str = ""
    action_label: str = ""
    confidence: int = 0
    forecast_20d_direction: str = ""
    forecast_20d_probability_pct: float | None = None
    forecast_20d_scenario_score: float | None = None
    forecast_20d_measure_label: str = ""
    industry: str = ""
    macro_stance_label: str = ""
    macro_confidence_adjustment: int = 0
    technical_confidence: int = 0
    market_priority_label: str = ""
    market_confidence_adjustment: int = 0
    benchmark_direction_label: str = ""
    strategy_score: float | None = None
    strategy_name: str = ""


@dataclass(frozen=True, slots=True)
class CandidateInput:
    symbol: str
    name: str
    analysis: MultiTimeframeAnalysis
    advice: InvestmentAdvice | None = None
    industry: str = ""


def assess_holding(
    position: Position,
    latest_close: float,
    analysis: MultiTimeframeAnalysis,
    advice: InvestmentAdvice | None = None,
    *,
    bars: tuple[PriceBar, ...] | list[PriceBar] = (),
    macro_overlay: MacroOverlay | None = None,
) -> HoldingAssessment:
    market_value = latest_close * position.quantity
    cost_value = position.cost_price * position.quantity
    pnl = market_value - cost_value
    history = (
        tuple(bar for bar in bars if bar.trade_date >= position.opened_on)
        if position.opened_on is not None
        else ()
    )
    peak = max((bar.close for bar in history), default=None)
    drawdown = (latest_close / peak - 1) * 100 if peak else None
    breakeven = (position.cost_price / latest_close - 1) * 100
    holding_days = None
    if position.opened_on is not None and history:
        holding_days = max(0, (history[-1].trade_date - position.opened_on).days)
    recommended_action = advice.action if advice is not None else None
    if (
        recommended_action is InvestmentAction.SELL
        or analysis.timing.action == TimingAction.EXIT_WATCH.value
    ):
        priority, priority_label = "urgent", "紧急"
        next_trigger = "下一交易时段优先复核退出条件；若技术失效条件成立则撤销本次退出判断。"
    elif (
        recommended_action is InvestmentAction.REDUCE
        or analysis.timing.action == TimingAction.RISK_WATCH.value
    ):
        priority, priority_label = "high", "高"
        next_trigger = "关注日/周 WR 进入超买区或高周期 MACD 继续转弱，触发减仓复核。"
    elif (drawdown is not None and drawdown <= -12) or pnl / cost_value * 100 <= -10:
        priority, priority_label = "medium", "中"
        next_trigger = "回撤或亏损已扩大，下一根日 K 收盘后重新计算 MACD/WR 与 ATR 失效条件。"
    else:
        priority, priority_label = "low", "低"
        next_trigger = "等待日线 WR 进入技术窗口，或周/月 MACD 方向变化后重新评估。"
    return HoldingAssessment(
        symbol=position.symbol,
        latest_close=round(latest_close, 4),
        market_value=round(market_value, 2),
        unrealized_pnl=round(pnl, 2),
        unrealized_return_pct=round(pnl / cost_value * 100, 2),
        status=analysis.timing.action,
        status_label=analysis.timing.label,
        reasons=analysis.timing.reasons,
        recommended_action=(advice.action.value if advice is not None else ""),
        recommended_action_label=(advice.action_label if advice is not None else ""),
        confidence=(advice.confidence if advice is not None else 0),
        invalidation_condition=(advice.invalidation_condition if advice is not None else ""),
        holding_days=holding_days,
        peak_close_since_entry=round(peak, 4) if peak is not None else None,
        drawdown_from_peak_pct=round(drawdown, 2) if drawdown is not None else None,
        breakeven_distance_pct=round(breakeven, 2),
        exit_priority=priority,
        exit_priority_label=priority_label,
        next_trigger=next_trigger,
        industry=position.industry,
        macro_stance_label=(macro_overlay.stance_label if macro_overlay is not None else ""),
        macro_confidence_adjustment=(
            advice.macro_confidence_adjustment
            if advice is not None
            else macro_overlay.confidence_adjustment
            if macro_overlay is not None
            else 0
        ),
    )


def rank_strategy_candidates(
    items: list[CandidateInput],
    strategy: CandidateStrategy | None = None,
) -> tuple[CandidateResult, ...]:
    eligible = [item for item in items if _platform_candidate_allowed(item)]
    strategy_scores: dict[str, float] = {}
    if strategy is not None:
        eligible = [item for item in eligible if _strategy_candidate_allowed(item, strategy)]
        strategy_scores = {
            item.symbol: strategy.score(
                industry=item.industry,
                confidence=_candidate_confidence(item),
                candidate_score=item.analysis.candidate_score,
                market_adjustment=(
                    item.advice.market_confidence_adjustment
                    if item.advice is not None
                    else 0
                ),
                macro_adjustment=(
                    item.advice.macro_confidence_adjustment if item.advice is not None else 0
                ),
            )
            for item in eligible
        }
    if strategy is None or strategy.ranking.mode == "legacy":
        ordered = sorted(
            eligible,
            key=lambda item: (
                -_candidate_confidence(item),
                -item.analysis.candidate_score,
                item.symbol,
            ),
        )
    else:
        ordered = sorted(
            eligible,
            key=lambda item: (
                -strategy_scores[item.symbol],
                -_candidate_confidence(item),
                -item.analysis.candidate_score,
                item.symbol,
            ),
        )
    if strategy is not None:
        ordered = ordered[: strategy.max_results]
    return tuple(
        CandidateResult(
            rank=index,
            symbol=item.symbol,
            name=item.name,
            score=item.analysis.candidate_score,
            direction=item.analysis.direction,
            direction_label=item.analysis.direction_label,
            timing=item.analysis.timing.action,
            timing_label=item.analysis.timing.label,
            reasons=item.analysis.timing.reasons,
            action=(item.advice.action.value if item.advice is not None else ""),
            action_label=(item.advice.action_label if item.advice is not None else ""),
            confidence=(item.advice.confidence if item.advice is not None else 0),
            forecast_20d_direction=(
                item.advice.forecasts[-1].direction_label if item.advice is not None else ""
            ),
            forecast_20d_probability_pct=(
                item.advice.forecasts[-1].probability_pct if item.advice is not None else None
            ),
            forecast_20d_scenario_score=(
                item.advice.forecasts[-1].scenario_score if item.advice is not None else None
            ),
            forecast_20d_measure_label=(
                "历史同条件命中率"
                if item.advice is not None and item.advice.forecasts[-1].probability_pct is not None
                else "上涨情景分（非概率）"
                if item.advice is not None and item.advice.forecasts[-1].scenario_score is not None
                else ""
            ),
            industry=item.industry,
            macro_stance_label=(
                str(item.advice.macro_overlay.get("stance_label", ""))
                if item.advice is not None
                else ""
            ),
            macro_confidence_adjustment=(
                item.advice.macro_confidence_adjustment if item.advice is not None else 0
            ),
            technical_confidence=(
                item.advice.technical_confidence if item.advice is not None else 0
            ),
            market_priority_label=(
                str(item.advice.market_context.get("priority_label", ""))
                if item.advice is not None
                else ""
            ),
            market_confidence_adjustment=(
                item.advice.market_confidence_adjustment if item.advice is not None else 0
            ),
            benchmark_direction_label=(
                str((item.advice.market_context.get("benchmark") or {}).get("direction_label", ""))
                if item.advice is not None
                else ""
            ),
            strategy_score=strategy_scores.get(item.symbol),
            strategy_name=strategy.name if strategy is not None else "",
        )
        for index, item in enumerate(ordered, start=1)
    )


def _platform_candidate_allowed(item: CandidateInput) -> bool:
    return bool(
        item.analysis.timing.action != TimingAction.EXIT_WATCH.value
        and (
            item.advice is None
            or item.advice.action
            not in {InvestmentAction.SELL, InvestmentAction.REDUCE, InvestmentAction.AVOID}
        )
    )


def _candidate_action(item: CandidateInput) -> str:
    if item.advice is not None:
        return item.advice.action.value
    if item.analysis.timing.action == TimingAction.ENTRY_WATCH.value:
        return InvestmentAction.BUY.value
    return InvestmentAction.WAIT.value


def _candidate_confidence(item: CandidateInput) -> int:
    return item.advice.confidence if item.advice is not None else item.analysis.candidate_score


def _strategy_candidate_allowed(item: CandidateInput, strategy: CandidateStrategy) -> bool:
    buy_gate: bool | None = None
    if item.advice is not None:
        raw_gate = item.advice.market_context.get("buy_gate_open")
        buy_gate = raw_gate if isinstance(raw_gate, bool) else None
    return strategy.allows(
        symbol=item.symbol,
        industry=item.industry,
        action=_candidate_action(item),
        direction=item.analysis.direction,
        confidence=_candidate_confidence(item),
        candidate_score=item.analysis.candidate_score,
        buy_gate_open=buy_gate,
    )
