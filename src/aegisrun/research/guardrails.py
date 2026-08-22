from __future__ import annotations

import re


class UnsafeAdviceError(ValueError):
    pass


class AdviceGuard:
    _patterns = (
        r"建议.{0,6}(买入|卖出|持有|加仓|减仓|建仓|清仓)",
        r"(可以买|应该买|值得买|适合买|立即买|逢低买)",
        r"(目标价|目标价格|止损|止盈|仓位|建仓点|买入点|卖出点)",
        r"(上涨|下跌|涨停|跌停).{0,8}(概率|可能性|预期)",
        r"(未来|后市).{0,10}(上涨|下跌|走强|走弱|看多|看空)",
        r"(稳赚|必涨|保本|保证收益|收益承诺|翻倍股|十倍股)",
        r"\b(BUY|SELL|HOLD)\b",
    )

    def ensure_safe(self, text: str, *, allow_disclaimer: bool = False) -> None:
        normalized = re.sub(r"\s+", "", text).upper()
        if allow_disclaimer:
            normalized = normalized.replace("不预测未来价格", "").replace("不提供买卖建议", "")
            normalized = normalized.replace("不构成投资建议", "")
        for pattern in self._patterns:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                raise UnsafeAdviceError("模型输出包含投资建议、预测或交易动作表达")


class InvestmentOutputGuard:
    """Allow evidence-backed advice while blocking guarantees and execution claims."""

    _patterns = (
        r"(稳赚|必涨|必跌|保本|保证收益|收益承诺|零风险|毫无风险|完全无风险|百分之百|100%胜率)",
        r"(已经|已为你|替你|自动).{0,8}(下单|委托|成交)",
        r"(已为你|替你|自动).{0,8}(买入|卖出)",
        r"已经(完成|执行)?了?(买入|卖出)",
        r"(无需核对|无需复核|不用止损|绝不会亏)",
    )

    def ensure_safe(self, text: str) -> None:
        normalized = re.sub(r"\s+", "", text)
        for allowed_disclaimer in (
            "不保证未来结果",
            "不保证收益",
            "不能保证收益",
            "无法保证收益",
            "不得承诺收益",
            "不是收益承诺",
            "不构成收益承诺",
            "不属于收益承诺",
            "并非收益承诺",
            "不是目标价或收益承诺",
            "不承诺收益",
            "不会承诺收益",
            "不会自动下单",
            "不自动下单",
            "未自动下单",
            "没有自动下单",
            "不得声称已代用户执行交易",
            "不代表已代用户执行交易",
            "不代表已代您执行任何交易",
        ):
            normalized = normalized.replace(allowed_disclaimer, "")
        for pattern in self._patterns:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                raise UnsafeAdviceError("输出包含收益保证、绝对化判断或未经授权的交易执行")
