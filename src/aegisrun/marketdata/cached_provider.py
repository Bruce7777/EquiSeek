from __future__ import annotations

import math
from datetime import date

from aegisrun.marketdata.baostock_provider import display_stock_code, normalize_stock_code
from aegisrun.marketdata.cache import CacheBounds, DateRange, MarketDataCache
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar
from aegisrun.marketdata.providers import MarketDataProvider


def _canonical_symbol(symbol: str) -> str:
    return display_stock_code(normalize_stock_code(symbol, allow_beijing=True))


def _same_bar(left: PriceBar, right: PriceBar) -> bool:
    if left.trade_date != right.trade_date:
        return False
    values = (
        (left.open, right.open),
        (left.high, right.high),
        (left.low, right.low),
        (left.close, right.close),
        (left.volume, right.volume),
        (left.amount, right.amount),
    )
    return all(math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8) for a, b in values)


class CachedMarketDataProvider:
    def __init__(self, provider: MarketDataProvider, cache: MarketDataCache) -> None:
        self.provider = provider
        self.cache = cache
        self.source_name = provider.source_name

    def _validate(
        self,
        dataset: MarketDataSet,
        symbol: str,
        adjustment: AdjustmentMode,
    ) -> None:
        if dataset.source != self.source_name:
            raise ValueError(
                f"上游返回的数据源不一致：期望 {self.source_name}，实际 {dataset.source}"
            )
        if dataset.symbol != symbol:
            raise ValueError(f"上游返回的股票不一致：期望 {symbol}，实际 {dataset.symbol}")
        if dataset.adjustment is not adjustment:
            raise ValueError(
                f"上游返回的复权口径不一致：期望 {adjustment.value}，"
                f"实际 {dataset.adjustment.value}"
            )
        if dataset.is_synthetic:
            raise ValueError("真实行情缓存拒绝写入模拟数据")

    def _result(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: AdjustmentMode,
        *,
        initial_dates: set[date],
        network_rows: int,
        fetch_ranges: list[str],
        warnings: list[str],
        rebuilt: bool,
    ) -> MarketDataSet:
        bars = self.cache.load_bars(
            self.source_name, symbol, adjustment, start_date, end_date
        )
        if not bars:
            raise ValueError("本地缓存和上游均未返回可用行情")
        returned_dates = {bar.trade_date for bar in bars}
        cache_hits = 0 if rebuilt else len(returned_dates & initial_dates)
        added = len(returned_dates - initial_dates) if not rebuilt else len(returned_dates)
        status = (
            "rebuilt"
            if rebuilt
            else "hit"
            if network_rows == 0
            else "miss"
            if not initial_dates
            else "partial"
        )
        lineage_warning = (
            f"本地行情缓存严格按来源={self.source_name}、股票={symbol}、"
            f"复权={adjustment.value}隔离；命中 {cache_hits} 根，新增 {added} 根。"
        )
        if rebuilt:
            lineage_warning += " 检测到同源复权历史变化，已重建该单一序列。"
        merged_warnings = tuple(dict.fromkeys((*warnings, lineage_warning)))
        return MarketDataSet(
            symbol=symbol,
            source=self.source_name,
            adjustment=adjustment,
            bars=bars,
            fetched_at=self.cache.fetched_at(self.source_name, symbol, adjustment),
            warnings=merged_warnings,
            cache_status=status,
            cache_hit_bars=cache_hits,
            cache_added_bars=added,
            network_rows=network_rows,
            fetch_ranges=tuple(fetch_ranges),
            cache_path=str(self.cache.path),
            cache_rebuilt=rebuilt,
        )

    def fetch_daily(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: AdjustmentMode,
    ) -> MarketDataSet:
        if start_date >= end_date:
            raise ValueError("开始日期必须早于结束日期")
        canonical = _canonical_symbol(symbol)
        initial = self.cache.load_bars(
            self.source_name, canonical, adjustment, start_date, end_date
        )
        initial_dates = {bar.trade_date for bar in initial}
        gaps = self.cache.missing_ranges(
            self.source_name, canonical, adjustment, start_date, end_date
        )
        if not gaps:
            return self._result(
                canonical,
                start_date,
                end_date,
                adjustment,
                initial_dates=initial_dates,
                network_rows=0,
                fetch_ranges=[],
                warnings=[],
                rebuilt=False,
            )

        bounds = self.cache.bounds(self.source_name, canonical, adjustment)
        network_rows = 0
        fetch_ranges: list[str] = []
        warnings: list[str] = []
        empty_ranges: list[DateRange] = []
        errors: list[Exception] = []
        rebuilt = False

        for gap in gaps:
            fetch_start = gap.start
            fetch_end = gap.end
            if bounds is not None and gap.start > bounds.end:
                fetch_start = bounds.end
            elif bounds is not None and gap.end < bounds.start:
                fetch_end = bounds.start
            request_range = DateRange(fetch_start, fetch_end)
            fetch_ranges.append(request_range.label())
            try:
                dataset = self.provider.fetch_daily(
                    canonical, fetch_start, fetch_end, adjustment
                )
            except ValueError as error:
                errors.append(error)
                empty_ranges.append(gap)
                continue
            self._validate(dataset, canonical, adjustment)
            network_rows += len(dataset.bars)
            warnings.extend(dataset.warnings)

            overlap_date = None
            if bounds is not None and fetch_start == bounds.end:
                overlap_date = bounds.end
            elif bounds is not None and fetch_end == bounds.start:
                overlap_date = bounds.start
            if overlap_date is not None:
                cached_overlap = self.cache.load_bars(
                    self.source_name, canonical, adjustment, overlap_date, overlap_date
                )
                upstream_overlap = next(
                    (bar for bar in dataset.bars if bar.trade_date == overlap_date), None
                )
                if cached_overlap and upstream_overlap is not None and not _same_bar(
                    cached_overlap[0], upstream_overlap
                ):
                    assert bounds is not None
                    full_bounds = CacheBounds(
                        min(bounds.start, start_date), max(bounds.end, end_date)
                    )
                    full_range = DateRange(full_bounds.start, full_bounds.end)
                    fetch_ranges.append(full_range.label())
                    replacement = self.provider.fetch_daily(
                        canonical, full_bounds.start, full_bounds.end, adjustment
                    )
                    self._validate(replacement, canonical, adjustment)
                    network_rows += len(replacement.bars)
                    warnings.extend(replacement.warnings)
                    self.cache.clear_series(self.source_name, canonical, adjustment)
                    self.cache.store_dataset(replacement, full_range)
                    rebuilt = True
                    break
            self.cache.store_dataset(dataset, request_range)

        available = self.cache.load_bars(
            self.source_name, canonical, adjustment, start_date, end_date
        )
        if not available and errors:
            raise errors[0]
        if available:
            for empty in empty_ranges:
                self.cache.record_coverage(
                    self.source_name, canonical, adjustment, empty
                )
        return self._result(
            canonical,
            start_date,
            end_date,
            adjustment,
            initial_dates=initial_dates,
            network_rows=network_rows,
            fetch_ranges=fetch_ranges,
            warnings=warnings,
            rebuilt=rebuilt,
        )

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()
