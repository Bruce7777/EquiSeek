from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aegisrun.marketdata.indicators import IndicatorSet
from aegisrun.marketdata.models import PriceBar
from aegisrun.research.backtest import summarize_signal_statistics, validate_historical_signals
from aegisrun.research.signals import Direction, MultiTimeframeAnalysis, TimingAction

if TYPE_CHECKING:
    from aegisrun.macro.analysis import MacroOverlay
    from aegisrun.portfolio.models import Position
    from aegisrun.research.market_context import MarketConfluence


class InvestmentAction(StrEnum):
    BUY = "buy"
    ADD = "add"
    HOLD = "hold"
    REDUCE = "reduce"
    SELL = "sell"
    WAIT = "wait"
    AVOID = "avoid"

    @property
    def label(self) -> str:
        return {
            self.BUY: "买入",
            self.ADD: "加仓",
            self.HOLD: "持有",
            self.REDUCE: "减仓",
            self.SELL: "卖出",
            self.WAIT: "等待",
            self.AVOID: "回避",
        }[self]


class ForecastDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"
    UNCERTAIN = "uncertain"

    @property
    def label(self) -> str:
        return {
            self.UP: "偏上涨",
            self.DOWN: "偏下跌",
            self.SIDEWAYS: "偏震荡",
            self.UNCERTAIN: "方向不确定",
        }[self]


@dataclass(frozen=True, slots=True)
class PriceForecast:
    trading_days: int
    direction: str
    direction_label: str
    probability_pct: float | None
    scenario_score: float | None
    expected_return_pct: float | None
    price_range_low: float | None
    price_range_high: float | None
    sample_count: int
    basis: str
    basis_label: str


@dataclass(frozen=True, slots=True)
class InvestmentAdvice:
    version: str
    symbol: str
    as_of: str
    action: InvestmentAction
    action_label: str
    confidence: int
    confidence_label: str
    technical_confidence: int
    market_confidence_adjustment: int
    macro_confidence_adjustment: int
    market_context: dict[str, Any]
    macro_overlay: dict[str, Any]
    direction: str
    direction_label: str
    current_price: float
    action_zone_low: float | None
    action_zone_high: float | None
    invalidation_price: float | None
    invalidation_condition: str
    forecasts: tuple[PriceForecast, ...]
    thesis: tuple[str, ...]
    evidence: tuple[str, ...]
    risk_controls: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _action_for(
    analysis: MultiTimeframeAnalysis,
    has_position: bool,
    market_context: MarketConfluence | None,
) -> InvestmentAction:
    timing = analysis.timing.action
    if timing == TimingAction.ENTRY_WATCH.value:
        action = InvestmentAction.ADD if has_position else InvestmentAction.BUY
        if market_context is not None and not market_context.buy_gate_open:
            return InvestmentAction.HOLD if has_position else InvestmentAction.WAIT
        return action
    if timing == TimingAction.EXIT_WATCH.value:
        return InvestmentAction.SELL if has_position else InvestmentAction.AVOID
    if timing == TimingAction.RISK_WATCH.value:
        return InvestmentAction.REDUCE if has_position else InvestmentAction.AVOID
    if analysis.direction == Direction.BEARISH.value:
        return InvestmentAction.REDUCE if has_position else InvestmentAction.AVOID
    if has_position:
        return InvestmentAction.HOLD
    return InvestmentAction.WAIT


def _rule_up_probability(
    analysis: MultiTimeframeAnalysis,
    horizon: int,
    market_context: MarketConfluence | None,
) -> float:
    score = 50.0 + max(-15.0, min(15.0, analysis.direction_score * 2.5))
    if analysis.direction == Direction.BULLISH.value:
        score += 8
    elif analysis.direction == Direction.BEARISH.value:
        score -= 8
    if analysis.timing.action == TimingAction.ENTRY_WATCH.value:
        score += 6
    elif analysis.timing.action == TimingAction.EXIT_WATCH.value:
        score -= 10
    elif analysis.timing.action == TimingAction.RISK_WATCH.value:
        score -= 6
    score -= min(12, len(analysis.risk_flags) * 4)
    if market_context is not None:
        score += max(-12, min(12, market_context.confidence_adjustment))
    horizon_weight = {5: 0.72, 10: 0.86, 20: 1.0}.get(horizon, 1.0)
    return round(max(20.0, min(80.0, 50 + (score - 50) * horizon_weight)), 2)


def _forecast_direction(up_probability: float) -> ForecastDirection:
    if up_probability >= 57:
        return ForecastDirection.UP
    if up_probability <= 43:
        return ForecastDirection.DOWN
    return ForecastDirection.SIDEWAYS


def _historical_statistics(
    bars: tuple[PriceBar, ...],
    action: InvestmentAction,
    horizons: tuple[int, ...],
) -> dict[int, tuple[float, float | None, int]]:
    if len(bars) < 560:
        return {}
    timing_action = None
    if action in {InvestmentAction.BUY, InvestmentAction.ADD}:
        timing_action = TimingAction.ENTRY_WATCH.value
    elif action is InvestmentAction.SELL:
        timing_action = TimingAction.EXIT_WATCH.value
    if timing_action is None:
        return {}
    signal_start_index = max(520, len(bars) - 760)
    signal_end_index = len(bars) - max(horizons) - 1
    if signal_start_index >= signal_end_index:
        return {}
    signals = validate_historical_signals(
        bars,
        bars[signal_start_index].trade_date,
        bars[signal_end_index].trade_date,
        horizons=horizons,
        min_history_bars=520,
        analysis_window_bars=800,
    )
    statistics = summarize_signal_statistics(signals, horizons)
    return {
        item.trading_days: (
            float(item.favorable_rate_pct),
            item.average_return_pct,
            item.sample_count,
        )
        for item in statistics
        if item.action == timing_action
        and item.sample_count > 0
        and item.favorable_rate_pct is not None
    }


def _confidence(
    analysis: MultiTimeframeAnalysis,
    action: InvestmentAction,
    maximum_samples: int,
) -> int:
    base = analysis.timing.strength
    if action in {InvestmentAction.HOLD, InvestmentAction.WAIT}:
        base = max(35, min(65, 45 + abs(analysis.direction_score) * 2))
    if action is InvestmentAction.AVOID and analysis.timing.action == TimingAction.WAIT.value:
        base = 55
    if maximum_samples:
        base = round(base * 0.8 + min(90, 50 + maximum_samples * 4) * 0.2)
    return max(20, min(95, base))


def _confidence_label(value: int) -> str:
    if value >= 80:
        return "高"
    if value >= 60:
        return "中等"
    return "低"


def build_investment_advice(
    symbol: str,
    bars_input: tuple[PriceBar, ...] | list[PriceBar],
    indicators: IndicatorSet,
    analysis: MultiTimeframeAnalysis,
    *,
    position: Position | None = None,
    macro_overlay: MacroOverlay | None = None,
    market_context: MarketConfluence | None = None,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> InvestmentAdvice:
    bars = tuple(bars_input)
    if not bars:
        raise ValueError("生成投资结论至少需要一根日 K")
    if not horizons or any(item < 1 for item in horizons):
        raise ValueError("预测周期必须是正交易日")
    action = _action_for(analysis, position is not None, market_context)
    current = bars[-1].close
    atr = indicators.atr[20][-1]
    atr_value = float(atr) if atr is not None else None
    history = _historical_statistics(bars, action, horizons)
    forecasts: list[PriceForecast] = []
    for horizon in horizons:
        rule_up = _rule_up_probability(analysis, horizon, market_context)
        direction = _forecast_direction(rule_up)
        probability = None
        scenario_score: float | None = rule_up
        expected_return = None
        samples = 0
        basis = "rule_score"
        basis_label = "MACD/WR 规则情景分，不是统计胜率"
        historical = history.get(horizon)
        if historical is not None:
            favorable_rate, expected_return, samples = historical
            if action in {InvestmentAction.BUY, InvestmentAction.ADD}:
                direction = ForecastDirection.UP
            else:
                direction = ForecastDirection.DOWN
            probability = favorable_rate
            scenario_score = None
            basis = "historical_signal_rate"
            basis_label = "同规则历史信号的无未来函数条件统计"
        envelope = atr_value * math.sqrt(horizon) if atr_value is not None else None
        forecasts.append(
            PriceForecast(
                trading_days=horizon,
                direction=direction.value,
                direction_label=direction.label,
                probability_pct=(round(probability, 2) if probability is not None else None),
                scenario_score=(round(scenario_score, 2) if scenario_score is not None else None),
                expected_return_pct=(
                    round(float(expected_return), 4) if expected_return is not None else None
                ),
                price_range_low=(round(max(0.01, current - envelope), 4) if envelope else None),
                price_range_high=(round(current + envelope, 4) if envelope else None),
                sample_count=samples,
                basis=basis,
                basis_label=basis_label,
            )
        )
    maximum_samples = max((item.sample_count for item in forecasts), default=0)
    technical_confidence = _confidence(analysis, action, maximum_samples)
    market_adjustment = market_context.confidence_adjustment if market_context is not None else 0
    macro_adjustment = 0
    if macro_overlay is not None:
        if action in {InvestmentAction.BUY, InvestmentAction.ADD, InvestmentAction.HOLD}:
            macro_adjustment = macro_overlay.confidence_adjustment
        elif action in {
            InvestmentAction.REDUCE,
            InvestmentAction.SELL,
            InvestmentAction.AVOID,
        }:
            macro_adjustment = -macro_overlay.confidence_adjustment
    combined_confidence = max(
        20, min(95, technical_confidence + market_adjustment + macro_adjustment)
    )

    action_zone_low = None
    action_zone_high = None
    invalidation_price = None
    invalidation_condition = "等待 MACD/WR 形成明确触发后重新评估"
    if atr_value is not None and action in {InvestmentAction.BUY, InvestmentAction.ADD}:
        action_zone_low = round(max(0.01, current - atr_value * 0.5), 4)
        action_zone_high = round(current + atr_value * 0.25, 4)
        invalidation_price = round(max(0.01, current - atr_value * 2), 4)
        invalidation_condition = (
            f"收盘跌破 ATR 风险参考价 {invalidation_price:.4f}，或周/月 MACD 转弱时，"
            "本次上涨/买入逻辑失效"
        )
    elif atr_value is not None and action in {
        InvestmentAction.REDUCE,
        InvestmentAction.SELL,
        InvestmentAction.AVOID,
    }:
        action_zone_low = round(max(0.01, current - atr_value * 0.25), 4)
        action_zone_high = round(current + atr_value * 0.5, 4)
        invalidation_price = round(current + atr_value * 2, 4)
        invalidation_condition = (
            f"收盘重新站上 ATR 风险参考价 {invalidation_price:.4f}，且高周期 MACD 风险解除时，"
            "本次下跌/退出逻辑失效"
        )

    context_reasons = market_context.reasons if market_context is not None else ()
    evidence = tuple(
        dict.fromkeys((*analysis.timing.reasons, *analysis.risk_flags, *context_reasons))
    )
    thesis_items = [
        f"月线 MACD 锚定大方向、周线判断延续或调整、日线辅助执行；"
        f"当前为{analysis.direction_label}，排序分 {analysis.direction_score:+d}。",
        "WR 使用正向 0–100 口径：85–90 以上为深度/极度超卖，20 以下为超买。",
        f"日线 WR 不决定大方向，只确认具体时机；当前为“{analysis.timing.label}”，"
        f"因此给出“{action.label}”结论。",
    ]
    if market_context is not None:
        benchmark = market_context.benchmark
        benchmark_direction = (
            benchmark.strategy.direction_label if benchmark.strategy is not None else "不可用"
        )
        sector_label = "尚未按需加载"
        if market_context.sector is not None:
            sector_label = (
                market_context.sector.strategy.direction_label
                if market_context.sector.strategy is not None
                else "加载失败"
            )
        thesis_items.append(
            f"市场共振门控：{benchmark.instrument.name}为{benchmark_direction}，"
            f"板块为{sector_label}；结论“{market_context.priority_label}”，"
            f"置信度调整 {market_adjustment:+d}。买入/加仓必须通过门控，退出动作不被覆盖。"
        )
    if macro_overlay is not None:
        thesis_items.append(
            f"用户标注行业“{macro_overlay.industry or '未填写'}”映射为"
            f"“{macro_overlay.matched_sector} / {macro_overlay.stance_label}”；"
            f"宏观只调整置信度 {macro_adjustment:+d}，不覆盖技术动作。"
        )
    thesis = tuple(thesis_items)
    return InvestmentAdvice(
        version="macd-wr-investment-decision-2026.08.4",
        symbol=symbol,
        as_of=bars[-1].trade_date.isoformat(),
        action=action,
        action_label=action.label,
        confidence=combined_confidence,
        confidence_label=_confidence_label(combined_confidence),
        technical_confidence=technical_confidence,
        market_confidence_adjustment=market_adjustment,
        macro_confidence_adjustment=macro_adjustment,
        market_context=(market_context.to_dict() if market_context is not None else {}),
        macro_overlay=(macro_overlay.to_dict() if macro_overlay is not None else {}),
        direction=analysis.direction,
        direction_label=analysis.direction_label,
        current_price=round(current, 4),
        action_zone_low=action_zone_low,
        action_zone_high=action_zone_high,
        invalidation_price=invalidation_price,
        invalidation_condition=invalidation_condition,
        forecasts=tuple(forecasts),
        thesis=thesis,
        evidence=evidence,
        risk_controls=(
            "建议只在规则触发后的下一交易时段自行复核，不自动下单。",
            "ATR 区间是波动风险参考，不是目标价或收益承诺。",
            "周/月线未收盘状态会变化；收盘后应重新计算。",
            "板块使用中证一级行业指数作为趋势代理，不等同于公司基本面。",
            "宏观行业映射只使用用户填写的行业标签，不自动猜测公司所属行业。",
        ),
        limitations=(
            "该结论是可回测的规则型研究建议，不保证未来结果。",
            "样本数不足时显示的是规则情景分；只有 historical_signal_rate 才是历史条件统计。",
            "公开历史或模拟数据可能存在滞后、缺失和复权差异，实盘前必须核对最新行情。",
        ),
    )


def build_investment_advice_summary(advice: InvestmentAdvice) -> str:
    zone = "暂无动作区间"
    if advice.action_zone_low is not None and advice.action_zone_high is not None:
        zone = f"{advice.action_zone_low:.4f}–{advice.action_zone_high:.4f}"
    lines = [
        "## 投资结论（MACD 大方向 + WR 时机）",
        f"- 证券/截止：{advice.symbol} / {advice.as_of}",
        f"- 建议动作：{advice.action_label}（规则置信度 {advice.confidence}/100，"
        f"{advice.confidence_label}）",
        f"- 置信度拆分：技术 {advice.technical_confidence}/100；"
        f"大盘/板块调整 {advice.market_confidence_adjustment:+d}；"
        f"宏观行业调整 {advice.macro_confidence_adjustment:+d}",
        f"- 大方向：{advice.direction_label}",
        f"- 当前价/动作参考区：{advice.current_price:.4f} / {zone}",
        f"- 失效条件：{advice.invalidation_condition}",
        f"- 市场共振：{advice.market_context.get('priority_label', '未启用')}",
        "",
        "### 方向预测",
    ]
    for item in advice.forecasts:
        expected = (
            "无可靠收益估计"
            if item.expected_return_pct is None
            else f"历史平均收益 {item.expected_return_pct:+.2f}%"
        )
        price_range = (
            "ATR 不足"
            if item.price_range_low is None or item.price_range_high is None
            else f"ATR 风险区间 {item.price_range_low:.4f}–{item.price_range_high:.4f}"
        )
        if item.probability_pct is not None:
            measure = f"历史同条件命中率 {item.probability_pct:.2f}%"
        elif item.scenario_score is not None:
            measure = f"上涨情景分 {item.scenario_score:.2f}/100（非概率）"
        else:
            measure = "无可用概率或情景分"
        lines.append(
            f"- {item.trading_days} 日：{item.direction_label}，{measure}；{expected}；"
            f"{price_range}；{item.basis_label}（样本 {item.sample_count}）"
        )
    lines.extend(("", "### 决策逻辑"))
    lines.extend(f"- {item}" for item in advice.thesis)
    lines.extend(("", "### 触发证据"))
    lines.extend(f"- {item}" for item in advice.evidence)
    lines.extend(("", "### 风险控制"))
    lines.extend(f"- {item}" for item in advice.risk_controls)
    lines.extend(("", "### 方法边界"))
    lines.extend(f"- {item}" for item in advice.limitations)
    return "\n".join(lines)
