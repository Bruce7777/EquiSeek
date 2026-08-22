from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar


class MarketDataProvider(Protocol):
    source_name: str

    def fetch_daily(
        self, symbol: str, start_date: date, end_date: date, adjustment: AdjustmentMode
    ) -> MarketDataSet: ...


class DemoMarketDataProvider:
    """Deterministic offline data for UI evaluation; never presented as real market data."""

    source_name = "synthetic-demo"

    def fetch_daily(
        self, symbol: str, start_date: date, end_date: date, adjustment: AdjustmentMode
    ) -> MarketDataSet:
        if start_date >= end_date:
            raise ValueError("start_date must be earlier than end_date")
        canonical = symbol.strip().upper()
        seed = int(hashlib.sha256(canonical.encode()).hexdigest()[:12], 16)
        randomizer = random.Random(seed)  # noqa: S311 - reproducible synthetic fixtures only
        cursor = start_date
        close = 25.0 + seed % 7000 / 100.0
        bars: list[PriceBar] = []
        index = 0
        while cursor <= end_date:
            if cursor.weekday() < 5:
                drift = math.sin(index / 13.0) * 0.004 + 0.0003
                shock = randomizer.uniform(-0.018, 0.018)
                previous = close
                close = max(1.0, close * (1.0 + drift + shock))
                opening = previous * (1.0 + randomizer.uniform(-0.006, 0.006))
                high = max(opening, close) * (1.0 + randomizer.uniform(0.002, 0.012))
                low = min(opening, close) * (1.0 - randomizer.uniform(0.002, 0.012))
                volume = 1_000_000.0 * (0.6 + randomizer.random())
                bars.append(
                    PriceBar(
                        trade_date=cursor,
                        open=opening,
                        high=high,
                        low=low,
                        close=close,
                        pre_close=previous,
                        volume=volume,
                        amount=volume * (opening + close) / 2.0,
                    )
                )
                index += 1
            cursor += timedelta(days=1)
        return MarketDataSet(
            symbol=canonical,
            source="synthetic-demo",
            adjustment=adjustment,
            bars=tuple(bars),
            fetched_at=datetime.now(UTC).isoformat(),
            is_synthetic=True,
            warnings=("当前为离线合成演示数据，不是真实证券行情，不得用于投资判断。",),
        )
