from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class AdjustmentMode(StrEnum):
    BFQ = "bfq"
    QFQ = "qfq"
    HFQ = "hfq"

    @property
    def label(self) -> str:
        return {
            self.BFQ: "不复权",
            self.QFQ: "前复权",
            self.HFQ: "后复权",
        }[self]


@dataclass(frozen=True, slots=True)
class PriceBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    pre_close: float | None = None

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC price relationship is invalid")
        if self.volume < 0 or self.amount < 0:
            raise ValueError("volume and amount cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketDataSet:
    symbol: str
    source: str
    adjustment: AdjustmentMode
    bars: tuple[PriceBar, ...]
    fetched_at: str
    is_synthetic: bool = False
    warnings: tuple[str, ...] = ()
    cache_status: str = "disabled"
    cache_hit_bars: int = 0
    cache_added_bars: int = 0
    network_rows: int = 0
    fetch_ranges: tuple[str, ...] = ()
    cache_path: str | None = None
    cache_rebuilt: bool = False

    @property
    def as_of(self) -> date:
        if not self.bars:
            raise ValueError("market data is empty")
        return self.bars[-1].trade_date
