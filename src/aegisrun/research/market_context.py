from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet
from aegisrun.marketdata.providers import MarketDataProvider
from aegisrun.research.signals import Direction, MultiTimeframeAnalysis, analyze_multi_timeframe


@dataclass(frozen=True, slots=True)
class ContextInstrument:
    kind: str
    symbol: str
    name: str
    proxy_for: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SectorProxy:
    instrument: ContextInstrument
    keywords: tuple[str, ...]


_BENCHMARKS = {
    "sse": ContextInstrument("benchmark", "000001.SH", "上证综指"),
    "szse": ContextInstrument("benchmark", "399001.SZ", "深证成指"),
    "chinext": ContextInstrument("benchmark", "399006.SZ", "创业板指"),
    "star": ContextInstrument("benchmark", "000688.SH", "科创50"),
}


SECTOR_PROXIES: tuple[SectorProxy, ...] = (
    SectorProxy(
        ContextInstrument(
            "sector", "000935.SH", "中证信息技术指数", "信息技术", "中证800一级行业指数"
        ),
        ("半导体", "芯片", "集成电路", "电子", "软件", "计算机", "信息技术", "人工智能"),
    ),
    SectorProxy(
        ContextInstrument(
            "sector", "000933.SH", "中证医药卫生指数", "医药卫生", "中证800一级行业指数"
        ),
        ("医疗", "医药", "创新药", "生物", "医疗器械"),
    ),
    SectorProxy(
        ContextInstrument("sector", "000974.SH", "中证800金融指数", "金融", "中证800一级行业指数"),
        ("银行", "证券", "券商", "保险", "金融"),
    ),
    SectorProxy(
        ContextInstrument(
            "sector", "000931.SH", "中证可选消费指数", "可选消费", "中证800一级行业指数"
        ),
        ("家电", "家用电器", "汽车", "新能源车", "电动车", "耐用消费", "可选消费"),
    ),
    SectorProxy(
        ContextInstrument(
            "sector", "000932.SH", "中证主要消费指数", "主要消费", "中证800一级行业指数"
        ),
        ("消费", "食品饮料", "白酒", "酒类", "酿酒", "农业", "零售"),
    ),
    SectorProxy(
        ContextInstrument("sector", "000928.SH", "中证能源指数", "能源", "中证800一级行业指数"),
        ("能源", "煤炭", "石油", "天然气", "油气"),
    ),
    SectorProxy(
        ContextInstrument(
            "sector", "399965.SZ", "中证800地产指数", "房地产", "中证800一级行业指数"
        ),
        ("地产", "房地产"),
    ),
)


def default_benchmark(stock_symbol: str) -> ContextInstrument:
    normalized = stock_symbol.strip().upper()
    digits = normalized.split(".", 1)[0]
    if normalized.endswith(".SZ") and digits.startswith(("300", "301")):
        return _BENCHMARKS["chinext"]
    if normalized.endswith(".SH") and digits.startswith(("688", "689")):
        return _BENCHMARKS["star"]
    if normalized.endswith(".SZ"):
        return _BENCHMARKS["szse"]
    if normalized.endswith(".BJ"):
        benchmark = _BENCHMARKS["sse"]
        return ContextInstrument(
            benchmark.kind,
            benchmark.symbol,
            benchmark.name,
            note="北交所宽基在当前公共源不可统一获取，使用上证综指作为全市场风险代理",
        )
    return _BENCHMARKS["sse"]


def sector_proxy_for(industry: str) -> ContextInstrument | None:
    normalized = "".join(industry.strip().lower().split())[:80]
    if not normalized:
        return None
    for proxy in SECTOR_PROXIES:
        if any(keyword.lower() in normalized for keyword in proxy.keywords):
            return proxy.instrument
    return None


@dataclass(frozen=True, slots=True)
class MarketTrendContext:
    instrument: ContextInstrument
    data: MarketDataSet | None
    strategy: MultiTimeframeAnalysis | None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.data is not None and self.strategy is not None and self.error is None

    @classmethod
    def unavailable(
        cls, instrument: ContextInstrument, error: Exception | str
    ) -> MarketTrendContext:
        message = str(error).strip() or type(error).__name__
        return cls(instrument, None, None, message[:500])

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.to_dict(),
            "available": self.available,
            "error": self.error,
            "source": self.data.source if self.data is not None else None,
            "as_of": self.data.as_of.isoformat() if self.data is not None else None,
            "bars": len(self.data.bars) if self.data is not None else 0,
            "adjustment": self.data.adjustment.value if self.data is not None else None,
            "cache_status": self.data.cache_status if self.data is not None else None,
            "direction": self.strategy.direction if self.strategy is not None else None,
            "direction_label": self.strategy.direction_label if self.strategy is not None else None,
            "direction_score": self.strategy.direction_score if self.strategy is not None else None,
            "timing": self.strategy.timing.action if self.strategy is not None else None,
            "timing_label": self.strategy.timing.label if self.strategy is not None else None,
        }


def load_market_trend(
    provider: MarketDataProvider,
    instrument: ContextInstrument,
    start_date: date,
    end_date: date,
) -> MarketTrendContext:
    data = provider.fetch_daily(instrument.symbol, start_date, end_date, AdjustmentMode.BFQ)
    if len(data.bars) < 30:
        raise ValueError(f"{instrument.name} 至少需要 30 个有效交易日")
    return MarketTrendContext(instrument, data, analyze_multi_timeframe(data.bars))


@dataclass(frozen=True, slots=True)
class MarketConfluence:
    version: str
    benchmark: MarketTrendContext
    sector: MarketTrendContext | None
    market_alignment: bool | None
    sector_alignment: bool | None
    status: str
    status_label: str
    priority_label: str
    confidence_adjustment: int
    buy_gate_open: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "benchmark": self.benchmark.to_dict(),
            "sector": self.sector.to_dict() if self.sector is not None else None,
            "market_alignment": self.market_alignment,
            "sector_alignment": self.sector_alignment,
            "status": self.status,
            "status_label": self.status_label,
            "priority_label": self.priority_label,
            "confidence_adjustment": self.confidence_adjustment,
            "buy_gate_open": self.buy_gate_open,
            "reasons": list(self.reasons),
        }


def _aligned(stock: MultiTimeframeAnalysis, context: MarketTrendContext | None) -> bool | None:
    if context is None or not context.available or context.strategy is None:
        return None
    return stock.direction == context.strategy.direction


def build_market_confluence(
    stock: MultiTimeframeAnalysis,
    benchmark: MarketTrendContext,
    sector: MarketTrendContext | None = None,
) -> MarketConfluence:
    market_alignment = _aligned(stock, benchmark)
    sector_alignment = _aligned(stock, sector)
    benchmark_direction = benchmark.strategy.direction if benchmark.strategy is not None else None
    sector_direction = (
        sector.strategy.direction if sector is not None and sector.strategy is not None else None
    )
    reasons: list[str] = []

    if not benchmark.available:
        status = "market_unavailable"
        label = "大盘数据不可用"
        priority = "等待大盘确认"
        adjustment = -8
        buy_gate = False
        reasons.append(f"{benchmark.instrument.name} 未完成：{benchmark.error or '数据不足'}")
    elif (
        stock.direction == Direction.BULLISH.value
        and benchmark_direction != Direction.BULLISH.value
    ):
        status = "market_divergent"
        label = "个股与大盘逆向"
        priority = "暂缓买入（大盘未同步）"
        adjustment = -12
        buy_gate = False
        reasons.append(
            f"个股为{stock.direction_label}，但{benchmark.instrument.name}为"
            f"{benchmark.strategy.direction_label if benchmark.strategy else '未知'}"
        )
    elif stock.direction == Direction.BULLISH.value and sector is None:
        status = "market_aligned_sector_pending"
        label = "个股与大盘同步"
        priority = "初筛通过 · 待板块确认"
        adjustment = 6
        buy_gate = True
        reasons.append(f"个股与{benchmark.instrument.name}方向同步，板块代理尚未加载")
    elif stock.direction == Direction.BULLISH.value and sector is not None and (
        not sector.available or sector_direction != Direction.BULLISH.value
    ):
        status = (
            "sector_unavailable"
            if sector is not None and not sector.available
            else "sector_divergent"
        )
        label = "板块未确认" if status == "sector_unavailable" else "个股与板块逆向"
        priority = "暂缓买入（板块未同步）"
        adjustment = -8
        buy_gate = False
        if sector is not None and not sector.available:
            reasons.append(f"板块代理未完成：{sector.error or '数据不足'}")
        elif sector is not None and sector.strategy is not None:
            reasons.append(
                f"个股和{benchmark.instrument.name}向上，但{sector.instrument.name}为"
                f"{sector.strategy.direction_label}"
            )
    elif stock.direction == Direction.BULLISH.value:
        status = "full_aligned"
        label = "个股/大盘/板块三层共振"
        priority = "三层共振 · 优先候选"
        adjustment = 12
        buy_gate = True
        assert sector is not None
        reasons.append(f"个股、{benchmark.instrument.name}和{sector.instrument.name}方向均为向上")
    elif market_alignment is True:
        status = "risk_aligned"
        label = "个股与大盘风险共振"
        priority = "非买入候选 · 优先处理风险"
        adjustment = 8
        buy_gate = False
        reasons.append(f"个股与{benchmark.instrument.name}同向，但当前不是上涨结构")
    else:
        status = "stock_not_bullish"
        label = "个股方向未转强"
        priority = "非优先候选"
        adjustment = -4
        buy_gate = False
        reasons.append("个股自身高周期方向未转强，不进入共振买入候选")

    return MarketConfluence(
        version="market-sector-confluence-2026.08.1",
        benchmark=benchmark,
        sector=sector,
        market_alignment=market_alignment,
        sector_alignment=sector_alignment,
        status=status,
        status_label=label,
        priority_label=priority,
        confidence_adjustment=adjustment,
        buy_gate_open=buy_gate,
        reasons=tuple(reasons),
    )
