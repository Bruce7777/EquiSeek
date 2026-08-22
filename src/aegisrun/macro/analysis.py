from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from aegisrun.macro.models import MacroSnapshot


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, round(value)))


@dataclass(frozen=True, slots=True)
class CapitalFlowPath:
    name: str
    dimension: str
    score: int
    status: str
    source: str
    channel: str
    destination: str
    evidence: tuple[str, ...]
    investment_effect: str


@dataclass(frozen=True, slots=True)
class CapitalFlowAssessment:
    direction_score: int
    direction_label: str
    volume_score: int
    volume_label: str
    transmission_score: int
    transmission_label: str
    speed_score: int
    speed_label: str
    fx_settlement_net_100m: float
    cross_border_net_receipts_100m: float
    evidence: tuple[str, ...]
    allocation_evidence: tuple[str, ...]
    paths: tuple[CapitalFlowPath, ...]
    bottlenecks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CostTransferChain:
    name: str
    pressure_score: int
    source: str
    channel: str
    bearer: str
    beneficiary: str
    investment_effect: str
    evidence: tuple[str, ...]
    confirmation: str
    reversal_conditions: str


@dataclass(frozen=True, slots=True)
class CostTransferAssessment:
    pressure_score: int
    pressure_label: str
    channels: tuple[str, ...]
    offsets: tuple[str, ...]
    chains: tuple[CostTransferChain, ...]


@dataclass(frozen=True, slots=True)
class SectorView:
    sector: str
    stance: str
    stance_label: str
    confidence: int
    rationale: str
    confirmation: str
    risk: str


@dataclass(frozen=True, slots=True)
class AssetAllocationTarget:
    key: str
    label: str
    asset_class: str
    strategic_pct: int
    target_pct: int
    minimum_pct: int
    maximum_pct: int
    action: str
    action_label: str
    vehicles: str
    purpose: str
    macro_rationale: str
    primary_risk: str


@dataclass(frozen=True, slots=True)
class AllocationBuildStep:
    order: int
    timing: str
    portfolio_pct: int
    instruction: str
    gate: str


@dataclass(frozen=True, slots=True)
class LongTermAllocationPlan:
    profile: str
    label: str
    suitability: str
    horizon: str
    drawdown_tolerance: str
    prerequisite: str
    targets: tuple[AssetAllocationTarget, ...]
    build_steps: tuple[AllocationBuildStep, ...]
    rebalance_rules: tuple[str, ...]
    increase_risk_triggers: tuple[str, ...]
    decrease_risk_triggers: tuple[str, ...]
    guardrails: tuple[str, ...]
    strategy_version: str = "macro-allocation-2026.08.1"

    @property
    def equity_target_pct(self) -> int:
        return sum(target.target_pct for target in self.targets if target.asset_class == "equity")

    @property
    def equity_strategic_pct(self) -> int:
        return sum(
            target.strategic_pct for target in self.targets if target.asset_class == "equity"
        )


@dataclass(frozen=True, slots=True)
class MacroInvestmentView:
    risk_appetite_score: int
    risk_appetite_label: str
    equity_exposure: str
    style_tilt: tuple[str, ...]
    sectors: tuple[SectorView, ...]
    decision_summary: tuple[str, ...]
    default_allocation_profile: str
    allocation_plans: tuple[LongTermAllocationPlan, ...]


@dataclass(frozen=True, slots=True)
class MacroAnalysis:
    version: str
    snapshot: MacroSnapshot
    capital_flow: CapitalFlowAssessment
    cost_transfer: CostTransferAssessment
    investment_view: MacroInvestmentView
    regime: str
    research_implications: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["snapshot"]["as_of"] = self.snapshot.as_of.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class MacroOverlay:
    industry: str
    matched_sector: str
    stance: str
    stance_label: str
    sector_confidence: int
    confidence_adjustment: int
    rationale: str
    macro_as_of: str
    mapping_version: str = "macro-industry-map-2026.08.1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_INDUSTRY_KEYWORDS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("高端制造", "工业自动化", "自动化", "电力设备", "半导体", "机器人", "机械")),
    (1, ("公用事业", "运营商", "电信", "通信运营", "高股息", "水务", "电力运营")),
    (2, ("券商", "证券", "资本市场服务")),
    (3, ("房地产", "地产", "建筑", "建材", "水泥", "家居")),
    (4, ("消费", "零售", "餐饮", "地方回款", "应收账款", "小盘")),
)


def build_macro_overlay(industry: str, analysis: MacroAnalysis) -> MacroOverlay:
    """Map a user-supplied industry to a transparent macro sector view.

    No company industry is inferred automatically: an empty or unmatched label stays
    unmapped and therefore cannot change confidence.
    """

    normalized = "".join(industry.strip().lower().split())[:80]
    matched_index: int | None = None
    for index, keywords in _INDUSTRY_KEYWORDS:
        if any(keyword.lower() in normalized for keyword in keywords):
            matched_index = index
            break
    if matched_index is None:
        return MacroOverlay(
            industry=industry.strip()[:80],
            matched_sector="未映射",
            stance="unmapped",
            stance_label="未纳入宏观调整",
            sector_confidence=0,
            confidence_adjustment=0,
            rationale="未提供可识别行业，宏观框架不改变技术结论或置信度。",
            macro_as_of=analysis.snapshot.as_of.isoformat(),
        )
    sector = analysis.investment_view.sectors[matched_index]
    adjustment = 0
    if sector.stance == "overweight":
        adjustment = min(8, max(1, round(sector.confidence / 12)))
    elif sector.stance == "underweight":
        adjustment = -min(12, max(1, round(sector.confidence / 8)))
    return MacroOverlay(
        industry=industry.strip()[:80],
        matched_sector=sector.sector,
        stance=sector.stance,
        stance_label=sector.stance_label,
        sector_confidence=sector.confidence,
        confidence_adjustment=adjustment,
        rationale=sector.rationale,
        macro_as_of=analysis.snapshot.as_of.isoformat(),
    )


def _flow_label(score: int) -> str:
    if score >= 35:
        return "跨境净流入，资本方向边际正向"
    if score <= -35:
        return "跨境净流出，资本方向偏负向"
    return "跨境流向接近平衡"


def _volume_label(score: int) -> str:
    if score >= 70:
        return "资本流量偏充裕"
    if score >= 50:
        return "资本流量中性偏宽"
    return "资本流量偏弱"


def _speed_label(score: int) -> str:
    if score >= 65:
        return "资本流速/实体传导较快"
    if score >= 45:
        return "资本流速/实体传导中性"
    return "资本流速偏慢，金融与实体分化"


def _transmission_label(score: int) -> str:
    if score >= 65:
        return "金融资本向实体需求与利润传导顺畅"
    if score >= 45:
        return "实体传导中性，结构分化"
    return "实体传导受阻，资金供给未充分转成私人需求"


def _path_status(score: int) -> str:
    if score >= 65:
        return "畅通"
    if score >= 45:
        return "分化"
    return "阻滞"


def _capital_flow_paths(
    get: Any,
    *,
    direction_score: int,
    volume_score: int,
    transmission_score: int,
    speed_score: int,
) -> tuple[CapitalFlowPath, ...]:
    policy_allocation = _clamp(
        50
        + max(0.0, get("government_bond_financing") - 4) * 5
        - max(0.0, get("tsf_stock_yoy") - get("rmb_loan_yoy")) * 4
    )
    private_allocation = _clamp(
        50 + get("private_investment_yoy") * 3 + get("retail_sales_yoy") * 2
    )
    price_pass_through = _clamp(
        55
        - max(0.0, get("producer_purchase_price_yoy") - get("ppi_yoy")) * 5
        - max(0.0, get("ppi_yoy") - get("cpi_yoy")) * 3
    )
    return (
        CapitalFlowPath(
            "金融体系资本供给",
            "流量",
            volume_score,
            _path_status(volume_score),
            "M2、社融、人民币贷款",
            "银行与资本市场融资",
            "政府、企业与居民资产负债表",
            (
                f"M2 {get('m2_yoy'):.1f}%",
                f"社融 {get('tsf_stock_yoy'):.1f}%",
                f"贷款 {get('rmb_loan_yoy'):.1f}%",
            ),
            "总量不弱，但需要继续检查资金流向和实体承接能力。",
        ),
        CapitalFlowPath(
            "跨境资本方向",
            "流向",
            _clamp(50 + direction_score / 2),
            "净流入" if direction_score >= 35 else "净流出" if direction_score <= -35 else "平衡",
            "银行结售汇、涉外收付款",
            "跨境结算与外汇市场",
            "境内人民币资产与外汇流动性",
            (
                f"结售汇净额 {get('bank_fx_settlement') - get('bank_fx_sales'):.0f} 亿元",
                "涉外收付款净额 "
                f"{get('cross_border_receipts') - get('cross_border_payments'):.0f} 亿元",
            ),
            "净流入改善外部流动性，但不能单独证明资金已进入股票或实体投资。",
        ),
        CapitalFlowPath(
            "政策融资配置",
            "流向",
            policy_allocation,
            _path_status(policy_allocation),
            "政府债券与社融结构",
            "财政融资、政策项目与银行信用",
            "公共部门和政策支持行业",
            (
                f"政府债券净融资 {get('government_bond_financing'):.2f} 万亿元",
                f"社融与贷款增速差 {get('tsf_stock_yoy') - get('rmb_loan_yoy'):+.1f} 个百分点",
            ),
            "政策资金占优时，关注项目现金流和财政回款，而非把宽货币等同于普涨。",
        ),
        CapitalFlowPath(
            "私人部门实体承接",
            "传导",
            private_allocation,
            _path_status(private_allocation),
            "企业与居民可得资金",
            "民间投资、工资/财产收入、消费",
            "民企扩产、居民消费与就业",
            (
                f"民间投资 {get('private_investment_yoy'):.1f}%",
                f"社零 {get('retail_sales_yoy'):.1f}%",
                f"财产净收入 {get('property_income_yoy'):.1f}%",
            ),
            "私人需求偏弱会让金融供给停留在资产和公共部门循环。",
        ),
        CapitalFlowPath(
            "货币活化与周转",
            "流速",
            speed_score,
            _path_status(speed_score),
            "M2 资金存量",
            "M1 活化、投资和消费周转",
            "实体交易与企业现金流",
            (
                f"M1/M2 增速 {get('m1_yoy'):.1f}% / {get('m2_yoy'):.1f}%",
                f"固定投资/社零 {get('fixed_asset_yoy'):.1f}% / {get('retail_sales_yoy'):.1f}%",
            ),
            "M1 明显慢于 M2 且内需偏弱，表示资本周转速度仍受约束。",
        ),
        CapitalFlowPath(
            "成本—售价—利润传递",
            "传导",
            min(transmission_score, price_pass_through),
            _path_status(price_pass_through),
            "原材料和购进成本",
            "PPI 到 CPI 与企业利润",
            "下游企业、居民与不同所有制企业",
            (
                f"购进价/PPI/CPI {get('producer_purchase_price_yoy'):.1f}% / "
                f"{get('ppi_yoy'):.1f}% / {get('cpi_yoy'):.1f}%",
                f"工业/私营工业利润 {get('industrial_profit_yoy'):.1f}% / "
                f"{get('private_industrial_profit_yoy'):.1f}%",
            ),
            "购进成本快于出厂价、PPI 又快于 CPI，提示议价能力决定利润归属。",
        ),
    )


def _transfer_chains(get: Any) -> tuple[CostTransferChain, ...]:
    real_estate = _clamp(
        max(0.0, -get("real_estate_investment_yoy")) * 4
        + max(0.0, -get("land_sale_revenue_yoy")) * 2
    )
    fiscal = _clamp(
        35
        + max(
            0.0,
            get("government_fund_spending_yoy") - get("government_fund_revenue_yoy"),
        )
        + max(0.0, get("debt_interest_spending_yoy")) * 2
        + max(0.0, -get("agriculture_spending_yoy")) * 2
    )
    household = _clamp(
        35
        + max(0.0, get("wage_income_yoy") - get("property_income_yoy")) * 6
        + max(0.0, -get("private_investment_yoy")) * 8
    )
    external = _clamp(
        35 + max(0.0, 5 - get("trade_yoy")) * 6 + max(0.0, get("m2_yoy") - get("gdp_real_yoy")) * 4
    )
    input_cost = _clamp(
        35
        + max(0.0, get("producer_purchase_price_yoy") - get("ppi_yoy")) * 7
        + max(0.0, get("ppi_yoy") - get("cpi_yoy")) * 4
    )
    urban_rural = _clamp(
        45
        + max(0.0, -get("agriculture_spending_yoy")) * 3
        + max(0.0, get("urban_income_yoy") - get("rural_income_yoy")) * 5
        + max(0.0, get("cpi_yoy") - get("rural_cpi_yoy")) * 5
    )
    return (
        CostTransferChain(
            "地产—土地财政链",
            real_estate,
            "地产资产负债表收缩和土地收入下降",
            "销售/投资下降 → 土地财政与上下游现金流承压",
            "高杠杆房企、地方财政、建筑建材链和相关家庭资产负债表",
            "低杠杆存量运营商、现金充裕收并购方与非地产财政依赖地区",
            "压低高杠杆地产链估值，并提高稳定现金流资产相对吸引力",
            (
                f"房地产开发投资同比 {get('real_estate_investment_yoy'):.1f}%",
                f"土地出让收入同比 {get('land_sale_revenue_yoy'):.1f}%",
            ),
            "销售回款、土地收入、开发投资与经营现金流同步改善",
            "土地收入和地产销售连续两个统计期转正，且高杠杆主体债务压力下降",
        ),
        CostTransferChain(
            "财政—公共成本链",
            fiscal,
            "政府性基金收入走弱、支出扩张与债务付息",
            "收入缺口/债务成本 → 支出结构调整与公共预算压力",
            "地方公共服务、涉农支出承接者和依赖政府回款的企业",
            "中央财政支持方向、低应收高现金流的公共服务运营商",
            "回避应收账款高、强依赖地方回款的弱现金流公司",
            (
                f"政府性基金收入/支出同比 {get('government_fund_revenue_yoy'):.1f}% / "
                f"{get('government_fund_spending_yoy'):.1f}%",
                f"农林水支出/债务付息同比 {get('agriculture_spending_yoy'):.1f}% / "
                f"{get('debt_interest_spending_yoy'):.1f}%",
            ),
            "政府基金收入、涉农支出和企业应收周转同步确认",
            "财政收入恢复快于付息压力，且拖欠账款和应收周转持续改善",
        ),
        CostTransferChain(
            "资本收益—劳动与民企链",
            household,
            "资产收益偏弱与民间投资收缩",
            "资产价格/融资分化 → 居民财富效应和民企扩张意愿下降",
            "中低收入家庭、融资弱势民企和需求弹性较大的消费行业",
            "融资可得性强、现金储备高和具有渠道议价权的龙头企业",
            "消费与小盘成长需要盈利和民间投资数据确认，不能只看流动性",
            (
                f"财产净收入/工资性收入同比 {get('property_income_yoy'):.1f}% / "
                f"{get('wage_income_yoy'):.1f}%",
                f"民间投资同比 {get('private_investment_yoy'):.1f}%",
            ),
            "民间投资、居民财产收入、社零和私营企业利润",
            "民间投资与财产收入连续改善，私人部门利润增速不再落后",
        ),
        CostTransferChain(
            "外部—国内实体链",
            external,
            "外部需求与金融条件变化",
            "全球金融/贸易波动 → 汇率、出口利润与国内稳增长成本",
            "低议价出口企业、资源环境承载地区与就业部门",
            "全球定价权、自然对冲和高技术壁垒的出口企业",
            "优先具有全球定价权、技术壁垒和汇率风险管理能力的企业",
            (
                f"货物进出口同比 {get('trade_yoy'):.1f}%",
                f"M2/实际 GDP 同比 {get('m2_yoy'):.1f}% / {get('gdp_real_yoy'):.1f}%",
            ),
            "结售汇、出口价格、汇率和出口企业利润率",
            "外需、汇率与出口利润同步稳定，且资源环境成本不再向弱势地区集中",
        ),
        CostTransferChain(
            "上游购进成本—下游利润链",
            input_cost,
            "原材料和能源购进价格快于工业出厂价",
            "购进价上涨 → 出厂价传导不全 → 毛利压力向下游和消费者分配",
            "议价能力弱的中下游制造、生活资料企业及部分消费者",
            "拥有资源品、技术壁垒、品牌提价权或长协成本优势的企业",
            "同一行业内优先筛选能把成本转成售价与现金流的公司",
            (
                f"购进价格/PPI 同比 {get('producer_purchase_price_yoy'):.1f}% / "
                f"{get('ppi_yoy'):.1f}%",
                f"PPI/CPI 同比 {get('ppi_yoy'):.1f}% / {get('cpi_yoy'):.1f}%",
            ),
            "毛利率、存货、应付/应收周转与终端提价",
            "购进价与出厂价剪刀差收窄，且下游毛利和销量同时修复",
        ),
        CostTransferChain(
            "城市资本化—乡村要素链",
            urban_rural,
            "工业化和城市资本集中需要土地、劳动力、储蓄及公共资源",
            "要素净流出/财政支出收缩 → 农村社会承担资本化与周期调整成本",
            "农村土地与劳动力提供者、县域公共服务和涉农产业链弱势主体",
            "乡村收入增长受益行业、县域消费和农业效率提升企业",
            "不能只看城乡收入增速差，应同时检查涉农财政、消费价格和公共服务",
            (
                f"农林水支出同比 {get('agriculture_spending_yoy'):.1f}%",
                f"农村/城镇收入同比 {get('rural_income_yoy'):.1f}% / "
                f"{get('urban_income_yoy'):.1f}%",
                f"农村/全国 CPI {get('rural_cpi_yoy'):.1f}% / {get('cpi_yoy'):.1f}%",
            ),
            "涉农财政、城乡收入中位数、县域消费和要素回流",
            "涉农公共投入恢复、县域内生投资增强且要素净流出压力下降",
        ),
    )


def _sector_views(
    get: Any, flow: CapitalFlowAssessment, transfer: CostTransferAssessment
) -> tuple[SectorView, ...]:
    manufacturing_confidence = _clamp(
        55 + (get("manufacturing_investment_yoy") - get("fixed_asset_yoy")) * 4
    )
    defensive_confidence = _clamp(50 + transfer.pressure_score * 0.35)
    property_risk = _clamp(55 + max(0.0, -get("real_estate_investment_yoy")) * 3)
    return (
        SectorView(
            "高端制造/工业自动化/电力设备",
            "overweight",
            "偏配",
            manufacturing_confidence,
            "制造业投资显著高于整体投资，资本流向具有结构性而非普涨特征。",
            "订单、产能利用率和企业盈利继续改善",
            "只有投资没有利润兑现，或海外需求快速走弱",
        ),
        SectorView(
            "公用事业/运营商/高股息现金流",
            "overweight",
            "偏配",
            defensive_confidence,
            "资本流速偏慢且成本转嫁压力高时，稳定现金流和低杠杆更占优。",
            "自由现金流和分红覆盖持续稳定",
            "无风险利率上行或监管导致盈利模式变化",
        ),
        SectorView(
            "券商/高弹性金融",
            "neutral",
            "观察",
            _clamp(45 + flow.volume_score * 0.25 + flow.direction_score * 0.15),
            "流量与跨境方向正向，但实体传导慢，尚不足以直接推导全面风险偏好上行。",
            "成交活跃、融资需求与盈利预期同步改善",
            "流动性停留在金融体系内且市场成交回落",
        ),
        SectorView(
            "房地产开发/高杠杆建筑建材",
            "underweight",
            "回避/低配",
            property_risk,
            "地产投资和土地收入同步收缩，是当前最清晰的成本承接链。",
            "销售、土地收入和经营现金流连续改善后再评估",
            "强政策托底可能带来阶段性反弹，低配不等于单边做空",
        ),
        SectorView(
            "弱现金流小盘消费与地方回款依赖行业",
            "underweight",
            "谨慎/低配",
            _clamp(45 + transfer.pressure_score * 0.45),
            "民间投资、财产收入和财政回款压力可能向企业与居民部门传递。",
            "民间投资、居民财产收入和应收账款周转同时改善",
            "政策补贴或需求刺激可能改变压力传导",
        ),
    )


_ALLOCATION_ORDER = (
    "cash",
    "rmb_fixed_income",
    "domestic_broad",
    "dividend_quality",
    "advanced_manufacturing",
    "global_equity",
    "gold",
)

_ALLOCATION_BASES: dict[str, dict[str, int]] = {
    "conservative": {
        "cash": 20,
        "rmb_fixed_income": 50,
        "domestic_broad": 10,
        "dividend_quality": 8,
        "advanced_manufacturing": 2,
        "global_equity": 3,
        "gold": 7,
    },
    "balanced": {
        "cash": 10,
        "rmb_fixed_income": 30,
        "domestic_broad": 22,
        "dividend_quality": 12,
        "advanced_manufacturing": 8,
        "global_equity": 10,
        "gold": 8,
    },
    "growth": {
        "cash": 5,
        "rmb_fixed_income": 15,
        "domestic_broad": 30,
        "dividend_quality": 10,
        "advanced_manufacturing": 18,
        "global_equity": 14,
        "gold": 8,
    },
}

_ALLOCATION_META: dict[str, tuple[str, str, str, str]] = {
    "cash": (
        "现金管理",
        "cash",
        "银行活期、货币基金或短期存款；只放可投资资金，不把高息网贷当现金管理",
        "提供分批买入和短期支出的缓冲，避免下跌时被迫卖出",
    ),
    "rmb_fixed_income": (
        "人民币固收",
        "fixed_income",
        "国债、政策性金融债、中短债或高等级信用债指数工具",
        "降低组合波动并提供相对稳定的票息来源",
    ),
    "domestic_broad": (
        "A股宽基",
        "equity",
        "覆盖沪深市场的低费率宽基指数基金或 ETF 联接，不用单只股票替代核心仓",
        "获取中国企业长期盈利增长，是权益核心而不是择时卫星仓",
    ),
    "dividend_quality": (
        "红利质量",
        "equity",
        "分散的红利、质量或低波指数工具，核对行业集中度和历史分红覆盖",
        "在流速偏慢、成本压力偏高阶段强化现金流和资产负债表质量",
    ),
    "advanced_manufacturing": (
        "结构制造",
        "equity",
        "制造业、工业自动化或电力设备等分散行业指数；只作为卫星仓",
        "承接制造业资本开支的结构机会，同时限制主题集中风险",
    ),
    "global_equity": (
        "全球权益",
        "equity",
        "跨区域宽基指数基金或合规 QDII；买入前检查额度、跟踪误差和场内溢价",
        "分散单一经济体、行业和人民币资产周期风险",
    ),
    "gold": (
        "黄金",
        "alternative",
        "黄金 ETF、ETF 联接或透明低费率积存金，不把珠宝首饰当投资仓位",
        "对冲货币、地缘与尾部风险，但不提供利息或企业现金流",
    ),
}

_ALLOCATION_RISKS = {
    "cash": "长期购买力可能被通胀侵蚀",
    "rmb_fixed_income": "利率上升会造成净值波动，信用债还存在违约风险",
    "domestic_broad": "权益熊市中可能出现较大回撤，不能使用三年内必用资金",
    "dividend_quality": "可能集中在金融、能源等行业，高股息不等于低风险",
    "advanced_manufacturing": "行业估值、订单和海外需求变化可能造成高波动",
    "global_equity": "存在市场、汇率、额度和溢价风险",
    "gold": "价格波动大且没有经营现金流，不能替代现金和债券",
}


def _rounded_allocation(raw: dict[str, float]) -> dict[str, int]:
    positive = {key: max(0.0, raw[key]) for key in _ALLOCATION_ORDER}
    total = sum(positive.values())
    if total <= 0:
        raise ValueError("allocation total must be positive")
    normalized = {key: value * 100 / total for key, value in positive.items()}
    rounded = {key: int(value) for key, value in normalized.items()}
    remainder = 100 - sum(rounded.values())
    ranked = sorted(
        _ALLOCATION_ORDER,
        key=lambda key: (normalized[key] - rounded[key], -_ALLOCATION_ORDER.index(key)),
        reverse=True,
    )
    for key in ranked[:remainder]:
        rounded[key] += 1
    return rounded


def _allocation_deltas(
    flow: CapitalFlowAssessment,
    transfer: CostTransferAssessment,
    sectors: tuple[SectorView, ...],
    risk_appetite: int,
) -> dict[str, float]:
    deltas = {key: 0.0 for key in _ALLOCATION_ORDER}
    defensive = min(1.0, max(0.0, (45 - risk_appetite) / 20))
    offensive = min(1.0, max(0.0, (risk_appetite - 65) / 20))
    defensive_tilt = {
        "cash": 4,
        "rmb_fixed_income": 6,
        "domestic_broad": -7,
        "dividend_quality": 2,
        "advanced_manufacturing": -3,
        "global_equity": -4,
        "gold": 2,
    }
    offensive_tilt = {
        "cash": -3,
        "rmb_fixed_income": -7,
        "domestic_broad": 6,
        "dividend_quality": -1,
        "advanced_manufacturing": 4,
        "global_equity": 3,
        "gold": -2,
    }
    for key in _ALLOCATION_ORDER:
        deltas[key] += defensive_tilt[key] * defensive
        deltas[key] += offensive_tilt[key] * offensive

    sector_map = {sector.sector: sector for sector in sectors}
    manufacturing = sector_map["高端制造/工业自动化/电力设备"]
    if manufacturing.stance == "overweight":
        deltas["advanced_manufacturing"] += 2
        deltas["domestic_broad"] -= 1
        deltas["rmb_fixed_income"] -= 1
    dividend = sector_map["公用事业/运营商/高股息现金流"]
    if dividend.stance == "overweight":
        deltas["dividend_quality"] += 2
        deltas["cash"] -= 1
        deltas["rmb_fixed_income"] -= 1
    if flow.direction_score >= 35:
        deltas["domestic_broad"] += 1
        deltas["cash"] -= 1
    elif flow.direction_score <= -35:
        deltas["domestic_broad"] -= 1
        deltas["cash"] += 1
    if transfer.pressure_score >= 60:
        deltas["gold"] += 1
        deltas["domestic_broad"] -= 1
    return deltas


def _target_rationale(
    key: str,
    *,
    flow: CapitalFlowAssessment,
    transfer: CostTransferAssessment,
    risk_appetite: int,
) -> str:
    transmission_state = (
        "顺畅"
        if flow.transmission_score >= 65
        else "中性分化"
        if flow.transmission_score >= 45
        else "偏弱"
    )
    risk_state = "进攻" if risk_appetite >= 65 else "中性" if risk_appetite >= 45 else "防守"
    rationales = {
        "cash": f"权益风险偏好 {risk_appetite}/100，保留再平衡弹药而非一次押满",
        "rmb_fixed_income": (
            f"资本流速 {flow.speed_score}/100、实体传导 "
            f"{flow.transmission_score}/100，固收承担稳定器"
        ),
        "domestic_broad": (
            f"保留长期权益核心；当前实体传导{transmission_state}，按规则控制战术偏移"
        ),
        "dividend_quality": (
            f"成本转嫁压力 {transfer.pressure_score}/100，优先现金流和资产负债表质量"
        ),
        "advanced_manufacturing": "制造业投资相对占优，保留结构仓但不替代宽基核心",
        "global_equity": f"用于地域分散；当前为{risk_state}阶段，仍需避免高溢价追涨",
        "gold": f"成本转嫁压力 {transfer.pressure_score}/100，仅配置为尾部风险缓冲",
    }
    return rationales[key]


def _allocation_plans(
    flow: CapitalFlowAssessment,
    transfer: CostTransferAssessment,
    sectors: tuple[SectorView, ...],
    risk_appetite: int,
) -> tuple[LongTermAllocationPlan, ...]:
    deltas = _allocation_deltas(flow, transfer, sectors, risk_appetite)
    profiles = (
        (
            "conservative",
            "保守防守",
            "更看重本金波动控制，不能接受权益仓短期大幅回撤",
            "至少 5 年",
            "仍可能出现约 10%–15% 的阶段回撤",
            (50, 20, 15, 15),
        ),
        (
            "balanced",
            "稳健平衡",
            "有稳定现金流，可接受中等波动，希望兼顾增长与抗风险",
            "至少 7–10 年",
            "应能承受约 20% 左右、极端时期更高的阶段回撤",
            (40, 20, 20, 20),
        ),
        (
            "growth",
            "成长进取",
            "收入稳定、期限很长，并能承受权益资产的大幅波动",
            "至少 10 年",
            "必须能承受 30% 以上的阶段回撤且不依赖杠杆",
            (30, 25, 25, 20),
        ),
    )
    plans: list[LongTermAllocationPlan] = []
    bands = {
        "cash": 3,
        "rmb_fixed_income": 5,
        "domestic_broad": 5,
        "dividend_quality": 3,
        "advanced_manufacturing": 3,
        "global_equity": 3,
        "gold": 3,
    }
    for profile, label, suitability, horizon, drawdown, tranches in profiles:
        strategic = _ALLOCATION_BASES[profile]
        target = _rounded_allocation(
            {key: strategic[key] + deltas[key] for key in _ALLOCATION_ORDER}
        )
        targets: list[AssetAllocationTarget] = []
        for key in _ALLOCATION_ORDER:
            name, asset_class, vehicles, purpose = _ALLOCATION_META[key]
            difference = target[key] - strategic[key]
            action = "increase" if difference >= 2 else "reduce" if difference <= -2 else "hold"
            action_label = (
                f"增配 +{difference}pct"
                if action == "increase"
                else f"减配 {abs(difference)}pct"
                if action == "reduce"
                else "维持战略仓"
            )
            band = bands[key]
            targets.append(
                AssetAllocationTarget(
                    key,
                    name,
                    asset_class,
                    strategic[key],
                    target[key],
                    max(0, target[key] - band),
                    min(100, target[key] + band),
                    action,
                    action_label,
                    vehicles,
                    purpose,
                    _target_rationale(
                        key,
                        flow=flow,
                        transfer=transfer,
                        risk_appetite=risk_appetite,
                    ),
                    _ALLOCATION_RISKS[key],
                )
            )
        steps = tuple(
            AllocationBuildStep(
                index,
                timing,
                percentage,
                instruction,
                gate,
            )
            for index, (timing, percentage, instruction, gate) in enumerate(
                (
                    (
                        "第 0 周",
                        tranches[0],
                        "按目标比例建立第一批；确保现金、固收和黄金不缺位，权益只买核心指数",
                        "已另留 6–12 个月应急金，且没有三年内确定要用的钱",
                    ),
                    (
                        "第 4 周",
                        tranches[1],
                        "用第二批补目标缺口，不因短期上涨临时提高权益比例",
                        "宏观数据未出现风险降级，所选指数不存在异常溢价",
                    ),
                    (
                        "第 8 周",
                        tranches[2],
                        "继续按当前实际仓位与目标仓位的差额投入",
                        "收入、负债和应急金状况没有恶化",
                    ),
                    (
                        "第 12 周",
                        tranches[3],
                        "完成目标组合；以后用新增资金优先补低于下限的资产",
                        "先更新宏观快照，再执行最后一批和首次再平衡",
                    ),
                ),
                start=1,
            )
        )
        plans.append(
            LongTermAllocationPlan(
                profile,
                label,
                suitability,
                horizon,
                drawdown,
                "先在组合之外留足 6–12 个月必要支出的应急金，偿还高息负债；"
                "输入金额只能是 3 年内不需要使用的可投资资金。",
                tuple(targets),
                steps,
                (
                    "每季度检查一次，不根据日内新闻或单次涨跌调整长期组合。",
                    "任一资产低于下限或高于上限时触发再平衡；优先用新增资金补仓，减少不必要卖出。",
                    "同一宏观方向至少连续两个统计期确认，才移动 5 个百分点；"
                    "单次最多调整 5 个百分点。",
                    "每年重新核对投资期限、收入、负债和回撤承受力；个人条件变化优先于宏观判断。",
                ),
                (
                    "资本流速与实体传导连续两个统计期均不低于 45，成本压力低于 60，"
                    "风险偏好不低于 45：从现金/固收转 5pct 到宽基与结构制造。",
                    "风险偏好达到 65 且民间投资、私人利润和成交同步确认："
                    "再转 5pct，仍不突破资产上限。",
                ),
                (
                    "跨境方向不高于 -35，或资本流量低于 40，且流速/传导继续低于 35："
                    "权益合计减 5pct，转入现金与高等级固收。",
                    "成本压力不低于 75 且私人部门传导低于 35："
                    "减少弱现金流权益，黄金最多增至目标上限。",
                ),
                (
                    "不用融资、期权或借款放大仓位。",
                    "个股只允许作为卫星仓：全部个股合计不超过组合 10%，单只不超过 3%。",
                    "不把行业主题、黄金或高股息产品当成无风险资产。",
                    "基金/ETF 只按资产类别举例；买入前自行核对费用、跟踪误差、流动性、溢价和税务。",
                ),
            )
        )
    return tuple(plans)


def analyze_macro_snapshot(snapshot: MacroSnapshot) -> MacroAnalysis:
    def get(code: str) -> float:
        return snapshot.metric(code).value

    settlement = get("bank_fx_settlement")
    sales = get("bank_fx_sales")
    receipts = get("cross_border_receipts")
    payments = get("cross_border_payments")
    fx_net = settlement - sales
    cross_border_net = receipts - payments
    direction_score = _clamp(
        fx_net / ((settlement + sales) / 2) * 250
        + cross_border_net / ((receipts + payments) / 2) * 250,
        -100,
        100,
    )
    money_growth = (get("m2_yoy") + get("tsf_stock_yoy") + get("rmb_loan_yoy")) / 3
    volume_score = _clamp(50 + (money_growth - 5) * 5)
    gdp = get("gdp_real_yoy")
    investment = get("fixed_asset_yoy")
    manufacturing = get("manufacturing_investment_yoy")
    transmission_score = _clamp(
        55
        - max(0.0, get("tsf_stock_yoy") - get("rmb_loan_yoy")) * 3
        - max(0.0, gdp - get("retail_sales_yoy")) * 3
        - max(0.0, -get("private_investment_yoy")) * 2
        + min(10.0, max(0.0, get("private_industrial_profit_yoy") - gdp))
    )
    speed_score = _clamp(
        50
        - max(0.0, get("m2_yoy") - get("m1_yoy")) * 4
        - max(0.0, gdp - get("retail_sales_yoy")) * 2
        - max(0.0, gdp - investment)
        + max(0.0, get("trade_yoy") - gdp) * 0.5
    )
    paths = _capital_flow_paths(
        get,
        direction_score=direction_score,
        volume_score=volume_score,
        transmission_score=transmission_score,
        speed_score=speed_score,
    )
    bottlenecks = tuple(
        path.investment_effect for path in paths if path.score < 45 or path.status == "阻滞"
    )
    flow = CapitalFlowAssessment(
        direction_score,
        _flow_label(direction_score),
        volume_score,
        _volume_label(volume_score),
        transmission_score,
        _transmission_label(transmission_score),
        speed_score,
        _speed_label(speed_score),
        round(fx_net, 2),
        round(cross_border_net, 2),
        (
            f"6 月银行结售汇净额 {fx_net:.0f} 亿元",
            f"6 月银行代客涉外收付款净额 {cross_border_net:.0f} 亿元",
            f"M2/社融存量/人民币贷款同比 {get('m2_yoy'):.1f}% / "
            f"{get('tsf_stock_yoy'):.1f}% / {get('rmb_loan_yoy'):.1f}%",
            f"M1/M2 同比 {get('m1_yoy'):.1f}% / {get('m2_yoy'):.1f}%",
            f"实际 GDP/固定资产/制造业投资同比 {gdp:.1f}% / "
            f"{investment:.1f}% / {manufacturing:.1f}%",
        ),
        (
            f"制造业投资 {manufacturing:.1f}% 高于整体固定资产投资 {investment:.1f}%",
            f"民间投资 {get('private_investment_yoy'):.1f}% 显示资金获得与扩张意愿分化",
            f"政府债券净融资 {get('government_bond_financing'):.2f} 万亿元，政策融资占比较高",
        ),
        paths,
        bottlenecks,
    )

    pressure = (
        min(25.0, max(0.0, -get("real_estate_investment_yoy")) * 2)
        + min(15.0, max(0.0, -get("land_sale_revenue_yoy")) * 1.5)
        + min(15.0, max(0.0, -get("agriculture_spending_yoy")) * 1.5)
        + min(
            15.0,
            max(0.0, get("government_fund_spending_yoy") - get("government_fund_revenue_yoy"))
            * 0.5,
        )
        + min(10.0, max(0.0, -get("private_investment_yoy")) * 2)
        + min(10.0, max(0.0, get("wage_income_yoy") - get("property_income_yoy")) * 2)
        + min(
            15.0,
            max(0.0, get("producer_purchase_price_yoy") - get("cpi_yoy")) * 2,
        )
        + min(
            10.0,
            max(
                0.0,
                get("industrial_profit_yoy") - get("private_industrial_profit_yoy"),
            ),
        )
        - min(10.0, max(0.0, get("rural_income_yoy") - get("urban_income_yoy")) * 5)
    )
    pressure_score = _clamp(pressure)
    pressure_label = (
        "成本转嫁压力偏高"
        if pressure_score >= 60
        else "成本转嫁压力中等"
        if pressure_score >= 35
        else "成本转嫁压力较低"
    )
    transfer = CostTransferAssessment(
        pressure_score,
        pressure_label,
        (
            f"房地产开发投资同比 {get('real_estate_investment_yoy'):.1f}%",
            f"土地出让收入同比 {get('land_sale_revenue_yoy'):.1f}%",
            f"政府性基金收入/支出同比 {get('government_fund_revenue_yoy'):.1f}% / "
            f"{get('government_fund_spending_yoy'):.1f}%",
            f"农林水财政支出同比 {get('agriculture_spending_yoy'):.1f}%",
            f"民间投资同比 {get('private_investment_yoy'):.1f}%",
            f"财产净收入/工资性收入同比 {get('property_income_yoy'):.1f}% / "
            f"{get('wage_income_yoy'):.1f}%",
            f"工业购进价/PPI/CPI 同比 {get('producer_purchase_price_yoy'):.1f}% / "
            f"{get('ppi_yoy'):.1f}% / {get('cpi_yoy'):.1f}%",
        ),
        (
            f"农村居民收入同比 {get('rural_income_yoy'):.1f}%，高于城镇的 "
            f"{get('urban_income_yoy'):.1f}%",
            f"制造业投资同比 {manufacturing:.1f}%，高于整体固定资产投资",
        ),
        _transfer_chains(get),
    )
    risk_appetite = _clamp(
        45
        + flow.direction_score * 0.2
        + (flow.volume_score - 50) * 0.3
        + (flow.speed_score - 50) * 0.35
        - transfer.pressure_score * 0.2
    )
    risk_label = "进攻" if risk_appetite >= 65 else "中性" if risk_appetite >= 45 else "防守"
    sectors = _sector_views(get, flow, transfer)
    allocation_plans = _allocation_plans(flow, transfer, sectors, risk_appetite)
    investment_view = MacroInvestmentView(
        risk_appetite,
        risk_label,
        "权益风险预算中性偏低；新增风险优先给强现金流和制造业景气确认方向",
        ("大盘/质量优于纯小盘", "现金流优于高杠杆", "结构性制造优于地产链普涨"),
        sectors,
        (
            f"资本三流：{flow.volume_label}，{flow.direction_label}，{flow.speed_label}；"
            f"实体传导为“{flow.transmission_label}”。",
            f"温铁军成本转嫁视角：{transfer.pressure_label}，压力主要由地产—土地财政和财政—公共成本链承接。",
            "操作含义：偏配高端制造与稳定现金流，低配高杠杆地产链；周期性金融等待成交和盈利确认。",
        ),
        "balanced",
        allocation_plans,
    )
    regime = (
        f"{flow.volume_label}；{flow.direction_label}；{flow.speed_label}；"
        f"{transfer.pressure_label}"
    )
    return MacroAnalysis(
        "macro-three-flows-cost-transfer-2026.08.4",
        snapshot,
        flow,
        transfer,
        investment_view,
        regime,
        investment_view.decision_summary,
        (
            "资本流量、资本流向、资本流速、实体传导分别计算；资本流速使用 M1/M2、"
            "GDP、投资和消费增速差作代理，不等同于严格的货币流通速度。",
            "成本转嫁链是求衡基于作者理论的工程化假设，用于发现承压主体，"
            "不是作者本人发布的计量公式。",
            "行业偏配/低配是宏观情景建议，仍需与个股盈利、估值及 MACD/WR 时机交叉验证。",
            "指标频率不同，必须以每行统计期而非采集截止日判断可比性；"
            "分析统计期之后的市场前应重新联网刷新同口径数据。",
            "系统不保证收益、不连接券商、不自动下单。",
        ),
    )


def build_macro_report(analysis: MacroAnalysis) -> str:
    flow = analysis.capital_flow
    transfer = analysis.cost_transfer
    view = analysis.investment_view
    lines = [
        "# 宏观投资结论",
        f"- 数据基线/截止：{analysis.snapshot.label} / {analysis.snapshot.as_of.isoformat()}",
        f"- 综合状态：{analysis.regime}",
        f"- 风险偏好：{view.risk_appetite_label}（{view.risk_appetite_score}/100）",
        f"- 权益建议：{view.equity_exposure}",
    ]
    lines.extend(f"- {item}" for item in view.decision_summary)
    default_plan = next(
        plan for plan in view.allocation_plans if plan.profile == view.default_allocation_profile
    )
    lines.extend(
        (
            "",
            "## 长期资产配置执行方案",
            f"- 默认画像：{default_plan.label}；期限：{default_plan.horizon}；"
            f"回撤承受提示：{default_plan.drawdown_tolerance}",
            f"- 前置条件：{default_plan.prerequisite}",
            f"- 权益战略中枢/当前目标：{default_plan.equity_strategic_pct}% / "
            f"{default_plan.equity_target_pct}%",
        )
    )
    for target in default_plan.targets:
        lines.append(
            f"- {target.label}：战略 {target.strategic_pct}% → 当前目标 {target.target_pct}% "
            f"（允许 {target.minimum_pct}%–{target.maximum_pct}%；{target.action_label}）"
        )
        lines.append(f"  实现：{target.vehicles}")
        lines.append(f"  宏观依据：{target.macro_rationale}")
    lines.append("- 四批建仓：")
    for step in default_plan.build_steps:
        lines.append(
            f"  第 {step.order} 批 / {step.timing} / 总资金 {step.portfolio_pct}%："
            f"{step.instruction}；门槛：{step.gate}"
        )
    lines.append("- 再平衡规则：")
    lines.extend(f"  {index}. {rule}" for index, rule in enumerate(default_plan.rebalance_rules, 1))
    lines.append("- 提高风险仓位的触发条件：")
    lines.extend(f"  - {item}" for item in default_plan.increase_risk_triggers)
    lines.append("- 降低风险仓位的触发条件：")
    lines.extend(f"  - {item}" for item in default_plan.decrease_risk_triggers)
    lines.append("- 组合护栏：")
    lines.extend(f"  - {item}" for item in default_plan.guardrails)
    lines.extend(("", "## 卢麒元资本三流：流量、流向、流速"))
    lines.extend(
        (
            f"- 流量：{flow.volume_label}（{flow.volume_score}/100）",
            f"- 流向：{flow.direction_label}（{flow.direction_score:+d}）",
            f"- 流速：{flow.speed_label}（{flow.speed_score}/100）",
            f"- 实体传导：{flow.transmission_label}（{flow.transmission_score}/100）",
            "- 口径：资本流量、资本流向、资本流速、实体传导分别计算，"
            "避免把‘钱多’直接等同于‘实体景气’。",
        )
    )
    lines.extend(f"- 证据：{item}" for item in flow.evidence)
    lines.extend(f"- 结构流向：{item}" for item in flow.allocation_evidence)
    for path in flow.paths:
        lines.append(
            f"- 路径[{path.dimension}/{path.status}]：{path.source} → {path.channel} → "
            f"{path.destination}（{path.score}/100）"
        )
    lines.extend(("", "## 温铁军代价/成本转嫁链"))
    lines.append(f"- 总体：{transfer.pressure_label}（{transfer.pressure_score}/100）")
    for chain in transfer.chains:
        lines.extend(
            (
                f"- {chain.name}（{chain.pressure_score}/100）",
                f"  来源：{chain.source}",
                f"  通道：{chain.channel}",
                f"  承接者：{chain.bearer}",
                f"  相对受益者：{chain.beneficiary}",
                f"  投资含义：{chain.investment_effect}",
                f"  证据：{'；'.join(chain.evidence)}",
                f"  验证指标：{chain.confirmation}",
                f"  反转条件：{chain.reversal_conditions}",
            )
        )
    lines.extend(("", "## 行业配置建议"))
    for sector in view.sectors:
        lines.extend(
            (
                f"- {sector.sector}：{sector.stance_label}（置信度 {sector.confidence}/100）",
                f"  逻辑：{sector.rationale}",
                f"  确认：{sector.confirmation}",
                f"  风险：{sector.risk}",
            )
        )
    lines.extend(("", "## 方法边界"))
    lines.extend(f"- {item}" for item in analysis.limitations)
    lines.extend(("", "## 数据警告"))
    lines.extend(f"- {item}" for item in analysis.snapshot.warnings)
    return "\n".join(lines)
