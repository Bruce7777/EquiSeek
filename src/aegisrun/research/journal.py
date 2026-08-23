from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

LONG_ACTIONS = frozenset({"buy", "add", "hold"})
DEFENSIVE_ACTIONS = frozenset({"sell", "reduce", "avoid"})
NEUTRAL_ACTIONS = frozenset({"wait"})


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _status(value: float) -> tuple[str, str]:
    if value > 0.005:
        return "profit", "盈利"
    if value < -0.005:
        return "loss", "亏损"
    return "flat", "持平"


def initial_research_outcome(result: dict[str, Any]) -> dict[str, Any]:
    raw_advice = result.get("advice")
    advice: dict[str, Any] = raw_advice if isinstance(raw_advice, dict) else {}
    action = str(advice.get("action", "wait"))
    baseline_price = _number(advice.get("current_price"))
    as_of = str(result.get("asOf") or result.get("as_of") or advice.get("as_of") or "")
    synthetic = str(result.get("sourceKind", "")) == "synthetic" or str(
        result.get("source", "")
    ).startswith("synthetic")
    return {
        "schema_version": 1,
        "status": "demo" if synthetic else "pending",
        "status_label": "演示记录" if synthetic else "待观察",
        "action": action,
        "action_label": str(advice.get("action_label", action or "等待")),
        "baseline_price": baseline_price,
        "baseline_as_of": as_of,
        "latest_price": baseline_price,
        "latest_as_of": as_of,
        "price_change_pct": 0.0 if baseline_price is not None else None,
        "decision_return_pct": None,
        "trading_days": 0,
        "updated_at": datetime.now(UTC).isoformat(),
        "is_real_market_data": not synthetic,
        "methodology": _methodology(action, synthetic=synthetic),
    }


def evaluate_research_outcome(
    result: dict[str, Any],
    *,
    latest_price: float,
    latest_as_of: date | str,
    trading_days: int,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    outcome = initial_research_outcome(result)
    baseline = _number(outcome.get("baseline_price"))
    latest = _number(latest_price)
    if baseline is None or latest is None:
        return unavailable_research_outcome(result, "研究参考价或最新收盘价无效")

    action = str(outcome["action"])
    price_change = (latest / baseline - 1.0) * 100.0
    decision_return: float | None
    if action in LONG_ACTIONS:
        decision_return = price_change
    elif action in DEFENSIVE_ACTIONS:
        decision_return = -price_change
    else:
        decision_return = None

    if max(0, trading_days) == 0:
        status, status_label = "pending", "待观察"
    elif decision_return is None:
        status, status_label = "observing", "未执行"
    else:
        status, status_label = _status(decision_return)

    return {
        **outcome,
        "status": status,
        "status_label": status_label,
        "latest_price": round(latest, 4),
        "latest_as_of": latest_as_of.isoformat()
        if isinstance(latest_as_of, date)
        else str(latest_as_of),
        "price_change_pct": round(price_change, 2),
        "decision_return_pct": round(decision_return, 2)
        if decision_return is not None
        else None,
        "trading_days": max(0, int(trading_days)),
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
        "methodology": _methodology(action, synthetic=False),
    }


def unavailable_research_outcome(result: dict[str, Any], reason: str) -> dict[str, Any]:
    current = result.get("outcome")
    outcome = dict(current) if isinstance(current, dict) else initial_research_outcome(result)
    if outcome.get("status") in {"profit", "loss", "flat", "observing"}:
        return {
            **outcome,
            "refresh_warning": reason[:240],
            "updated_at": datetime.now(UTC).isoformat(),
        }
    return {
        **outcome,
        "status": "unavailable",
        "status_label": "暂不可用",
        "refresh_warning": reason[:240],
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _methodology(action: str, *, synthetic: bool) -> str:
    if synthetic:
        return "离线合成数据仅用于界面演示，不计算真实市场表现。"
    if action in LONG_ACTIONS:
        return "按研究参考收盘价到最新同复权收盘价的涨跌计算假设收益；不代表真实成交。"
    if action in DEFENSIVE_ACTIONS:
        return "按研究后价格变化的反向值衡量防守效果；正值表示规避下跌，不代表真实成交。"
    return "等待决策未假设成交，仅跟踪研究后价格变化。"
