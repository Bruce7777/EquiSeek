from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import replace
from datetime import date
from html import unescape
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from aegisrun.macro.models import MacroMetric, MacroSnapshot
from aegisrun.user_data import user_data_root

MAX_MACRO_JSON_BYTES = 1024 * 1024
MAX_OFFICIAL_RELEASE_BYTES = 2 * 1024 * 1024

PBOC_H1_2026 = (
    "中国人民银行：2026 年上半年金融统计数据（政府网站转载）",
    "https://dfjrjgj.hlj.gov.cn/hljjrjd/c113262/202607/c00_31958680.shtml",
)
SAFE_JUNE_2026 = (
    "国家外汇管理局：2026 年 6 月银行结售汇和涉外收付款",
    "https://www.safe.gov.cn/safe/2026/0717/27704.html",
)
NBS_H1_2026 = (
    "国家统计局：2026 年上半年国民经济运行",
    "https://www.stats.gov.cn/sj/xwfbh/fbhwd/202607/t20260715_1964121.html",
)
NBS_INCOME_H1_2026 = (
    "国家统计局：2026 年上半年居民收入和消费支出",
    "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260715_1964129.html",
)
NBS_RETAIL_H1_2026 = (
    "国家统计局：2026 年上半年社会消费品零售总额",
    "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260715_1964127.html",
)
NBS_CPI_JUNE_2026 = (
    "国家统计局：2026 年 6 月居民消费价格",
    "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260709_1964084.html",
)
NBS_PPI_JUNE_2026 = (
    "国家统计局：2026 年 6 月工业生产者价格",
    "https://www.stats.gov.cn/sj/zxfb/202607/t20260709_1964083.html",
)
NBS_PROFIT_H1_2026 = (
    "国家统计局：2026 年上半年规模以上工业企业利润",
    "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260727_1964194.html",
)
MOF_H1_2026 = (
    "财政部：2026 年上半年财政收支",
    "https://qh.mof.gov.cn/bszn/tongzhitonggao/202607/t20260723_3994044.htm",
)


class MacroDataProvider(Protocol):
    def load(self) -> MacroSnapshot: ...


def _metric(
    code: str,
    name: str,
    value: float,
    unit: str,
    period: str,
    source: tuple[str, str],
    note: str = "",
) -> MacroMetric:
    return MacroMetric(code, name, value, unit, period, source[0], source[1], note)


class BundledOfficialMacroProvider:
    """Audited official historical baseline; it is not a live-data claim."""

    def load(self) -> MacroSnapshot:
        return MacroSnapshot(
            version="cn-macro-official-h1-2026.2",
            label="中国宏观官方基线（2026 年上半年）",
            as_of=date(2026, 6, 30),
            metrics=(
                _metric("m2_yoy", "M2 同比", 8.0, "%", "2026-06", PBOC_H1_2026),
                _metric(
                    "m1_yoy",
                    "M1 同比",
                    4.0,
                    "%",
                    "2026-06",
                    PBOC_H1_2026,
                    "人民银行自 2025 年起采用修订后的 M1 统计口径",
                ),
                _metric("tsf_stock_yoy", "社融存量同比", 7.4, "%", "2026-06", PBOC_H1_2026),
                _metric("rmb_loan_yoy", "人民币贷款同比", 5.2, "%", "2026-06", PBOC_H1_2026),
                _metric(
                    "government_bond_financing",
                    "政府债券净融资",
                    6.44,
                    "万亿元",
                    "2026-H1",
                    PBOC_H1_2026,
                    "同比少 1.22 万亿元",
                ),
                _metric(
                    "interbank_rate",
                    "银行间同业拆借月加权平均利率",
                    1.41,
                    "%",
                    "2026-06",
                    PBOC_H1_2026,
                ),
                _metric("bank_fx_settlement", "银行结汇", 20299, "亿元", "2026-06", SAFE_JUNE_2026),
                _metric("bank_fx_sales", "银行售汇", 16437, "亿元", "2026-06", SAFE_JUNE_2026),
                _metric(
                    "cross_border_receipts",
                    "银行代客涉外收入",
                    60951,
                    "亿元",
                    "2026-06",
                    SAFE_JUNE_2026,
                ),
                _metric(
                    "cross_border_payments",
                    "银行代客对外付款",
                    58420,
                    "亿元",
                    "2026-06",
                    SAFE_JUNE_2026,
                ),
                _metric("gdp_real_yoy", "实际 GDP 同比", 4.7, "%", "2026-H1", NBS_H1_2026),
                _metric("fixed_asset_yoy", "固定资产投资同比", -5.7, "%", "2026-H1", NBS_H1_2026),
                _metric(
                    "manufacturing_investment_yoy",
                    "制造业投资同比",
                    -1.2,
                    "%",
                    "2026-H1",
                    NBS_H1_2026,
                ),
                _metric(
                    "private_investment_yoy",
                    "民间投资同比",
                    -8.5,
                    "%",
                    "2026-H1",
                    NBS_H1_2026,
                ),
                _metric(
                    "real_estate_investment_yoy",
                    "房地产开发投资同比",
                    -18.0,
                    "%",
                    "2026-H1",
                    NBS_H1_2026,
                ),
                _metric("trade_yoy", "货物进出口同比", 16.9, "%", "2026-H1", NBS_H1_2026),
                _metric(
                    "retail_sales_yoy",
                    "社会消费品零售总额同比",
                    1.3,
                    "%",
                    "2026-H1",
                    NBS_RETAIL_H1_2026,
                ),
                _metric("cpi_yoy", "居民消费价格同比", 1.0, "%", "2026-06", NBS_CPI_JUNE_2026),
                _metric(
                    "rural_cpi_yoy",
                    "农村居民消费价格同比",
                    0.8,
                    "%",
                    "2026-06",
                    NBS_CPI_JUNE_2026,
                ),
                _metric(
                    "ppi_yoy",
                    "工业生产者出厂价格同比",
                    4.1,
                    "%",
                    "2026-06",
                    NBS_PPI_JUNE_2026,
                ),
                _metric(
                    "producer_purchase_price_yoy",
                    "工业生产者购进价格同比",
                    6.4,
                    "%",
                    "2026-06",
                    NBS_PPI_JUNE_2026,
                ),
                _metric(
                    "industrial_profit_yoy",
                    "规模以上工业企业利润同比",
                    18.7,
                    "%",
                    "2026-H1",
                    NBS_PROFIT_H1_2026,
                ),
                _metric(
                    "private_industrial_profit_yoy",
                    "私营工业企业利润同比",
                    13.0,
                    "%",
                    "2026-H1",
                    NBS_PROFIT_H1_2026,
                ),
                _metric(
                    "urban_income_yoy",
                    "城镇居民人均可支配收入同比",
                    4.4,
                    "%",
                    "2026-H1",
                    NBS_INCOME_H1_2026,
                ),
                _metric(
                    "rural_income_yoy",
                    "农村居民人均可支配收入同比",
                    6.4,
                    "%",
                    "2026-H1",
                    NBS_INCOME_H1_2026,
                ),
                _metric(
                    "property_income_yoy",
                    "居民人均财产净收入同比",
                    1.1,
                    "%",
                    "2026-H1",
                    NBS_INCOME_H1_2026,
                ),
                _metric(
                    "wage_income_yoy",
                    "居民人均工资性收入同比",
                    5.3,
                    "%",
                    "2026-H1",
                    NBS_INCOME_H1_2026,
                ),
                _metric(
                    "land_sale_revenue_yoy",
                    "国有土地使用权出让收入同比",
                    -31.5,
                    "%",
                    "2026-H1",
                    MOF_H1_2026,
                ),
                _metric(
                    "government_fund_revenue_yoy",
                    "政府性基金预算收入同比",
                    -21.6,
                    "%",
                    "2026-H1",
                    MOF_H1_2026,
                ),
                _metric(
                    "government_fund_spending_yoy",
                    "政府性基金预算支出同比",
                    -16.4,
                    "%",
                    "2026-H1",
                    MOF_H1_2026,
                ),
                _metric(
                    "agriculture_spending_yoy",
                    "农林水财政支出同比",
                    -8.6,
                    "%",
                    "2026-H1",
                    MOF_H1_2026,
                ),
                _metric(
                    "debt_interest_spending_yoy",
                    "债务付息支出同比",
                    4.5,
                    "%",
                    "2026-H1",
                    MOF_H1_2026,
                ),
            ),
            methodology_sources=(
                (
                    "卢麒元资本流动相关讲座文本（用于流向/流量/流速解释框架）",
                    "https://www.weibo.com/ttarticle/p/show?id=2309405047566243856502",
                ),
                (
                    "温铁军：贫困经济学——资本化与制度成本转嫁",
                    "https://www.aisixiang.com/data/54162.html",
                ),
                (
                    "董筱丹、温铁军：制度成本转嫁研究",
                    "https://jjll.ruc.edu.cn/CN/article/downloadArticleFile.do?"
                    "attachType=PDF&id=8408",
                ),
                (
                    "中国证监会基金投资者教育手册：长期、分散与风险匹配",
                    "https://www.csrc.gov.cn/qingdao/c105643/c1319462/content.shtml",
                ),
                (
                    "Investor.gov：Asset Allocation and Diversification",
                    "https://www.investor.gov/introduction-investing/getting-started/"
                    "asset-allocation",
                ),
            ),
            warnings=(
                "这是截至 2026-06-30 的官方半年度基线；后续月份发布后不会自动更新。",
                "流向/流量/传导和成本转嫁分数是求衡的透明代理模型，"
                "不是理论作者发布的计量公式。",
            ),
        )


_ANCHOR = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
_HREF = re.compile(r"\bhref\s*=\s*['\"](?P<value>[^'\"]+)['\"]", re.I)
_TITLE = re.compile(r"\btitle\s*=\s*['\"](?P<value>[^'\"]+)['\"]", re.I)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain_text(value: str) -> str:
    return _SPACE.sub(" ", unescape(_TAG.sub(" ", _SCRIPT_STYLE.sub(" ", value)))).strip()


def _signed_percentage(text: str, pattern: str) -> float:
    match = re.search(pattern, text, flags=re.I)
    if match is None:
        raise ValueError(f"官方发布正文缺少指标：{pattern[:80]}")
    direction, raw = match.group("direction"), match.group("value")
    value = float(raw)
    return -value if direction in {"下降", "减少", "回落", "下跌"} else value


def _number(text: str, pattern: str) -> float:
    match = re.search(pattern, text, flags=re.I)
    if match is None:
        raise ValueError(f"官方发布正文缺少指标：{pattern[:80]}")
    return float(match.group("value"))


class LiveOfficialMacroProvider:
    """Build a current structured snapshot from the latest four official releases."""

    indexes = {
        "nbs": "https://www.stats.gov.cn/sj/zxfb/",
        "pboc": "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html",
        "safe": "https://www.safe.gov.cn/safe/sjjd/index.html",
        "mof": "https://gks.mof.gov.cn/tongjishuju/",
    }
    hostnames = {
        "nbs": "stats.gov.cn",
        "pboc": "pbc.gov.cn",
        "safe": "safe.gov.cn",
        "mof": "mof.gov.cn",
    }

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        today: date | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.transport = transport
        self.today = today
        self.timeout_seconds = timeout_seconds

    def load(self) -> MacroSnapshot:
        reference_date = self.today or date.today()
        baseline = BundledOfficialMacroProvider().load()
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "EquiSeek/0.1 official-macro-refresh"},
        ) as client:
            indexes = {key: self._fetch(client, key, url) for key, url in self.indexes.items()}
            urls = {
                "pboc": self._latest_link(
                    indexes["pboc"], self.indexes["pboc"], "金融统计数据报告"
                ),
                "safe": self._latest_link(
                    indexes["safe"],
                    self.indexes["safe"],
                    "银行结售汇和银行代客涉外收付款数据",
                ),
                "mof": self._latest_link(indexes["mof"], self.indexes["mof"], "财政收支情况"),
                "nbs_activity": self._latest_link(indexes["nbs"], self.indexes["nbs"], "国民经济"),
                "nbs_cpi": self._latest_link(
                    indexes["nbs"], self.indexes["nbs"], "居民消费价格同比"
                ),
                "nbs_ppi": self._latest_link(
                    indexes["nbs"], self.indexes["nbs"], "工业生产者出厂价格同比"
                ),
            }
            pages = {
                key: _plain_text(self._fetch(client, key.split("_")[0], url))
                for key, url in urls.items()
            }

        periods = {
            "month": self._latest_month_period(" ".join(pages.values()), reference_date),
            "cumulative": self._latest_cumulative_period(pages["nbs_activity"], reference_date),
        }
        updates = self._extract_updates(pages, urls, periods)
        expected = {
            "m2_yoy",
            "m1_yoy",
            "tsf_stock_yoy",
            "rmb_loan_yoy",
            "government_bond_financing",
            "interbank_rate",
            "bank_fx_settlement",
            "bank_fx_sales",
            "cross_border_receipts",
            "cross_border_payments",
            "fixed_asset_yoy",
            "manufacturing_investment_yoy",
            "private_investment_yoy",
            "real_estate_investment_yoy",
            "trade_yoy",
            "retail_sales_yoy",
            "cpi_yoy",
            "rural_cpi_yoy",
            "ppi_yoy",
            "producer_purchase_price_yoy",
            "land_sale_revenue_yoy",
            "government_fund_revenue_yoy",
            "government_fund_spending_yoy",
            "agriculture_spending_yoy",
            "debt_interest_spending_yoy",
        }
        missing = sorted(expected - updates.keys())
        if missing:
            raise ValueError(f"官方最新数据结构化覆盖不完整：{', '.join(missing)}")
        metrics = tuple(
            replace(
                metric,
                value=updates[metric.code][0],
                period=updates[metric.code][1],
                source_name=updates[metric.code][2],
                source_url=updates[metric.code][3],
                note=updates[metric.code][4],
            )
            if metric.code in updates
            else metric
            for metric in baseline.metrics
        )
        return MacroSnapshot(
            version=f"cn-macro-official-live-{reference_date.isoformat()}",
            label=f"中国宏观官方联网快照（采集截止 {reference_date.isoformat()}）",
            as_of=reference_date,
            metrics=metrics,
            methodology_sources=baseline.methodology_sources,
            warnings=(
                f"本次从 4 个官方站点更新 {len(updates)}/{len(metrics)} 项核心指标。",
                "GDP、居民收入和工业利润等低频指标保留各自最新官方统计期；"
                "每个指标的统计期和原文链接均单独展示。",
                "采集截止日表示本轮已检查官方发布的时间，不代表所有指标都是日频数据。",
                *baseline.warnings[1:],
            ),
        )

    def _fetch(self, client: httpx.Client, source: str, url: str) -> str:
        response = client.get(url)
        response.raise_for_status()
        hostname = (urlparse(str(response.url)).hostname or "").lower()
        expected = self.hostnames[source]
        if hostname != expected and not hostname.endswith(f".{expected}"):
            raise ValueError(f"{source} 官方页面重定向到了非白名单域名")
        if len(response.content) > MAX_OFFICIAL_RELEASE_BYTES:
            raise ValueError(f"{source} 官方页面超过 2 MiB 安全读取上限")
        return response.text

    @staticmethod
    def _latest_link(index_html: str, base_url: str, title_fragment: str) -> str:
        for anchor in _ANCHOR.finditer(index_html):
            attrs = anchor.group("attrs")
            href = _HREF.search(attrs)
            if href is None:
                continue
            title_match = _TITLE.search(attrs)
            title = (
                unescape(title_match.group("value"))
                if title_match
                else _plain_text(anchor.group("body"))
            )
            if title_fragment in _SPACE.sub("", title):
                url = urljoin(base_url, href.group("value"))
                if url.startswith("https://"):
                    return str(url)
        raise ValueError(f"官方列表页未找到最新发布：{title_fragment}")

    @staticmethod
    def _latest_month_period(text: str, fallback: date) -> str:
        values: list[tuple[int, int]] = [
            (int(year), int(month))
            for year, month in re.findall(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
            if 1 <= int(month) <= 12
        ]
        counts = Counter(values)
        year, month = max(
            counts,
            key=lambda value: (counts[value], value),
            default=(fallback.year, fallback.month),
        )
        return f"{year:04d}-{month:02d}"

    @staticmethod
    def _latest_cumulative_period(text: str, fallback: date) -> str:
        match = re.search(r"(20\d{2})\s*年\s*1\s*[—–-]\s*(\d{1,2})\s*月", text)
        if match is None:
            return f"{fallback.year:04d}-YTD"
        return f"{int(match.group(1)):04d}-01..{int(match.group(2)):02d}"

    @staticmethod
    def _extract_updates(
        pages: dict[str, str],
        urls: dict[str, str],
        periods: dict[str, str],
    ) -> dict[str, tuple[float, str, str, str, str]]:
        pboc, safe = pages["pboc"], pages["safe"]
        activity, cpi, ppi, mof = (
            pages["nbs_activity"],
            pages["nbs_cpi"],
            pages["nbs_ppi"],
            pages["mof"],
        )
        monthly, cumulative = periods["month"], periods["cumulative"]
        signed = r"(?P<direction>增长|下降|上涨|减少|回落)\s*(?P<value>\d+(?:\.\d+)?)\s*%"
        pboc_source = "中国人民银行：最新金融统计数据报告"
        nbs_source = "国家统计局：最新国民经济运行数据"
        safe_source = "国家外汇管理局：最新银行结售汇和涉外收付款数据"
        mof_source = "财政部：最新财政收支情况"

        settlement = re.search(
            r"银行结汇\s*(?P<settlement>\d+(?:\.\d+)?)\s*亿元人民币?，?售汇\s*(?P<sales>\d+(?:\.\d+)?)\s*亿元",
            safe,
        )
        cross_border = re.search(
            r"银行代客涉外收入\s*(?P<receipts>\d+(?:\.\d+)?)\s*亿元人民币?，?对外付款\s*(?P<payments>\d+(?:\.\d+)?)\s*亿元",
            safe,
        )
        if settlement is None or cross_border is None:
            raise ValueError("外汇局最新发布缺少结售汇或涉外收付款核心值")

        def item(
            value: float, period: str, name: str, url: str, note: str
        ) -> tuple[float, str, str, str, str]:
            return (value, period, name, url, note)

        updates = {
            "m2_yoy": item(
                _signed_percentage(pboc, rf"广义货币（?M2）?.{{0,40}}?同比{signed}"),
                monthly,
                pboc_source,
                urls["pboc"],
                "联网提取并通过固定口径校验",
            ),
            "m1_yoy": item(
                _signed_percentage(pboc, rf"狭义货币（?M1）?.{{0,40}}?同比{signed}"),
                monthly,
                pboc_source,
                urls["pboc"],
                "人民银行修订后 M1 口径",
            ),
            "tsf_stock_yoy": item(
                _signed_percentage(pboc, rf"社会融资规模存量.{{0,40}}?同比{signed}"),
                monthly,
                pboc_source,
                urls["pboc"],
                "社会融资规模存量同比",
            ),
            "rmb_loan_yoy": item(
                _signed_percentage(pboc, rf"对实体经济发放的人民币贷款余额.{{0,40}}?同比{signed}"),
                monthly,
                pboc_source,
                urls["pboc"],
                "对实体经济人民币贷款余额同比",
            ),
            "government_bond_financing": item(
                _number(pboc, r"政府债券净融资\s*(?P<value>\d+(?:\.\d+)?)\s*万亿元"),
                cumulative,
                pboc_source,
                urls["pboc"],
                "本年累计净融资",
            ),
            "interbank_rate": item(
                _number(pboc, r"同业拆借月?加权平均利率为\s*(?P<value>\d+(?:\.\d+)?)\s*%"),
                monthly,
                pboc_source,
                urls["pboc"],
                "月加权平均利率",
            ),
            "bank_fx_settlement": item(
                float(settlement.group("settlement")),
                monthly,
                safe_source,
                urls["safe"],
                "当月人民币口径",
            ),
            "bank_fx_sales": item(
                float(settlement.group("sales")),
                monthly,
                safe_source,
                urls["safe"],
                "当月人民币口径",
            ),
            "cross_border_receipts": item(
                float(cross_border.group("receipts")),
                monthly,
                safe_source,
                urls["safe"],
                "当月银行代客口径",
            ),
            "cross_border_payments": item(
                float(cross_border.group("payments")),
                monthly,
                safe_source,
                urls["safe"],
                "当月银行代客口径",
            ),
            "fixed_asset_yoy": item(
                _signed_percentage(
                    activity, rf"全国固定资产投资（不含农户）.{{0,40}}?同比{signed}"
                ),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "manufacturing_investment_yoy": item(
                _signed_percentage(activity, rf"制造业投资{signed}"),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "private_investment_yoy": item(
                _signed_percentage(activity, rf"民间投资同比{signed}"),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "real_estate_investment_yoy": item(
                _signed_percentage(activity, rf"房地产开发投资{signed}"),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "trade_yoy": item(
                _signed_percentage(activity, rf"货物进出口总额.{{0,40}}?同比{signed}"),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "retail_sales_yoy": item(
                _signed_percentage(activity, rf"社会消费品零售总额.{{0,40}}?同比{signed}"),
                cumulative,
                nbs_source,
                urls["nbs_activity"],
                "累计同比",
            ),
            "cpi_yoy": item(
                _signed_percentage(cpi, rf"全国居民消费价格同比{signed}"),
                monthly,
                "国家统计局：最新居民消费价格",
                urls["nbs_cpi"],
                "当月同比",
            ),
            "rural_cpi_yoy": item(
                _signed_percentage(cpi, rf"农村{signed}"),
                monthly,
                "国家统计局：最新居民消费价格",
                urls["nbs_cpi"],
                "农村居民当月同比",
            ),
            "ppi_yoy": item(
                _signed_percentage(ppi, rf"工业生产者出厂价格同比{signed}"),
                monthly,
                "国家统计局：最新工业生产者价格",
                urls["nbs_ppi"],
                "当月同比",
            ),
            "producer_purchase_price_yoy": item(
                _signed_percentage(ppi, rf"工业生产者购进价格同比{signed}"),
                monthly,
                "国家统计局：最新工业生产者价格",
                urls["nbs_ppi"],
                "当月同比",
            ),
            "land_sale_revenue_yoy": item(
                _signed_percentage(mof, rf"国有土地使用权出让收入.{{0,40}}?同比{signed}"),
                cumulative,
                mof_source,
                urls["mof"],
                "累计同比",
            ),
            "government_fund_revenue_yoy": item(
                _signed_percentage(mof, rf"全国政府性基金预算收入.{{0,40}}?同比{signed}"),
                cumulative,
                mof_source,
                urls["mof"],
                "累计同比",
            ),
            "government_fund_spending_yoy": item(
                _signed_percentage(mof, rf"全国政府性基金预算支出.{{0,40}}?同比{signed}"),
                cumulative,
                mof_source,
                urls["mof"],
                "累计同比",
            ),
            "agriculture_spending_yoy": item(
                _signed_percentage(mof, rf"农林水支出.{{0,40}}?同比{signed}"),
                cumulative,
                mof_source,
                urls["mof"],
                "累计同比",
            ),
            "debt_interest_spending_yoy": item(
                _signed_percentage(mof, rf"债务付息支出.{{0,40}}?同比{signed}"),
                cumulative,
                mof_source,
                urls["mof"],
                "累计同比",
            ),
        }
        return updates


class CachedLiveOfficialMacroProvider:
    def __init__(self, live: LiveOfficialMacroProvider, cache_path: Path) -> None:
        self.live = live
        self.cache_path = cache_path

    def load(self) -> MacroSnapshot:
        try:
            snapshot = self.live.load()
        except (httpx.HTTPError, ValueError, UnicodeError) as error:
            if self.cache_path.is_file() and not self.cache_path.is_symlink():
                cached = JsonMacroProvider(self.cache_path).load()
                fallback_warning = (
                    "本轮官网刷新失败，使用最近一次完整联网快照："
                    f"{type(error).__name__}: {str(error)[:160]}"
                )
                return replace(
                    cached,
                    warnings=(
                        fallback_warning,
                        *cached.warnings,
                    ),
                )
            raise RuntimeError(f"官方最新宏观数据刷新失败：{error}") from error
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.cache_path)
        return snapshot


class JsonMacroProvider:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def load(self) -> MacroSnapshot:
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("宏观数据 JSON 必须是可读的普通文件")
        if self.path.stat().st_size > MAX_MACRO_JSON_BYTES:
            raise ValueError("宏观数据 JSON 超过 1 MiB 限制")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("宏观数据 JSON 无法读取或格式无效") from error
        if not isinstance(value, dict):
            raise ValueError("宏观数据 JSON 顶层必须是对象")
        return MacroSnapshot.from_dict(value)


def default_macro_provider(*, live: bool = False) -> MacroDataProvider:
    custom = os.getenv("EQUISEEK_MACRO_DATA_PATH", "").strip() or os.getenv(
        "AEGISRUN_MACRO_DATA_PATH", ""
    ).strip()
    if custom:
        return JsonMacroProvider(Path(custom))
    if live:
        root = user_data_root()
        return CachedLiveOfficialMacroProvider(
            LiveOfficialMacroProvider(),
            root / "macro" / "latest-official-snapshot.json",
        )
    return BundledOfficialMacroProvider()
