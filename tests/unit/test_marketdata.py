from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from aegisrun.marketdata.baostock_provider import BaoStockProvider, normalize_stock_code
from aegisrun.marketdata.indicators import calculate_indicators
from aegisrun.marketdata.models import AdjustmentMode, PriceBar
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.marketdata.tushare_provider import TushareProvider, _tushare_code


def make_bars(count: int = 80, *, flat: bool = False) -> list[PriceBar]:
    start = date(2026, 1, 1)
    bars: list[PriceBar] = []
    for index in range(count):
        close = 10.0 if flat else 10.0 + index
        bars.append(
            PriceBar(
                trade_date=start + timedelta(days=index),
                open=close - 0.3,
                high=close + 0.7,
                low=close - 0.8,
                close=close,
                volume=1000.0 + index,
                amount=(1000.0 + index) * close,
            )
        )
    return bars


def test_indicator_engine_calculates_expected_latest_values() -> None:
    result = calculate_indicators(make_bars())

    assert result.ma[5][-1] == pytest.approx(sum(range(85, 90)) / 5)
    assert result.ma[20][-1] == pytest.approx(sum(range(70, 90)) / 20)
    assert result.rsi[6][-1] == pytest.approx(100.0)
    assert result.boll_mid[-1] == pytest.approx(result.ma[20][-1])
    assert result.boll_upper[-1] > result.boll_mid[-1] > result.boll_lower[-1]
    assert result.atr[20][-1] is not None
    assert 0 <= (result.wr[10][-1] or 0) <= 100
    assert len(result.macd) == len(result.dif) == len(result.dea) == 80


def test_flat_series_has_neutral_rsi_and_zero_macd() -> None:
    result = calculate_indicators(make_bars(flat=True))

    assert result.rsi[6][-1] == pytest.approx(50.0)
    assert result.macd[-1] == pytest.approx(0.0)
    assert result.dif[-1] == pytest.approx(0.0)


def test_demo_provider_is_deterministic_and_explicitly_synthetic() -> None:
    provider = DemoMarketDataProvider()
    first = provider.fetch_daily(
        "600519.SH", date(2025, 1, 1), date(2026, 1, 1), AdjustmentMode.QFQ
    )
    second = provider.fetch_daily(
        "600519.SH", date(2025, 1, 1), date(2026, 1, 1), AdjustmentMode.QFQ
    )

    assert first.source == "synthetic-demo"
    assert first.bars == second.bars
    assert len(first.bars) >= 120
    assert any("合成" in warning for warning in first.warnings)


@pytest.mark.parametrize(
    ("user_code", "expected"),
    [
        ("600519", "sh.600519"),
        ("600519.SH", "sh.600519"),
        ("000001.SZ", "sz.000001"),
        ("sh.600000", "sh.600000"),
    ],
)
def test_normalize_stock_code(user_code: str, expected: str) -> None:
    assert normalize_stock_code(user_code) == expected


def test_beijing_exchange_is_supported_by_tushare_and_explicitly_rejected_by_baostock() -> None:
    assert _tushare_code("920001.BJ") == "920001.BJ"
    assert _tushare_code("832000") == "832000.BJ"
    with pytest.raises(ValueError, match="切换 Tushare"):
        normalize_stock_code("920001.BJ")
    with pytest.raises(ValueError, match="切换 Tushare"):
        normalize_stock_code("832000")


class FakeQuery:
    fields = ["date", "code", "open", "high", "low", "close", "preclose", "volume", "amount"]
    error_code = "0"
    error_msg = "success"

    def __init__(self) -> None:
        self._rows = [
            ["2026-01-05", "sh.600519", "10", "11", "9", "10.5", "9.8", "100", "1050"],
            ["2026-01-02", "sh.600519", "9", "10", "8", "9.8", "9.2", "90", "882"],
        ]

    def next(self) -> bool:
        return bool(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows.pop(0)


class FakeBaoStock:
    def __init__(self) -> None:
        self.last_args: dict[str, Any] = {}

    def login(self) -> Any:
        return type("Login", (), {"error_code": "0", "error_msg": "success"})()

    def logout(self) -> None:
        return None

    def query_history_k_data_plus(self, *args: Any, **kwargs: Any) -> FakeQuery:
        self.last_args = {"args": args, "kwargs": kwargs}
        return FakeQuery()


def test_baostock_provider_maps_and_sorts_rows() -> None:
    api = FakeBaoStock()
    result = BaoStockProvider(api=api).fetch_daily(
        "600519.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustmentMode.QFQ
    )

    assert [bar.trade_date.isoformat() for bar in result.bars] == ["2026-01-02", "2026-01-05"]
    assert result.bars[-1].close == pytest.approx(10.5)
    assert api.last_args["kwargs"]["adjustflag"] == "2"
    assert result.source == "baostock"


def test_tushare_provider_applies_qfq_without_leaking_token() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: Any) -> Any:
        import json

        import httpx

        payload = json.loads(request.content)
        calls.append(payload)
        if payload["api_name"] == "daily":
            fields = [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
                "amount",
            ]
            items = [
                ["600519.SH", "20260105", 20, 22, 19, 21, 20, 100, 2100],
                ["600519.SH", "20260102", 10, 11, 9, 10, 9.5, 90, 900],
            ]
        else:
            fields = ["ts_code", "trade_date", "adj_factor"]
            items = [["600519.SH", "20260105", 2.0], ["600519.SH", "20260102", 1.0]]
        return httpx.Response(
            200, json={"code": 0, "msg": None, "data": {"fields": fields, "items": items}}
        )

    import httpx

    provider = TushareProvider("token-secret", transport=httpx.MockTransport(handler))
    result = provider.fetch_daily(
        "600519.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustmentMode.QFQ
    )
    provider.close()

    assert result.bars[0].close == pytest.approx(5.0)
    assert result.bars[1].close == pytest.approx(21.0)
    assert calls[0]["token"] == "token-secret"
    assert "token-secret" not in repr(provider)


@pytest.mark.parametrize(
    ("symbol", "expected_api"),
    [("000001.SH", "index_daily"), ("399006.SZ", "index_daily"), ("512480.SH", "fund_daily")],
)
def test_tushare_provider_routes_market_context_to_index_or_fund_daily(
    symbol: str, expected_api: str
) -> None:
    calls: list[str] = []

    def handler(request: Any) -> Any:
        import json

        import httpx

        payload = json.loads(request.content)
        calls.append(payload["api_name"])
        fields = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "vol",
            "amount",
        ]
        items = [[symbol, "20260105", 10, 11, 9, 10.5, 10, 100, 1050]]
        return httpx.Response(
            200, json={"code": 0, "msg": None, "data": {"fields": fields, "items": items}}
        )

    import httpx

    provider = TushareProvider("token-secret", transport=httpx.MockTransport(handler))
    result = provider.fetch_daily(symbol, date(2026, 1, 1), date(2026, 1, 31), AdjustmentMode.BFQ)
    provider.close()

    assert calls == [expected_api]
    assert result.symbol == symbol
