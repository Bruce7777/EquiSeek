from __future__ import annotations

import json
import os
from pathlib import Path

from aegisrun.portfolio.models import PortfolioBook, Position, WatchItem, canonical_symbol
from aegisrun.user_data import named_data_file


def default_portfolio_path() -> Path:
    configured = os.getenv("EQUISEEK_PORTFOLIO_PATH") or os.getenv("AEGISRUN_PORTFOLIO_PATH")
    if configured:
        return Path(configured).expanduser()
    return named_data_file("portfolio.json")


class PortfolioRepository:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_portfolio_path()

    def load(self) -> PortfolioBook:
        if not self.path.exists():
            return PortfolioBook()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"无法读取本地持仓文件：{type(error).__name__}") from error
        if not isinstance(payload, dict):
            raise ValueError("本地持仓文件必须是 JSON 对象")
        return PortfolioBook.from_dict(payload)

    def save(self, book: PortfolioBook) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(book.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def upsert_position(self, position: Position) -> PortfolioBook:
        current = self.load()
        positions = {item.symbol: item for item in current.positions}
        positions[position.symbol] = position
        updated = PortfolioBook(
            positions=tuple(sorted(positions.values(), key=lambda item: item.symbol)),
            watchlist=current.watchlist,
        )
        self.save(updated)
        return updated

    def remove_position(self, symbol: str) -> PortfolioBook:
        current = self.load()
        canonical = canonical_symbol(symbol)
        updated = PortfolioBook(
            positions=tuple(item for item in current.positions if item.symbol != canonical),
            watchlist=current.watchlist,
        )
        self.save(updated)
        return updated

    def upsert_watch(self, item: WatchItem) -> PortfolioBook:
        current = self.load()
        watchlist = {existing.symbol: existing for existing in current.watchlist}
        watchlist[item.symbol] = item
        updated = PortfolioBook(
            positions=current.positions,
            watchlist=tuple(sorted(watchlist.values(), key=lambda value: value.symbol)),
        )
        self.save(updated)
        return updated

    def remove_watch(self, symbol: str) -> PortfolioBook:
        current = self.load()
        canonical = canonical_symbol(symbol)
        updated = PortfolioBook(
            positions=current.positions,
            watchlist=tuple(item for item in current.watchlist if item.symbol != canonical),
        )
        self.save(updated)
        return updated
