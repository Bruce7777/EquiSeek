from __future__ import annotations

from datetime import date

from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.research.market_context import (
    ContextInstrument,
    MarketTrendContext,
    build_market_confluence,
    default_benchmark,
    load_market_trend,
    sector_proxy_for,
)
from aegisrun.research.signals import (
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)


def _analysis(direction: Direction) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        version="test",
        direction=direction.value,
        direction_label=direction.label,
        direction_score=6 if direction is Direction.BULLISH else -6,
        regime="test",
        macd={},
        wr={},
        risk_flags=(),
        timing=TimingDecision(TimingAction.WAIT.value, TimingAction.WAIT.label, 50, ()),
        candidate_score=60,
    )


def _trend(instrument: ContextInstrument, direction: Direction) -> MarketTrendContext:
    data = DemoMarketDataProvider().fetch_daily(
        instrument.symbol,
        date(2023, 1, 1),
        date(2026, 8, 17),
        AdjustmentMode.BFQ,
    )
    return MarketTrendContext(instrument, data, _analysis(direction))


def test_benchmark_selection_matches_listing_board() -> None:
    assert default_benchmark("600519.SH").symbol == "000001.SH"
    assert default_benchmark("000001.SZ").symbol == "399001.SZ"
    assert default_benchmark("300750.SZ").symbol == "399006.SZ"
    assert default_benchmark("688981.SH").symbol == "000688.SH"
    assert default_benchmark("920001.BJ").symbol == "000001.SH"


def test_sector_proxy_mapping_is_explicit_and_does_not_guess_unknown_labels() -> None:
    semiconductor = sector_proxy_for("半导体设备")
    assert semiconductor is not None
    assert semiconductor.symbol == "000935.SH"
    assert semiconductor.proxy_for == "信息技术"
    optional_consumer = sector_proxy_for("可选消费")
    assert optional_consumer is not None
    assert optional_consumer.symbol == "000931.SH"
    assert sector_proxy_for("无法识别的冷门主题") is None


def test_market_and_sector_alignment_prioritizes_three_layer_confluence() -> None:
    stock = _analysis(Direction.BULLISH)
    market = _trend(default_benchmark("600519.SH"), Direction.BULLISH)
    sector_spec = sector_proxy_for("半导体")
    assert sector_spec is not None
    sector = _trend(sector_spec, Direction.BULLISH)

    pending = build_market_confluence(stock, market)
    aligned = build_market_confluence(stock, market, sector)

    assert pending.status == "market_aligned_sector_pending"
    assert "待板块确认" in pending.priority_label
    assert aligned.status == "full_aligned"
    assert aligned.confidence_adjustment > pending.confidence_adjustment
    assert "优先候选" in aligned.priority_label


def test_bearish_market_or_sector_blocks_bullish_priority() -> None:
    stock = _analysis(Direction.BULLISH)
    bearish_market = _trend(default_benchmark("600519.SH"), Direction.BEARISH)
    market_blocked = build_market_confluence(stock, bearish_market)
    assert market_blocked.status == "market_divergent"
    assert market_blocked.buy_gate_open is False

    bullish_market = _trend(default_benchmark("600519.SH"), Direction.BULLISH)
    sector_spec = sector_proxy_for("银行")
    assert sector_spec is not None
    bearish_sector = _trend(sector_spec, Direction.BEARISH)
    sector_blocked = build_market_confluence(stock, bullish_market, bearish_sector)
    assert sector_blocked.status == "sector_divergent"
    assert sector_blocked.buy_gate_open is False

    unavailable_sector = MarketTrendContext.unavailable(sector_spec, "network unavailable")
    unavailable_blocked = build_market_confluence(stock, bullish_market, unavailable_sector)
    assert unavailable_blocked.status == "sector_unavailable"
    assert unavailable_blocked.buy_gate_open is False


def test_context_loader_uses_unadjusted_same_source_history() -> None:
    trend = load_market_trend(
        DemoMarketDataProvider(),
        default_benchmark("600519.SH"),
        date(2023, 1, 1),
        date(2026, 8, 17),
    )

    assert trend.available is True
    assert trend.data is not None
    assert trend.data.adjustment is AdjustmentMode.BFQ
    assert trend.data.source == "synthetic-demo"
    assert trend.strategy is not None
