from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet, PriceBar


def normalize_stock_code(value: str, *, allow_beijing: bool = False) -> str:
    normalized = value.strip().lower()
    if normalized.startswith(("sh.", "sz.")) and len(normalized) == 9:
        return normalized
    if normalized.startswith("bj.") and len(normalized) == 9:
        if allow_beijing:
            return normalized
        raise ValueError("BaoStock 暂不支持北交所代码，请切换 Tushare 数据源")
    upper = normalized.upper()
    if upper.endswith(".SH"):
        return f"sh.{upper[:6]}"
    if upper.endswith(".SZ"):
        return f"sz.{upper[:6]}"
    if upper.endswith(".BJ"):
        if allow_beijing:
            return f"bj.{upper[:6]}"
        raise ValueError("BaoStock 暂不支持北交所代码，请切换 Tushare 数据源")
    digits = "".join(character for character in normalized if character.isdigit())
    if len(digits) != 6:
        raise ValueError("股票代码格式应为 600519、600519.SH 或 000001.SZ")
    if digits.startswith(("4", "8", "9")):
        if not allow_beijing:
            raise ValueError("BaoStock 暂不支持北交所代码，请切换 Tushare 数据源")
        exchange = "bj"
    else:
        exchange = "sh" if digits.startswith(("5", "6", "9")) else "sz"
    return f"{exchange}.{digits}"


def display_stock_code(value: str) -> str:
    exchange, digits = value.split(".", 1)
    return f"{digits}.{exchange.upper()}"


class BaoStockProvider:
    source_name = "baostock"

    def __init__(self, api: Any | None = None) -> None:
        if api is None:
            try:
                import baostock as api_module  # type: ignore[import-untyped]
            except ModuleNotFoundError as error:
                if error.name == "baostock":
                    message = "BaoStock 未安装，请重新安装 EquiSeek Python 依赖"
                else:
                    message = f"BaoStock 运行依赖不完整：缺少 {error.name or '未知模块'}"
                raise RuntimeError(message) from error
            except ImportError as error:
                raise RuntimeError(f"BaoStock 导入失败：{type(error).__name__}") from error
            api = api_module
        self.api = api

    def fetch_daily(
        self, symbol: str, start_date: date, end_date: date, adjustment: AdjustmentMode
    ) -> MarketDataSet:
        if start_date >= end_date:
            raise ValueError("开始日期必须早于结束日期")
        code = normalize_stock_code(symbol)
        login = self.api.login()
        if str(login.error_code) != "0":
            raise RuntimeError(f"BaoStock 登录失败：{login.error_msg}")
        try:
            query = self.api.query_history_k_data_plus(
                code,
                "date,code,open,high,low,close,preclose,volume,amount",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                frequency="d",
                adjustflag={
                    AdjustmentMode.HFQ: "1",
                    AdjustmentMode.QFQ: "2",
                    AdjustmentMode.BFQ: "3",
                }[adjustment],
            )
            if str(query.error_code) != "0":
                raise RuntimeError(f"BaoStock 查询失败：{query.error_msg}")
            rows: list[PriceBar] = []
            fields = list(query.fields)
            while query.next():
                raw = dict(zip(fields, query.get_row_data(), strict=True))
                if not raw.get("close"):
                    continue
                rows.append(
                    PriceBar(
                        trade_date=date.fromisoformat(raw["date"]),
                        open=float(raw["open"]),
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        close=float(raw["close"]),
                        pre_close=float(raw["preclose"]) if raw.get("preclose") else None,
                        volume=float(raw.get("volume") or 0),
                        amount=float(raw.get("amount") or 0),
                    )
                )
        finally:
            self.api.logout()
        rows.sort(key=lambda item: item.trade_date)
        if not rows:
            raise ValueError("公开数据源未返回行情，请检查代码、日期或网络")
        return MarketDataSet(
            symbol=display_stock_code(code),
            source="baostock",
            adjustment=adjustment,
            bars=tuple(rows),
            fetched_at=datetime.now(UTC).isoformat(),
            warnings=("BaoStock 为第三方公开历史数据源，数据准确性与使用条件以其说明为准。",),
        )
