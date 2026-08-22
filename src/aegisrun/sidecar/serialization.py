from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def json_value(value: Any) -> Any:
    """Convert domain values into JSON-safe values without importing a UI framework."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return json_value(to_dict())
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def research_projection(result: Any) -> dict[str, Any]:
    """Bound the renderer payload while keeping Python as the research fact source."""

    bars = result.data.bars[-180:]
    offset = len(result.data.bars) - len(bars)

    def series(values: tuple[float | None, ...]) -> list[float | None]:
        return [round(item, 4) if item is not None else None for item in values[offset:]]

    return {
        "kind": "research",
        "symbol": result.data.symbol,
        "source": result.data.source,
        "sourceKind": "synthetic" if result.data.is_synthetic else "public-history",
        "adjustment": result.data.adjustment.value,
        "asOf": result.data.as_of.isoformat(),
        "fetchedAt": result.data.fetched_at,
        "warnings": list(result.data.warnings),
        "snapshot": json_value(result.snapshot),
        "strategy": json_value(result.strategy),
        "advice": result.investment_advice.to_dict(),
        "marketContext": result.market_context.to_dict(),
        "macroOverlay": json_value(result.macro_overlay),
        "summary": result.model_summary or result.deterministic_summary,
        "answerMode": "deepseek" if result.model_summary else "local",
        "modelWarning": result.model_warning,
        "plan": json_value(result.plan),
        "workspace": result.workspace,
        "chart": {
            "formulaVersion": result.indicators.version,
            "bars": [
                {
                    "date": item.trade_date.isoformat(),
                    "open": item.open,
                    "high": item.high,
                    "low": item.low,
                    "close": item.close,
                    "volume": item.volume,
                }
                for item in bars
            ],
            "ma5": series(result.indicators.ma[5]),
            "ma20": series(result.indicators.ma[20]),
            "macd": series(result.indicators.macd),
            "wr10": series(result.indicators.wr[10]),
        },
    }
