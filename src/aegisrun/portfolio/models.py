from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from aegisrun.marketdata.baostock_provider import display_stock_code, normalize_stock_code


def canonical_symbol(value: str) -> str:
    return display_stock_code(normalize_stock_code(value, allow_beijing=True))


def _clean_text(value: str, *, limit: int) -> str:
    return " ".join(value.strip().split())[:limit]


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: float
    cost_price: float
    name: str = ""
    opened_on: date | None = None
    notes: str = ""
    industry: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "name", _clean_text(self.name, limit=40))
        object.__setattr__(self, "notes", _clean_text(self.notes, limit=500))
        object.__setattr__(self, "industry", _clean_text(self.industry, limit=80))
        if self.quantity <= 0:
            raise ValueError("持仓数量必须大于 0")
        if self.cost_price <= 0:
            raise ValueError("持仓成本必须大于 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "quantity": self.quantity,
            "cost_price": self.cost_price,
            "opened_on": self.opened_on.isoformat() if self.opened_on else None,
            "notes": self.notes,
            "industry": self.industry,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Position:
        opened = payload.get("opened_on")
        return cls(
            symbol=str(payload["symbol"]),
            name=str(payload.get("name", "")),
            quantity=float(payload["quantity"]),
            cost_price=float(payload["cost_price"]),
            opened_on=date.fromisoformat(str(opened)) if opened else None,
            notes=str(payload.get("notes", "")),
            industry=str(payload.get("industry", "")),
        )


@dataclass(frozen=True, slots=True)
class WatchItem:
    symbol: str
    name: str = ""
    notes: str = ""
    industry: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "name", _clean_text(self.name, limit=40))
        object.__setattr__(self, "notes", _clean_text(self.notes, limit=500))
        object.__setattr__(self, "industry", _clean_text(self.industry, limit=80))

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "notes": self.notes,
            "industry": self.industry,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WatchItem:
        return cls(
            symbol=str(payload["symbol"]),
            name=str(payload.get("name", "")),
            notes=str(payload.get("notes", "")),
            industry=str(payload.get("industry", "")),
        )


@dataclass(frozen=True, slots=True)
class PortfolioBook:
    schema_version: int = 1
    positions: tuple[Position, ...] = field(default_factory=tuple)
    watchlist: tuple[WatchItem, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"不支持的持仓数据版本：{self.schema_version}")
        position_symbols = [item.symbol for item in self.positions]
        watch_symbols = [item.symbol for item in self.watchlist]
        if len(position_symbols) != len(set(position_symbols)):
            raise ValueError("同一证券不能存在多条持仓记录")
        if len(watch_symbols) != len(set(watch_symbols)):
            raise ValueError("同一证券不能重复加入自选")

    def position(self, symbol: str) -> Position | None:
        canonical = canonical_symbol(symbol)
        return next((item for item in self.positions if item.symbol == canonical), None)

    def symbols(self) -> tuple[str, ...]:
        symbols = [
            *(item.symbol for item in self.positions),
            *(item.symbol for item in self.watchlist),
        ]
        return tuple(dict.fromkeys(symbols))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "positions": [item.to_dict() for item in self.positions],
            "watchlist": [item.to_dict() for item in self.watchlist],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PortfolioBook:
        positions = payload.get("positions", [])
        watchlist = payload.get("watchlist", [])
        if not isinstance(positions, list) or not isinstance(watchlist, list):
            raise ValueError("持仓文件结构无效")
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            positions=tuple(Position.from_dict(item) for item in positions),
            watchlist=tuple(WatchItem.from_dict(item) for item in watchlist),
        )
