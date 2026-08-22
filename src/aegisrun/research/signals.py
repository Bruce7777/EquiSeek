from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from aegisrun.marketdata.indicators import SignalIndicatorSet, calculate_signal_indicators
from aegisrun.marketdata.models import PriceBar
from aegisrun.marketdata.timeframes import Timeframe, aggregate_bars


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGE = "range"
    INSUFFICIENT = "insufficient"

    @property
    def label(self) -> str:
        return {
            self.BULLISH: "上涨结构",
            self.BEARISH: "下跌结构",
            self.RANGE: "方向分歧/震荡",
            self.INSUFFICIENT: "数据不足",
        }[self]


class TimingAction(StrEnum):
    ENTRY_WATCH = "entry_watch"
    EXIT_WATCH = "exit_watch"
    RISK_WATCH = "risk_watch"
    WAIT = "wait"
    INSUFFICIENT = "insufficient"

    @property
    def label(self) -> str:
        return {
            self.ENTRY_WATCH: "技术入场观察窗口",
            self.EXIT_WATCH: "技术退出观察窗口",
            self.RISK_WATCH: "调整风险观察",
            self.WAIT: "等待多周期共振",
            self.INSUFFICIENT: "数据不足",
        }[self]


class WrZone(StrEnum):
    EXTREME_OVERSOLD = "extreme_oversold"
    DEEP_OVERSOLD = "deep_oversold"
    OVERSOLD = "oversold"
    NEUTRAL = "neutral"
    OVERBOUGHT = "overbought"

    @property
    def label(self) -> str:
        return {
            self.EXTREME_OVERSOLD: "极度超卖",
            self.DEEP_OVERSOLD: "深度超卖",
            self.OVERSOLD: "超卖",
            self.NEUTRAL: "中性",
            self.OVERBOUGHT: "超买",
        }[self]


class DecisionStepStatus(StrEnum):
    SATISFIED = "satisfied"
    WARN = "warn"
    BLOCK = "block"

    @property
    def label(self) -> str:
        return {
            self.SATISFIED: "通过",
            self.WARN: "观察",
            self.BLOCK: "风险",
        }[self]


@dataclass(frozen=True, slots=True)
class MacdFrameState:
    timeframe: str
    label: str
    as_of: str
    latest_available_as_of: str
    bars: int
    provisional: bool
    provisional_excluded: bool
    dif: float
    dea: float
    histogram: float
    relation: str
    zero_zone: str
    cross: str
    golden_crosses_above_zero: int
    bearish_divergence: bool
    double_top: bool
    phase: str
    phase_label: str
    score: int


@dataclass(frozen=True, slots=True)
class WrFrameState:
    timeframe: str
    label: str
    value: float | None
    previous: float | None
    zone: str
    zone_label: str
    entered_oversold: bool
    entered_overbought: bool
    as_of: str = ""
    latest_available_as_of: str = ""
    provisional: bool = False
    provisional_excluded: bool = False


@dataclass(frozen=True, slots=True)
class TimingDecision:
    action: str
    label: str
    strength: int
    reasons: tuple[str, ...]
    structure_signal: str = "no_top_risk"
    wr_confirmation: str = "none"


@dataclass(frozen=True, slots=True)
class DecisionStep:
    key: str
    title: str
    status: str
    status_label: str
    summary: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MultiTimeframeAnalysis:
    version: str
    direction: str
    direction_label: str
    direction_score: int
    regime: str
    macd: dict[str, MacdFrameState]
    wr: dict[str, WrFrameState]
    risk_flags: tuple[str, ...]
    timing: TimingDecision
    candidate_score: int
    direction_method: str = "monthly_anchor_weekly_confirmation_daily_execution"
    decision_path: tuple[DecisionStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rounded(value: float) -> float:
    return round(value, 4)


def _relation_label(value: str) -> str:
    return {
        "above_signal": "DIF 在 DEA 上方",
        "below_signal": "DIF 在 DEA 下方",
        "on_signal": "DIF 与 DEA 重合",
    }.get(value, value)


def _zero_zone_label(value: str) -> str:
    return {
        "above_zero": "零轴上方",
        "below_zero": "零轴下方",
        "cross_zero": "零轴附近",
    }.get(value, value)


def _cross_label(value: str) -> str:
    return {"golden": "本周期金叉", "death": "本周期死叉", "none": "无新交叉"}.get(value, value)


def _number(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _local_peaks(values: list[float], *, start: int = 1) -> list[int]:
    return [
        index
        for index in range(max(start, 1), len(values) - 1)
        if values[index] >= values[index - 1] and values[index] > values[index + 1]
    ]


def detect_macd_top_structure(
    price_highs: list[float], dif_values: list[float]
) -> tuple[bool, bool]:
    if len(price_highs) != len(dif_values):
        raise ValueError("price and DIF series must have the same length")
    if len(price_highs) < 12:
        return False, False
    start = max(1, len(price_highs) - 80)
    price_peaks = _local_peaks(price_highs, start=start)
    dif_peaks = _local_peaks(dif_values, start=start)

    bearish_divergence = False
    if len(price_peaks) >= 2:
        first, second = price_peaks[-2:]
        if second - first >= 2:
            bearish_divergence = (
                len(price_highs) - second <= 8
                and price_highs[second] > price_highs[first] * 1.003
                and dif_values[first] > 0
                and dif_values[second] < dif_values[first] * 0.97
            )

    double_top = False
    positive_peaks = [index for index in dif_peaks if dif_values[index] > 0]
    if len(positive_peaks) >= 2:
        first, second = positive_peaks[-2:]
        valley = min(dif_values[first : second + 1])
        lower_peak = min(dif_values[first], dif_values[second])
        double_top = (
            second - first >= 3
            and len(dif_values) - second <= 8
            and dif_values[second] <= dif_values[first] * 1.08
            and valley <= lower_peak * 0.72
        )
    return bearish_divergence, double_top


def _macd_structure(
    bars: tuple[PriceBar, ...], indicators: SignalIndicatorSet
) -> tuple[bool, bool]:
    return detect_macd_top_structure(
        [bar.high for bar in bars],
        [_number(value) for value in indicators.dif],
    )


def _crosses_above_zero(indicators: SignalIndicatorSet) -> int:
    start = max(1, len(indicators.dif) - 60)
    count = 0
    for index in range(start, len(indicators.dif)):
        previous_dif = _number(indicators.dif[index - 1])
        previous_dea = _number(indicators.dea[index - 1])
        dif = _number(indicators.dif[index])
        dea = _number(indicators.dea[index])
        if previous_dif <= previous_dea and dif > dea and dif > 0 and dea > 0:
            count += 1
    return count


def _macd_state(
    timeframe: Timeframe,
    bars: tuple[PriceBar, ...],
    indicators: SignalIndicatorSet,
    *,
    complete: bool,
    latest_available_as_of: str,
    provisional_excluded: bool,
) -> MacdFrameState:
    dif = _number(indicators.dif[-1])
    dea = _number(indicators.dea[-1])
    histogram = _number(indicators.macd[-1])
    previous_dif = _number(indicators.dif[-2]) if len(bars) > 1 else dif
    previous_dea = _number(indicators.dea[-2]) if len(bars) > 1 else dea
    cross = "none"
    if previous_dif <= previous_dea and dif > dea:
        cross = "golden"
    elif previous_dif >= previous_dea and dif < dea:
        cross = "death"
    relation = "above_signal" if dif > dea else "below_signal" if dif < dea else "on_signal"
    zero_zone = (
        "above_zero"
        if dif > 0 and dea > 0
        else "below_zero"
        if dif < 0 and dea < 0
        else "cross_zero"
    )
    score = 0
    if dif > dea:
        score = 2 if dif > 0 and dea > 0 else 1
    elif dif < dea:
        score = -2 if dif < 0 and dea < 0 else -1
    divergence, double_top = _macd_structure(bars, indicators)
    phase, phase_label = _macd_phase(
        relation,
        zero_zone,
        cross,
        divergence,
        double_top,
        _crosses_above_zero(indicators),
    )
    return MacdFrameState(
        timeframe=timeframe.value,
        label=timeframe.label,
        as_of=bars[-1].trade_date.isoformat(),
        latest_available_as_of=latest_available_as_of,
        bars=len(bars),
        provisional=not complete,
        provisional_excluded=provisional_excluded,
        dif=_rounded(dif),
        dea=_rounded(dea),
        histogram=_rounded(histogram),
        relation=relation,
        zero_zone=zero_zone,
        cross=cross,
        golden_crosses_above_zero=_crosses_above_zero(indicators),
        bearish_divergence=divergence,
        double_top=double_top,
        phase=phase,
        phase_label=phase_label,
        score=score,
    )


def _macd_phase(
    relation: str,
    zero_zone: str,
    cross: str,
    bearish_divergence: bool,
    double_top: bool,
    golden_crosses_above_zero: int,
) -> tuple[str, str]:
    if double_top or bearish_divergence:
        return "second_top_risk", "零轴上第二顶部/背离风险"
    if golden_crosses_above_zero >= 2 and relation == "below_signal":
        return "repeated_cross_weakening", "零轴上多次金叉后转弱"
    if cross == "death" and zero_zone == "above_zero":
        return "bullish_death_cross", "上涨区死叉调整"
    if zero_zone == "above_zero" and relation == "above_signal":
        return "bullish_continuation", "零轴上多头延续"
    if zero_zone == "above_zero":
        return "bullish_correction", "上涨大结构内调整"
    if zero_zone == "below_zero" and relation == "below_signal":
        return "bearish_continuation", "零轴下空头延续"
    if zero_zone == "below_zero":
        return "bearish_rebound", "下跌大结构内反弹"
    return "zero_axis_transition", "零轴附近方向切换"


def classify_wr(value: float | None) -> WrZone:
    if value is None:
        return WrZone.NEUTRAL
    if value >= 90:
        return WrZone.EXTREME_OVERSOLD
    if value >= 85:
        return WrZone.DEEP_OVERSOLD
    if value >= 80:
        return WrZone.OVERSOLD
    if value <= 20:
        return WrZone.OVERBOUGHT
    return WrZone.NEUTRAL


def _wr_state(
    timeframe: Timeframe,
    bars: tuple[PriceBar, ...],
    indicators: SignalIndicatorSet,
    *,
    complete: bool,
    latest_available_as_of: str,
    provisional_excluded: bool,
    period: int = 10,
) -> WrFrameState:
    series = indicators.wr[period]
    current = series[-1]
    previous = series[-2] if len(series) > 1 else None
    zone = classify_wr(current)
    previous_zone = classify_wr(previous)
    oversold_zones = {
        WrZone.OVERSOLD,
        WrZone.DEEP_OVERSOLD,
        WrZone.EXTREME_OVERSOLD,
    }
    return WrFrameState(
        timeframe=timeframe.value,
        label=timeframe.label,
        value=None if current is None else round(current, 4),
        previous=None if previous is None else round(previous, 4),
        zone=zone.value,
        zone_label=zone.label,
        entered_oversold=zone in oversold_zones and previous_zone not in oversold_zones,
        entered_overbought=zone is WrZone.OVERBOUGHT and previous_zone is not WrZone.OVERBOUGHT,
        as_of=bars[-1].trade_date.isoformat(),
        latest_available_as_of=latest_available_as_of,
        provisional=not complete,
        provisional_excluded=provisional_excluded,
    )


def resolve_macd_direction(macd: dict[str, MacdFrameState]) -> tuple[Direction, int]:
    """Resolve direction by hierarchy, while retaining a score for ranking.

    Monthly MACD defines the structural anchor, weekly MACD decides whether that
    structure is continuing or adjusting, and daily MACD is only an execution aid.
    This deliberately prevents one strong daily move from overruling the big cycle.
    """

    weights = {Timeframe.DAILY.value: 1, Timeframe.WEEKLY.value: 2, Timeframe.MONTHLY.value: 3}
    score = sum(state.score * weights[key] for key, state in macd.items())
    monthly = macd[Timeframe.MONTHLY.value]
    weekly = macd[Timeframe.WEEKLY.value]
    if monthly.bars < 26:
        return Direction.INSUFFICIENT, score
    if monthly.score >= 2:
        return Direction.BULLISH, score
    if monthly.score <= -2:
        return Direction.BEARISH, score
    if monthly.score == 1 and weekly.score >= 1:
        return Direction.BULLISH, score
    if monthly.score == -1 and weekly.score <= -1:
        return Direction.BEARISH, score
    return Direction.RANGE, score


def _risk_flags(macd: dict[str, MacdFrameState]) -> tuple[str, ...]:
    flags: list[str] = []
    for key in (Timeframe.MONTHLY.value, Timeframe.WEEKLY.value):
        state = macd[key]
        if state.bearish_divergence:
            flags.append(f"{state.label}价格创新高而 DIF 峰值降低（顶背离）")
        if state.double_top:
            flags.append(f"{state.label} DIF 在零轴上形成第二个顶部（双峰结构）")
        if state.cross == "death" and state.zero_zone == "above_zero":
            flags.append(f"{state.label}零轴上死叉")
        if state.golden_crosses_above_zero >= 2 and state.relation == "below_signal":
            flags.append(f"{state.label}零轴上多次金叉后转弱")
    return tuple(dict.fromkeys(flags))


def derive_timing_decision(
    direction: Direction,
    wr: dict[str, WrFrameState],
    risk_flags: tuple[str, ...],
) -> TimingDecision:
    daily = wr[Timeframe.DAILY.value]
    weekly = wr[Timeframe.WEEKLY.value]
    oversold = {
        zone.value for zone in (WrZone.OVERSOLD, WrZone.DEEP_OVERSOLD, WrZone.EXTREME_OVERSOLD)
    }
    if direction is Direction.INSUFFICIENT:
        return TimingDecision(
            TimingAction.INSUFFICIENT.value,
            TimingAction.INSUFFICIENT.label,
            0,
            ("月线不足 26 根，MACD 大方向尚未充分预热",),
            "insufficient",
            "none",
        )
    structure_signal = (
        "high_timeframe_second_top"
        if any("双峰" in item or "顶背离" in item or "第二个顶部" in item for item in risk_flags)
        else "high_timeframe_weakening"
        if risk_flags
        else "no_top_risk"
    )
    if risk_flags and (
        daily.zone == WrZone.OVERBOUGHT.value or weekly.zone == WrZone.OVERBOUGHT.value
    ):
        timing_frame = weekly if weekly.zone == WrZone.OVERBOUGHT.value else daily
        reasons = (
            *risk_flags,
            f"{timing_frame.label} WR10={timing_frame.value}，处于{timing_frame.zone_label}区，"
            "确认高周期顶部结构的退出窗口",
        )
        return TimingDecision(
            TimingAction.EXIT_WATCH.value,
            TimingAction.EXIT_WATCH.label,
            min(100, 70 + len(risk_flags) * 8),
            reasons,
            structure_signal,
            f"{timing_frame.timeframe}_overbought_exit",
        )
    if direction is Direction.BEARISH and daily.zone == WrZone.OVERBOUGHT.value:
        return TimingDecision(
            TimingAction.EXIT_WATCH.value,
            TimingAction.EXIT_WATCH.label,
            72,
            ("MACD 多周期大方向偏弱", f"日线 WR10={daily.value}，处于{daily.zone_label}区"),
            "bearish_regime",
            "daily_overbought_exit",
        )
    if direction is Direction.BULLISH and daily.zone in oversold and not risk_flags:
        strength = 82 if daily.entered_oversold else 70
        if weekly.zone in oversold:
            strength += 10
        return TimingDecision(
            TimingAction.ENTRY_WATCH.value,
            TimingAction.ENTRY_WATCH.label,
            min(strength, 95),
            (
                "月线 MACD 锚定上涨大结构，周线未触发顶部风险",
                f"日线 WR10={daily.value}，处于{daily.zone_label}区",
                (
                    "本根首次进入超卖区"
                    if daily.entered_oversold
                    else "仍处于超卖区，需结合下一根确认"
                ),
            ),
            structure_signal,
            "daily_oversold_entry",
        )
    if risk_flags:
        return TimingDecision(
            TimingAction.RISK_WATCH.value,
            TimingAction.RISK_WATCH.label,
            min(85, 55 + len(risk_flags) * 10),
            (*risk_flags, "顶部/调整结构已成立；等待日线或周线 WR≤20 确认具体退出窗口"),
            structure_signal,
            "awaiting_overbought_exit",
        )
    return TimingDecision(
        TimingAction.WAIT.value,
        TimingAction.WAIT.label,
        40,
        (f"MACD 大方向：{direction.label}", f"日线 WR 当前为{daily.zone_label}"),
        structure_signal,
        "none",
    )


def _decision_path(
    direction: Direction,
    macd: dict[str, MacdFrameState],
    wr: dict[str, WrFrameState],
    risk_flags: tuple[str, ...],
    timing: TimingDecision,
) -> tuple[DecisionStep, ...]:
    monthly = macd[Timeframe.MONTHLY.value]
    weekly = macd[Timeframe.WEEKLY.value]
    daily_wr = wr[Timeframe.DAILY.value]
    weekly_wr = wr[Timeframe.WEEKLY.value]

    monthly_status = (
        DecisionStepStatus.SATISFIED
        if direction is Direction.BULLISH
        else DecisionStepStatus.BLOCK
        if direction is Direction.BEARISH
        else DecisionStepStatus.WARN
    )
    weekly_status = (
        DecisionStepStatus.BLOCK
        if weekly.phase in {"second_top_risk", "repeated_cross_weakening"}
        else DecisionStepStatus.SATISFIED
        if weekly.score > 0
        else DecisionStepStatus.WARN
    )
    top_status = DecisionStepStatus.BLOCK if risk_flags else DecisionStepStatus.SATISFIED
    wr_status = (
        DecisionStepStatus.SATISFIED
        if timing.action in {TimingAction.ENTRY_WATCH.value, TimingAction.EXIT_WATCH.value}
        else DecisionStepStatus.WARN
    )
    final_status = (
        DecisionStepStatus.SATISFIED
        if timing.action == TimingAction.ENTRY_WATCH.value
        else DecisionStepStatus.BLOCK
        if timing.action in {TimingAction.EXIT_WATCH.value, TimingAction.RISK_WATCH.value}
        else DecisionStepStatus.WARN
    )

    def step(
        key: str,
        title: str,
        status: DecisionStepStatus,
        summary: str,
        evidence: tuple[str, ...],
    ) -> DecisionStep:
        return DecisionStep(key, title, status.value, status.label, summary, evidence)

    return (
        step(
            "monthly_direction",
            "① 月线 MACD 定大方向",
            monthly_status,
            f"{direction.label}；{monthly.phase_label}",
            (f"DIF {monthly.dif:.4f} / DEA {monthly.dea:.4f}",),
        ),
        step(
            "weekly_confirmation",
            "② 周线 MACD 判延续/调整",
            weekly_status,
            weekly.phase_label,
            (f"{_cross_label(weekly.cross)}；{_zero_zone_label(weekly.zero_zone)}",),
        ),
        step(
            "top_structure",
            "③ 检查零轴上多次金叉、背离与第二顶部",
            top_status,
            "；".join(risk_flags) if risk_flags else "未发现月/周线顶部结构",
            risk_flags or ("顶部风险闸门未触发",),
        ),
        step(
            "wr_timing",
            "④ WR 只负责具体时机",
            wr_status,
            f"日线 {daily_wr.zone_label}（{daily_wr.value}）；"
            f"周线 {weekly_wr.zone_label}（{weekly_wr.value}）",
            tuple(timing.reasons),
        ),
        step(
            "final_action",
            "⑤ 输出动作与失效条件",
            final_status,
            timing.label,
            (f"触发强度 {timing.strength}/100",),
        ),
    )


def _candidate_score(
    direction: Direction,
    direction_score: int,
    wr: dict[str, WrFrameState],
    risk_flags: tuple[str, ...],
) -> int:
    score = 45 + max(-20, min(20, direction_score * 2))
    daily = wr[Timeframe.DAILY.value]
    weekly = wr[Timeframe.WEEKLY.value]
    if direction is Direction.BULLISH:
        score += 15
    elif direction is Direction.BEARISH:
        score -= 20
    if daily.zone in {WrZone.DEEP_OVERSOLD.value, WrZone.EXTREME_OVERSOLD.value}:
        score += 20
    elif daily.zone == WrZone.OVERSOLD.value:
        score += 12
    if weekly.zone in {
        WrZone.OVERSOLD.value,
        WrZone.DEEP_OVERSOLD.value,
        WrZone.EXTREME_OVERSOLD.value,
    }:
        score += 10
    if daily.zone == WrZone.OVERBOUGHT.value:
        score -= 12
    score -= min(30, len(risk_flags) * 12)
    return max(0, min(100, score))


def analyze_multi_timeframe(
    bars: tuple[PriceBar, ...] | list[PriceBar],
) -> MultiTimeframeAnalysis:
    source_bars = tuple(bars)
    if not source_bars:
        raise ValueError("at least one price bar is required")
    as_of = source_bars[-1].trade_date
    macd: dict[str, MacdFrameState] = {}
    wr: dict[str, WrFrameState] = {}
    for timeframe in (Timeframe.DAILY, Timeframe.WEEKLY, Timeframe.MONTHLY):
        frame = aggregate_bars(source_bars, timeframe, today=as_of)
        provisional_excluded = not frame.latest_complete and len(frame.bars) > 1
        decision_bars = frame.bars[:-1] if provisional_excluded else frame.bars
        indicators = calculate_signal_indicators(decision_bars)
        latest_available_as_of = frame.bars[-1].trade_date.isoformat()
        macd[timeframe.value] = _macd_state(
            timeframe,
            decision_bars,
            indicators,
            complete=frame.latest_complete,
            latest_available_as_of=latest_available_as_of,
            provisional_excluded=provisional_excluded,
        )
        wr[timeframe.value] = _wr_state(
            timeframe,
            decision_bars,
            indicators,
            complete=frame.latest_complete,
            latest_available_as_of=latest_available_as_of,
            provisional_excluded=provisional_excluded,
        )
    direction, direction_score = resolve_macd_direction(macd)
    risks = _risk_flags(macd)
    timing = derive_timing_decision(direction, wr, risks)
    regime = "trend"
    if direction is Direction.BULLISH and risks:
        regime = "bullish_with_adjustment_risk"
    elif direction is Direction.BEARISH:
        regime = "bearish"
    elif direction in {Direction.RANGE, Direction.INSUFFICIENT}:
        regime = direction.value
    return MultiTimeframeAnalysis(
        version="macd-wr-mtf-2026.08.3",
        direction=direction.value,
        direction_label=direction.label,
        direction_score=direction_score,
        regime=regime,
        macd=macd,
        wr=wr,
        risk_flags=risks,
        timing=timing,
        candidate_score=_candidate_score(direction, direction_score, wr, risks),
        decision_path=_decision_path(direction, macd, wr, risks, timing),
    )


def build_signal_summary(analysis: MultiTimeframeAnalysis) -> str:
    lines = [
        "## 多周期技术结构",
        f"- 大方向：{analysis.direction_label}"
        f"（月线锚定、周线确认；排序分 {analysis.direction_score}）",
        f"- 当前窗口：{analysis.timing.label}（强度 {analysis.timing.strength}/100）",
        "- WR 口径：0–100；≥80 超卖、≥85 深度超卖、≤20 超买。",
        "",
        "### MACD 日/周/月",
    ]
    for key in (Timeframe.MONTHLY.value, Timeframe.WEEKLY.value, Timeframe.DAILY.value):
        macd_state = analysis.macd[key]
        provisional = ""
        if macd_state.provisional_excluded:
            provisional = (
                f"（决策采用已收盘 {macd_state.as_of}；形成中 "
                f"{macd_state.latest_available_as_of} 已排除）"
            )
        elif macd_state.provisional:
            provisional = "（尚无前一完整周期，结论仅标记为数据不足）"
        lines.append(
            f"- {macd_state.label}{provisional}：DIF {macd_state.dif:.2f} / "
            f"DEA {macd_state.dea:.2f}；{_relation_label(macd_state.relation)}；"
            f"{_zero_zone_label(macd_state.zero_zone)}；{_cross_label(macd_state.cross)}；"
            f"{macd_state.phase_label}。"
        )
    lines.extend(("", "### WR 日/周/月"))
    for key in (Timeframe.MONTHLY.value, Timeframe.WEEKLY.value, Timeframe.DAILY.value):
        wr_state = analysis.wr[key]
        value = "数据不足" if wr_state.value is None else f"{wr_state.value:.2f}"
        decision_date = f"；决策截止 {wr_state.as_of}" if wr_state.as_of else ""
        excluded = (
            f"；形成中 {wr_state.latest_available_as_of} 已排除"
            if wr_state.provisional_excluded
            else ""
        )
        lines.append(
            f"- {wr_state.label} WR10：{value}；{wr_state.zone_label}{decision_date}{excluded}。"
        )
    lines.extend(("", "### 触发依据"))
    lines.extend(f"- {reason}" for reason in analysis.timing.reasons)
    if analysis.risk_flags:
        lines.extend(("", "### 结构风险"))
        lines.extend(f"- {risk}" for risk in analysis.risk_flags)
    lines.extend(
        (
            "",
            "- 以上为投资决策引擎的确定性技术输入；明确动作见“投资结论”。规则不保证未来结果。",
        )
    )
    return "\n".join(lines)
