from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, date, datetime, timedelta

from aegisrun.marketdata.cache import MarketDataCache
from aegisrun.marketdata.cached_provider import CachedMarketDataProvider
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar


class RecordingProvider:
    def __init__(self, source_name: str = "recording", *, scale: float = 1.0) -> None:
        self.source_name = source_name
        self.scale = scale
        self.calls: list[tuple[str, date, date, AdjustmentMode]] = []

    def fetch_daily(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: AdjustmentMode,
    ) -> MarketDataSet:
        self.calls.append((symbol, start_date, end_date, adjustment))
        bars: list[PriceBar] = []
        cursor = start_date
        previous: float | None = None
        while cursor <= end_date:
            if cursor.weekday() < 5:
                close = (100 + (cursor - date(2026, 1, 1)).days * 0.1) * self.scale
                bars.append(
                    PriceBar(
                        cursor,
                        close - 0.2,
                        close + 0.8,
                        close - 0.8,
                        close,
                        10_000,
                        close * 10_000,
                        previous,
                    )
                )
                previous = close
            cursor += timedelta(days=1)
        if not bars:
            raise ValueError("source returned no trading bars")
        return MarketDataSet(
            symbol=symbol,
            source=self.source_name,
            adjustment=adjustment,
            bars=tuple(bars),
            fetched_at=datetime.now(UTC).isoformat(),
            warnings=(f"source={self.source_name}",),
        )


def test_cache_first_fetch_then_same_range_is_network_free(tmp_path) -> None:
    provider = RecordingProvider()
    cache = MarketDataCache(tmp_path / "market.sqlite3")
    cached = CachedMarketDataProvider(provider, cache)

    first = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )
    second = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )

    assert len(provider.calls) == 1
    assert first.cache_status == "miss"
    assert first.cache_added_bars == len(first.bars)
    assert first.network_rows == len(first.bars)
    assert second.cache_status == "hit"
    assert second.cache_hit_bars == len(second.bars)
    assert second.cache_added_bars == 0
    assert second.network_rows == 0
    assert second.bars == first.bars
    assert second.cache_path == str(cache.path)
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600


def test_cache_refuses_unknown_future_schema_instead_of_downgrading_it(tmp_path) -> None:
    path = tmp_path / "market.sqlite3"
    MarketDataCache(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE market_cache_meta SET value='999' WHERE key='schema_version'"
        )
        connection.commit()

    try:
        MarketDataCache(path)
    except RuntimeError as error:
        assert "不支持的本地行情缓存版本：999" in str(error)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("future cache schemas must never be silently downgraded")


def test_cache_extension_fetches_only_tail_with_one_overlap_date(tmp_path) -> None:
    provider = RecordingProvider()
    cached = CachedMarketDataProvider(provider, MarketDataCache(tmp_path / "market.sqlite3"))
    cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )

    extended = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 23), AdjustmentMode.QFQ
    )

    assert len(provider.calls) == 2
    assert provider.calls[-1][1:] == (
        date(2026, 1, 16),
        date(2026, 1, 23),
        AdjustmentMode.QFQ,
    )
    assert extended.cache_status == "partial"
    assert extended.cache_hit_bars == 10
    assert extended.cache_added_bars == 5
    assert extended.network_rows == 6
    assert extended.fetch_ranges == ("2026-01-16..2026-01-23",)


def test_cache_prepend_fetches_only_head_with_one_overlap_date(tmp_path) -> None:
    provider = RecordingProvider()
    cached = CachedMarketDataProvider(provider, MarketDataCache(tmp_path / "market.sqlite3"))
    cached.fetch_daily(
        "600519.SH", date(2026, 1, 12), date(2026, 1, 23), AdjustmentMode.QFQ
    )

    extended = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 23), AdjustmentMode.QFQ
    )

    assert len(provider.calls) == 2
    assert provider.calls[-1][1:] == (
        date(2026, 1, 5),
        date(2026, 1, 12),
        AdjustmentMode.QFQ,
    )
    assert extended.cache_status == "partial"
    assert extended.cache_hit_bars == 10
    assert extended.cache_added_bars == 5
    assert extended.network_rows == 6


def test_cache_records_empty_weekend_tail_and_does_not_request_it_again(tmp_path) -> None:
    provider = RecordingProvider("baostock")
    cached = CachedMarketDataProvider(provider, MarketDataCache(tmp_path / "market.sqlite3"))
    cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 9), AdjustmentMode.QFQ
    )

    weekend = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 11), AdjustmentMode.QFQ
    )
    repeated = cached.fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 11), AdjustmentMode.QFQ
    )

    assert len(provider.calls) == 2
    assert weekend.cache_added_bars == 0
    assert weekend.network_rows == 1  # one overlap Friday, no weekend bars
    assert repeated.cache_status == "hit"
    assert repeated.network_rows == 0


def test_cache_never_mixes_source_or_adjustment(tmp_path) -> None:
    cache = MarketDataCache(tmp_path / "market.sqlite3")
    baostock = RecordingProvider("baostock")
    tushare = RecordingProvider("tushare")

    CachedMarketDataProvider(baostock, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )
    tushare_data = CachedMarketDataProvider(tushare, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )
    hfq_data = CachedMarketDataProvider(baostock, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.HFQ
    )

    assert len(baostock.calls) == 2
    assert len(tushare.calls) == 1
    assert tushare_data.source == "tushare"
    assert tushare_data.adjustment is AdjustmentMode.QFQ
    assert hfq_data.source == "baostock"
    assert hfq_data.adjustment is AdjustmentMode.HFQ
    assert cache.count_bars("baostock", "600519.SH", AdjustmentMode.QFQ) == 10
    assert cache.count_bars("baostock", "600519.SH", AdjustmentMode.HFQ) == 10
    assert cache.count_bars("tushare", "600519.SH", AdjustmentMode.QFQ) == 10


def test_adjusted_overlap_change_rebuilds_only_matching_series(tmp_path) -> None:
    cache = MarketDataCache(tmp_path / "market.sqlite3")
    original = RecordingProvider("baostock", scale=1.0)
    CachedMarketDataProvider(original, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )
    other = RecordingProvider("tushare", scale=1.0)
    CachedMarketDataProvider(other, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
    )

    rebased = RecordingProvider("baostock", scale=0.5)
    result = CachedMarketDataProvider(rebased, cache).fetch_daily(
        "600519.SH", date(2026, 1, 5), date(2026, 1, 23), AdjustmentMode.QFQ
    )

    assert result.cache_status == "rebuilt"
    assert result.cache_rebuilt is True
    assert len(rebased.calls) == 2
    assert rebased.calls[0][1:3] == (date(2026, 1, 16), date(2026, 1, 23))
    assert rebased.calls[1][1:3] == (date(2026, 1, 5), date(2026, 1, 23))
    assert result.bars[0].close < 60
    assert cache.count_bars("tushare", "600519.SH", AdjustmentMode.QFQ) == 10


def test_cached_provider_rejects_wrong_upstream_lineage(tmp_path) -> None:
    class WrongSourceProvider(RecordingProvider):
        def fetch_daily(
            self,
            symbol: str,
            start_date: date,
            end_date: date,
            adjustment: AdjustmentMode,
        ) -> MarketDataSet:
            result = super().fetch_daily(symbol, start_date, end_date, adjustment)
            return MarketDataSet(
                result.symbol,
                "unexpected-source",
                result.adjustment,
                result.bars,
                result.fetched_at,
            )

    cached = CachedMarketDataProvider(
        WrongSourceProvider("expected-source"), MarketDataCache(tmp_path / "market.sqlite3")
    )

    try:
        cached.fetch_daily(
            "600519.SH", date(2026, 1, 5), date(2026, 1, 16), AdjustmentMode.QFQ
        )
    except ValueError as error:
        assert "数据源不一致" in str(error)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("wrong-source data must not enter the cache")
