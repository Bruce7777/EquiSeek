from __future__ import annotations

from datetime import date

import httpx

from aegisrun.macro.providers import LiveOfficialMacroProvider


def test_live_official_provider_discovers_latest_pages_and_refreshes_core_metrics() -> None:
    pages = {
        "/sj/zxfb/": """
            <a href="/sj/zxfb/202608/t20260817_1.html">2026年7月份国民经济运行情况</a>
            <a href="/sj/zxfb/202608/t20260809_2.html">2026年7月份居民消费价格同比上涨0.5%</a>
            <a href="/sj/zxfb/202608/t20260809_3.html">2026年7月份工业生产者出厂价格同比上涨3.5%</a>
        """,
        "/sj/zxfb/202608/t20260817_1.html": """
            2026年1—7月份，全国固定资产投资（不含农户）同比下降6.7%。
            制造业投资下降1.7%，民间投资同比下降9.4%，房地产开发投资下降19.2%。
            货物进出口总额同比增长17.3%，社会消费品零售总额同比增长1.2%。
        """,
        "/sj/zxfb/202608/t20260809_2.html": (
            "2026年7月份，全国居民消费价格同比上涨0.5%，农村上涨0.4%。"
        ),
        "/sj/zxfb/202608/t20260809_3.html": (
            "2026年7月份，工业生产者出厂价格同比上涨3.5%，工业生产者购进价格同比上涨5.5%。"
        ),
        "/diaochatongjisi/116219/index.html": (
            '<a href="/diaochatongjisi/116219/202608/report.html">2026年7月金融统计数据报告</a>'
        ),
        "/diaochatongjisi/116219/202608/report.html": """
            2026年7月末，广义货币（M2）余额同比增长7.7%，狭义货币（M1）余额同比增长4.0%。
            社会融资规模存量同比增长7.4%，对实体经济发放的人民币贷款余额同比增长5.2%。
            政府债券净融资7.76万亿元，同业拆借月加权平均利率为1.4%。
        """,
        "/safe/sjjd/index.html": (
            '<a href="/safe/2026/0817/27789.html">国家外汇管理局公布2026年7月'
            "银行结售汇和银行代客涉外收付款数据</a>"
        ),
        "/safe/2026/0817/27789.html": """
            2026年7月，银行结汇18097亿元人民币，售汇16856亿元。
            银行代客涉外收入58837亿元人民币，对外付款54770亿元。
        """,
        "/tongjishuju/": (
            '<a href="/tongjishuju/202608/t20260814_1.htm">2026年1—7月财政收支情况</a>'
        ),
        "/tongjishuju/202608/t20260814_1.htm": """
            2026年1—7月，国有土地使用权出让收入同比下降30.8%。
            全国政府性基金预算收入同比下降21.2%，全国政府性基金预算支出同比下降16.4%。
            农林水支出同比下降6.8%，债务付息支出同比增长5.2%。
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[request.url.path], request=request)

    snapshot = LiveOfficialMacroProvider(
        transport=httpx.MockTransport(handler),
        today=date(2026, 8, 22),
    ).load()
    metrics = {item.code: item for item in snapshot.metrics}

    assert snapshot.as_of == date(2026, 8, 22)
    assert snapshot.version == "cn-macro-official-live-2026-08-22"
    assert metrics["m2_yoy"].value == 7.7
    assert metrics["m2_yoy"].period == "2026-07"
    assert metrics["bank_fx_settlement"].value == 18097
    assert metrics["fixed_asset_yoy"].value == -6.7
    assert metrics["fixed_asset_yoy"].period == "2026-01..07"
    assert metrics["land_sale_revenue_yoy"].value == -30.8
    assert metrics["gdp_real_yoy"].period == "2026-H1"
    assert "更新 25/32 项核心指标" in snapshot.warnings[0]
