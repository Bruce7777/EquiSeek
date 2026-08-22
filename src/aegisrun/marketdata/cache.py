from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar
from aegisrun.user_data import named_data_file

SCHEMA_VERSION = "1"
LOCAL_ACTOR = "local-desktop"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'local-desktop'
);
CREATE TABLE IF NOT EXISTS market_price_bars (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    pre_close REAL,
    volume REAL NOT NULL,
    amount REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'local-desktop',
    updated_by TEXT NOT NULL DEFAULT 'local-desktop',
    PRIMARY KEY (source, symbol, adjustment, trade_date)
);
CREATE TABLE IF NOT EXISTS market_data_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'local-desktop',
    CHECK (start_date <= end_date)
);
CREATE INDEX IF NOT EXISTS idx_market_coverage_lookup
ON market_data_coverage (source, symbol, adjustment, start_date, end_date);
"""


def default_market_cache_path() -> Path:
    configured = os.getenv("EQUISEEK_MARKET_CACHE_PATH", "").strip() or os.getenv(
        "AEGISRUN_MARKET_CACHE_PATH", ""
    ).strip()
    if configured:
        return Path(configured).expanduser()
    return named_data_file("market-data.sqlite3")


def market_cache_enabled() -> bool:
    value = (
        os.getenv("EQUISEEK_MARKET_CACHE_ENABLED")
        or os.getenv("AEGISRUN_MARKET_CACHE_ENABLED")
        or "true"
    ).strip().lower()
    return value not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("date range start must not be after end")

    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class CacheBounds:
    start: date
    end: date


class MarketDataCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_market_cache_path()).expanduser().absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = OFF")
        except sqlite3.DatabaseError as error:
            raise RuntimeError(f"本地行情缓存无法打开：{self.path}（{error}）") from error
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(SCHEMA_SQL)
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    INSERT INTO market_cache_meta(key, value, updated_at, updated_by)
                    VALUES ('schema_version', ?, ?, ?)
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (SCHEMA_VERSION, now, LOCAL_ACTOR),
                )
                version = connection.execute(
                    "SELECT value FROM market_cache_meta WHERE key='schema_version'"
                ).fetchone()
                if version is None or str(version["value"]) != SCHEMA_VERSION:
                    actual = "missing" if version is None else str(version["value"])
                    raise RuntimeError(
                        f"不支持的本地行情缓存版本：{actual}（当前支持 {SCHEMA_VERSION}；"
                        f"路径 {self.path}）"
                    )
                connection.commit()
            self.path.chmod(0o600)
        except (OSError, sqlite3.DatabaseError) as error:
            raise RuntimeError(f"本地行情缓存初始化失败：{self.path}（{error}）") from error

    @staticmethod
    def _key(adjustment: AdjustmentMode) -> str:
        return adjustment.value

    def count_bars(self, source: str, symbol: str, adjustment: AdjustmentMode) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM market_price_bars
                WHERE source=? AND symbol=? AND adjustment=?
                """,
                (source, symbol, self._key(adjustment)),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def bounds(
        self, source: str, symbol: str, adjustment: AdjustmentMode
    ) -> CacheBounds | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(trade_date) AS start_date, MAX(trade_date) AS end_date
                FROM market_price_bars
                WHERE source=? AND symbol=? AND adjustment=?
                """,
                (source, symbol, self._key(adjustment)),
            ).fetchone()
        if row is None or row["start_date"] is None or row["end_date"] is None:
            return None
        return CacheBounds(
            date.fromisoformat(row["start_date"]),
            date.fromisoformat(row["end_date"]),
        )

    def load_bars(
        self,
        source: str,
        symbol: str,
        adjustment: AdjustmentMode,
        start_date: date,
        end_date: date,
    ) -> tuple[PriceBar, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT trade_date, open, high, low, close, pre_close, volume, amount
                FROM market_price_bars
                WHERE source=? AND symbol=? AND adjustment=?
                  AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (
                    source,
                    symbol,
                    self._key(adjustment),
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            ).fetchall()
        return tuple(
            PriceBar(
                date.fromisoformat(row["trade_date"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
                float(row["amount"]),
                float(row["pre_close"]) if row["pre_close"] is not None else None,
            )
            for row in rows
        )

    def fetched_at(self, source: str, symbol: str, adjustment: AdjustmentMode) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(fetched_at) AS fetched_at
                FROM market_price_bars
                WHERE source=? AND symbol=? AND adjustment=?
                """,
                (source, symbol, self._key(adjustment)),
            ).fetchone()
        return str(row["fetched_at"]) if row is not None and row["fetched_at"] else ""

    def coverage(
        self, source: str, symbol: str, adjustment: AdjustmentMode
    ) -> tuple[DateRange, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT start_date, end_date
                FROM market_data_coverage
                WHERE source=? AND symbol=? AND adjustment=?
                ORDER BY start_date, end_date
                """,
                (source, symbol, self._key(adjustment)),
            ).fetchall()
        return tuple(
            DateRange(date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"]))
            for row in rows
        )

    def missing_ranges(
        self,
        source: str,
        symbol: str,
        adjustment: AdjustmentMode,
        start_date: date,
        end_date: date,
    ) -> tuple[DateRange, ...]:
        cursor = start_date
        gaps: list[DateRange] = []
        for covered in self.coverage(source, symbol, adjustment):
            if covered.end < cursor:
                continue
            if covered.start > end_date:
                break
            if covered.start > cursor:
                gaps.append(DateRange(cursor, min(end_date, covered.start - timedelta(days=1))))
            cursor = max(cursor, covered.end + timedelta(days=1))
            if cursor > end_date:
                break
        if cursor <= end_date:
            gaps.append(DateRange(cursor, end_date))
        return tuple(gaps)

    @staticmethod
    def _merge_ranges(ranges: list[DateRange]) -> tuple[DateRange, ...]:
        if not ranges:
            return ()
        ordered = sorted(ranges, key=lambda item: (item.start, item.end))
        merged: list[DateRange] = [ordered[0]]
        for item in ordered[1:]:
            current = merged[-1]
            if item.start <= current.end + timedelta(days=1):
                merged[-1] = DateRange(current.start, max(current.end, item.end))
            else:
                merged.append(item)
        return tuple(merged)

    def _replace_coverage(
        self,
        connection: sqlite3.Connection,
        source: str,
        symbol: str,
        adjustment: AdjustmentMode,
        added: DateRange,
        fetched_at: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT start_date, end_date FROM market_data_coverage
            WHERE source=? AND symbol=? AND adjustment=?
            """,
            (source, symbol, self._key(adjustment)),
        ).fetchall()
        ranges = [
            DateRange(date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"]))
            for row in rows
        ]
        ranges.append(added)
        merged = self._merge_ranges(ranges)
        connection.execute(
            """
            DELETE FROM market_data_coverage
            WHERE source=? AND symbol=? AND adjustment=?
            """,
            (source, symbol, self._key(adjustment)),
        )
        now = datetime.now(UTC).isoformat()
        connection.executemany(
            """
            INSERT INTO market_data_coverage(
                source, symbol, adjustment, start_date, end_date,
                fetched_at, created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source,
                    symbol,
                    self._key(adjustment),
                    item.start.isoformat(),
                    item.end.isoformat(),
                    fetched_at,
                    now,
                    LOCAL_ACTOR,
                )
                for item in merged
            ],
        )

    def record_coverage(
        self,
        source: str,
        symbol: str,
        adjustment: AdjustmentMode,
        covered: DateRange,
        *,
        fetched_at: str | None = None,
    ) -> None:
        timestamp = fetched_at or datetime.now(UTC).isoformat()
        with self._connect() as connection:
            self._replace_coverage(
                connection, source, symbol, adjustment, covered, timestamp
            )
            connection.commit()

    def store_dataset(
        self, dataset: MarketDataSet, covered: DateRange
    ) -> None:
        if dataset.is_synthetic:
            raise ValueError("离线模拟数据不得写入真实行情缓存")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_price_bars(
                    source, symbol, adjustment, trade_date,
                    open, high, low, close, pre_close, volume, amount,
                    fetched_at, created_at, updated_at, created_by, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, symbol, adjustment, trade_date) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    pre_close=excluded.pre_close,
                    volume=excluded.volume,
                    amount=excluded.amount,
                    fetched_at=excluded.fetched_at,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                [
                    (
                        dataset.source,
                        dataset.symbol,
                        dataset.adjustment.value,
                        bar.trade_date.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.pre_close,
                        bar.volume,
                        bar.amount,
                        dataset.fetched_at,
                        now,
                        now,
                        LOCAL_ACTOR,
                        LOCAL_ACTOR,
                    )
                    for bar in dataset.bars
                ],
            )
            self._replace_coverage(
                connection,
                dataset.source,
                dataset.symbol,
                dataset.adjustment,
                covered,
                dataset.fetched_at,
            )
            connection.commit()

    def clear_series(self, source: str, symbol: str, adjustment: AdjustmentMode) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM market_price_bars
                WHERE source=? AND symbol=? AND adjustment=?
                """,
                (source, symbol, self._key(adjustment)),
            )
            connection.execute(
                """
                DELETE FROM market_data_coverage
                WHERE source=? AND symbol=? AND adjustment=?
                """,
                (source, symbol, self._key(adjustment)),
            )
            connection.commit()
