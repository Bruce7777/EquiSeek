from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from aegisrun.marketdata.indicators import IndicatorSet
from aegisrun.marketdata.models import MarketDataSet
from aegisrun.research.advice import (
    InvestmentAdvice,
    build_investment_advice_summary,
)
from aegisrun.research.signals import MultiTimeframeAnalysis, build_signal_summary

if TYPE_CHECKING:
    from aegisrun.research.market_context import MarketConfluence


def _latest(values: tuple[float | None, ...]) -> float | None:
    return values[-1] if values else None


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    symbol: str
    as_of: date
    start_date: date
    source: str
    source_kind: str
    adjustment: str
    formula_version: str
    bars: int
    latest_close: float
    previous_close: float | None
    indicators: dict[str, float | None]
    strategy: dict[str, Any]
    investment_advice: dict[str, Any]
    market_context: dict[str, Any]
    warnings: tuple[str, ...]
    cache_status: str = "disabled"
    cache_hit_bars: int = 0
    cache_added_bars: int = 0
    network_rows: int = 0
    fetch_ranges: tuple[str, ...] = ()

    def to_prompt_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        payload["start_date"] = self.start_date.isoformat()
        return payload


def build_research_snapshot(
    data: MarketDataSet,
    indicators: IndicatorSet,
    strategy: MultiTimeframeAnalysis | None = None,
    advice: InvestmentAdvice | None = None,
    market_context: MarketConfluence | None = None,
) -> ResearchSnapshot:
    latest = data.bars[-1]
    previous = data.bars[-2].close if len(data.bars) > 1 else latest.pre_close
    values = {
        **{f"MA{period}": _rounded(_latest(series)) for period, series in indicators.ma.items()},
        "DIF": _rounded(_latest(indicators.dif)),
        "DEA": _rounded(_latest(indicators.dea)),
        "MACD": _rounded(_latest(indicators.macd)),
        "K": _rounded(_latest(indicators.k)),
        "D": _rounded(_latest(indicators.d)),
        "J": _rounded(_latest(indicators.j)),
        **{f"RSI{period}": _rounded(_latest(series)) for period, series in indicators.rsi.items()},
        **{f"ATR{period}": _rounded(_latest(series)) for period, series in indicators.atr.items()},
        "BOLL_MID": _rounded(_latest(indicators.boll_mid)),
        "BOLL_UPPER": _rounded(_latest(indicators.boll_upper)),
        "BOLL_LOWER": _rounded(_latest(indicators.boll_lower)),
        **{f"WR{period}": _rounded(_latest(series)) for period, series in indicators.wr.items()},
    }
    return ResearchSnapshot(
        symbol=data.symbol,
        as_of=data.as_of,
        start_date=data.bars[0].trade_date,
        source=data.source,
        source_kind="synthetic" if data.is_synthetic else "public-history",
        adjustment=data.adjustment.value,
        formula_version=indicators.version,
        bars=len(data.bars),
        latest_close=round(latest.close, 4),
        previous_close=round(previous, 4) if previous is not None else None,
        indicators=values,
        strategy=strategy.to_dict() if strategy is not None else {},
        investment_advice=advice.to_dict() if advice is not None else {},
        market_context=market_context.to_dict() if market_context is not None else {},
        warnings=data.warnings,
        cache_status=data.cache_status,
        cache_hit_bars=data.cache_hit_bars,
        cache_added_bars=data.cache_added_bars,
        network_rows=data.network_rows,
        fetch_ranges=data.fetch_ranges,
    )


def _format(value: float | None) -> str:
    return "数据不足" if value is None else f"{value:.2f}"


def build_objective_summary(
    snapshot: ResearchSnapshot,
    strategy: MultiTimeframeAnalysis | None = None,
    advice: InvestmentAdvice | None = None,
) -> str:
    ma5 = snapshot.indicators["MA5"]
    ma20 = snapshot.indicators["MA20"]
    relationship = "无法比较"
    if ma5 is not None and ma20 is not None:
        relationship = "高于" if ma5 > ma20 else "低于" if ma5 < ma20 else "等于"
    source_label = (
        f"{snapshot.source}（{'合成演示' if snapshot.source_kind == 'synthetic' else '公开历史'}）"
    )
    cache_label = {
        "disabled": "未启用",
        "hit": f"完整命中，本地读取 {snapshot.cache_hit_bars} 根",
        "miss": f"首次写入，新增 {snapshot.cache_added_bars} 根",
        "partial": (
            f"增量补齐，命中 {snapshot.cache_hit_bars} 根、新增 {snapshot.cache_added_bars} 根"
        ),
        "rebuilt": f"同源历史变化后重建 {snapshot.cache_added_bars} 根",
    }.get(snapshot.cache_status, snapshot.cache_status)
    warnings = "\n".join(f"- {warning}" for warning in snapshot.warnings)
    base = (
        f"## 数据快照\n\n- 证券：{snapshot.symbol}\n- 截止日期：{snapshot.as_of.isoformat()}\n"
        f"- 来源：{source_label}\n- 复权：{snapshot.adjustment}\n- 样本：{snapshot.bars} 个日 K\n"
        f"- 本地缓存：{cache_label}\n"
        f"- 公式版本：{snapshot.formula_version}\n\n## 历史指标事实\n\n"
        f"- 收盘价：{snapshot.latest_close:.2f}\n"
        f"- MA5：{_format(ma5)}；MA20：{_format(ma20)}；MA5 {relationship} MA20。\n"
        f"- MACD：{_format(snapshot.indicators['MACD'])}；"
        f"DIF：{_format(snapshot.indicators['DIF'])}；"
        f"DEA：{_format(snapshot.indicators['DEA'])}。\n"
        f"- K/D/J：{_format(snapshot.indicators['K'])} / {_format(snapshot.indicators['D'])} / "
        f"{_format(snapshot.indicators['J'])}。\n"
        f"- RSI6/12/24：{_format(snapshot.indicators['RSI6'])} / "
        f"{_format(snapshot.indicators['RSI12'])} / {_format(snapshot.indicators['RSI24'])}。\n"
        f"- ATR20：{_format(snapshot.indicators['ATR20'])}；"
        f"WR10：{_format(snapshot.indicators['WR10'])}。\n"
        f"- BOLL 下/中/上轨：{_format(snapshot.indicators['BOLL_LOWER'])} / "
        f"{_format(snapshot.indicators['BOLL_MID'])} / "
        f"{_format(snapshot.indicators['BOLL_UPPER'])}。\n"
    )
    signal = f"\n{build_signal_summary(strategy)}\n" if strategy is not None else ""
    decision = f"\n{build_investment_advice_summary(advice)}\n" if advice is not None else ""
    boundary = (
        "- 投资动作与方向预测是规则型研究结论，不保证未来结果，系统不连接券商且不会自动下单。"
        if advice is not None
        else "- 以上内容只陈述历史行情统计和公式关系，不预测未来价格，不提供买卖建议。"
    )
    return f"{base}{signal}{decision}\n## 数据与方法提示\n\n{warnings}\n{boundary}"
