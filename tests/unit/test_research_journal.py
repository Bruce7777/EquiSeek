from __future__ import annotations

from datetime import date

import pytest

from aegisrun.research.journal import (
    evaluate_research_outcome,
    initial_research_outcome,
    unavailable_research_outcome,
)


def result(action: str, *, source_kind: str = "public-history") -> dict[str, object]:
    return {
        "kind": "research",
        "symbol": "600050.SH",
        "source": "baostock",
        "sourceKind": source_kind,
        "asOf": "2026-08-20",
        "advice": {
            "action": action,
            "action_label": action,
            "current_price": 10.0,
            "as_of": "2026-08-20",
        },
    }


@pytest.mark.parametrize("action", ["buy", "add", "hold"])
def test_long_decision_uses_forward_price_return(action: str) -> None:
    outcome = evaluate_research_outcome(
        result(action), latest_price=11.0, latest_as_of=date(2026, 8, 25), trading_days=3
    )

    assert outcome["status"] == "profit"
    assert outcome["price_change_pct"] == 10.0
    assert outcome["decision_return_pct"] == 10.0
    assert "不代表真实成交" in outcome["methodology"]


@pytest.mark.parametrize("action", ["sell", "reduce", "avoid"])
def test_defensive_decision_scores_avoided_decline_without_claiming_trade(action: str) -> None:
    outcome = evaluate_research_outcome(
        result(action), latest_price=9.0, latest_as_of="2026-08-25", trading_days=3
    )

    assert outcome["status"] == "profit"
    assert outcome["price_change_pct"] == -10.0
    assert outcome["decision_return_pct"] == 10.0
    assert "防守效果" in outcome["methodology"]


def test_wait_decision_tracks_price_but_does_not_invent_profit_or_loss() -> None:
    outcome = evaluate_research_outcome(
        result("wait"), latest_price=11.0, latest_as_of="2026-08-25", trading_days=3
    )

    assert outcome["status"] == "observing"
    assert outcome["status_label"] == "未执行"
    assert outcome["price_change_pct"] == 10.0
    assert outcome["decision_return_pct"] is None


def test_synthetic_and_refresh_failures_remain_explicit() -> None:
    demo = initial_research_outcome(result("buy", source_kind="synthetic"))
    unavailable = unavailable_research_outcome(result("buy"), "network unavailable")

    assert demo["status"] == "demo"
    assert demo["is_real_market_data"] is False
    assert unavailable["status"] == "unavailable"
    assert unavailable["refresh_warning"] == "network unavailable"


def test_non_finite_market_price_is_rejected() -> None:
    outcome = evaluate_research_outcome(
        result("buy"), latest_price=float("inf"), latest_as_of="2026-08-25", trading_days=3
    )

    assert outcome["status"] == "unavailable"
