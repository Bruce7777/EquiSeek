from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from aegisrun.marketdata.baostock_provider import display_stock_code, normalize_stock_code
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar


def _tushare_code(symbol: str) -> str:
    return display_stock_code(normalize_stock_code(symbol, allow_beijing=True))


def _market_api(code: str) -> str:
    digits, exchange = code.split(".", 1)
    if (exchange == "SH" and digits.startswith("000")) or (
        exchange == "SZ" and digits.startswith("399")
    ):
        return "index_daily"
    if (exchange == "SH" and digits.startswith("5")) or (
        exchange == "SZ" and digits.startswith(("15", "16"))
    ):
        return "fund_daily"
    return "daily"


class TushareProvider:
    source_name = "tushare"

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.tushare.pro",
    ) -> None:
        if not token.strip():
            raise ValueError("Tushare Token 不能为空")
        self._token = token.strip()
        self._client = httpx.Client(base_url=base_url, timeout=30.0, transport=transport)

    def _query(self, api_name: str, params: dict[str, str], fields: str) -> list[dict[str, Any]]:
        response = self._client.post(
            "",
            json={"api_name": api_name, "token": self._token, "params": params, "fields": fields},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Tushare 查询失败：{payload.get('msg') or payload.get('code')}")
        data = payload.get("data") or {}
        names = data.get("fields") or []
        return [dict(zip(names, row, strict=True)) for row in data.get("items") or []]

    def fetch_daily(
        self, symbol: str, start_date: date, end_date: date, adjustment: AdjustmentMode
    ) -> MarketDataSet:
        code = _tushare_code(symbol)
        params = {
            "ts_code": code,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        }
        api_name = _market_api(code)
        if api_name != "daily" and adjustment is not AdjustmentMode.BFQ:
            raise ValueError("指数和基金趋势代理固定使用不复权口径")
        daily = self._query(
            api_name,
            params,
            "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
        )
        if not daily:
            raise ValueError("Tushare 未返回行情，请检查代码、日期、Token 权限或调用额度")
        factors: dict[str, float] = {}
        if api_name == "daily" and adjustment is not AdjustmentMode.BFQ:
            factors = {
                str(row["trade_date"]): float(row["adj_factor"])
                for row in self._query("adj_factor", params, "ts_code,trade_date,adj_factor")
            }
            if not factors:
                raise ValueError("Tushare 未返回复权因子，无法完成复权计算")
        latest_factor = factors.get(max(factors)) if factors else 1.0
        assert latest_factor is not None

        rows: list[PriceBar] = []
        for raw in daily:
            factor = factors.get(str(raw["trade_date"]), 1.0)
            multiplier = {
                AdjustmentMode.BFQ: 1.0,
                AdjustmentMode.QFQ: factor / latest_factor,
                AdjustmentMode.HFQ: factor,
            }[adjustment]
            rows.append(
                PriceBar(
                    trade_date=datetime.strptime(str(raw["trade_date"]), "%Y%m%d").date(),
                    open=float(raw["open"]) * multiplier,
                    high=float(raw["high"]) * multiplier,
                    low=float(raw["low"]) * multiplier,
                    close=float(raw["close"]) * multiplier,
                    pre_close=float(raw["pre_close"]) * multiplier,
                    volume=float(raw.get("vol") or 0),
                    amount=float(raw.get("amount") or 0),
                )
            )
        rows.sort(key=lambda item: item.trade_date)
        return MarketDataSet(
            symbol=code,
            source="tushare",
            adjustment=adjustment,
            bars=tuple(rows),
            fetched_at=datetime.now(UTC).isoformat(),
            warnings=(
                "Tushare 数据由当前设备上的用户 Token 获取，使用受用户账户与服务协议约束。",
                *(("指数/板块代理采用不复权行情。",) if api_name != "daily" else ()),
            ),
        )

    def close(self) -> None:
        self._client.close()
