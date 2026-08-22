from __future__ import annotations

import json
from copy import deepcopy
from datetime import date

import httpx
import pytest

from aegisrun.macro.analysis import (
    analyze_macro_snapshot,
    build_macro_overlay,
    build_macro_report,
)
from aegisrun.macro.freshness import OfficialMacroFreshnessVerifier
from aegisrun.macro.models import MacroSnapshot
from aegisrun.macro.pipeline import macro_research_plan, run_macro_research
from aegisrun.macro.providers import BundledOfficialMacroProvider, JsonMacroProvider
from aegisrun.research.guardrails import InvestmentOutputGuard


def test_official_macro_baseline_has_auditable_sources_and_transparent_scores() -> None:
    snapshot = BundledOfficialMacroProvider().load()
    analysis = analyze_macro_snapshot(snapshot)
    report = build_macro_report(analysis)

    assert snapshot.as_of.isoformat() == "2026-06-30"
    assert len(snapshot.metrics) == 32
    assert all(metric.source_url.startswith("https://") for metric in snapshot.metrics)
    assert analysis.capital_flow.fx_settlement_net_100m == 3862
    assert analysis.capital_flow.cross_border_net_receipts_100m == 2531
    assert analysis.capital_flow.direction_score == 63
    assert analysis.capital_flow.volume_score == 59
    assert analysis.capital_flow.transmission_score != analysis.capital_flow.speed_score
    assert len(analysis.capital_flow.paths) >= 5
    assert analysis.capital_flow.bottlenecks
    assert 0 <= analysis.cost_transfer.pressure_score <= 100
    assert analysis.investment_view.risk_appetite_label == "防守"
    assert len(analysis.cost_transfer.chains) >= 6
    assert all(
        chain.beneficiary and chain.reversal_conditions for chain in analysis.cost_transfer.chains
    )
    assert len(analysis.investment_view.sectors) == 5
    assert {item.stance for item in analysis.investment_view.sectors} == {
        "overweight",
        "neutral",
        "underweight",
    }
    assert analysis.investment_view.default_allocation_profile == "balanced"
    plans = analysis.investment_view.allocation_plans
    assert [plan.profile for plan in plans] == ["conservative", "balanced", "growth"]
    assert [plan.equity_target_pct for plan in plans] == sorted(
        plan.equity_target_pct for plan in plans
    )
    for plan in plans:
        assert sum(target.strategic_pct for target in plan.targets) == 100
        assert sum(target.target_pct for target in plan.targets) == 100
        assert all(
            target.minimum_pct <= target.target_pct <= target.maximum_pct for target in plan.targets
        )
        assert sum(step.portfolio_pct for step in plan.build_steps) == 100
        assert len(plan.targets) == 7
        assert len(plan.rebalance_rules) >= 4
        assert len(plan.guardrails) >= 4
        assert "应急" in plan.prerequisite
        assert all("减配 -" not in target.action_label for target in plan.targets)
    balanced = next(plan for plan in plans if plan.profile == "balanced")
    balanced_targets = {target.key: target for target in balanced.targets}
    assert (
        balanced_targets["domestic_broad"].target_pct
        < balanced_targets["domestic_broad"].strategic_pct
    )
    assert (
        balanced_targets["dividend_quality"].target_pct
        > balanced_targets["dividend_quality"].strategic_pct
    )
    assert balanced_targets["gold"].target_pct > balanced_targets["gold"].strategic_pct
    assert balanced.equity_target_pct < balanced.equity_strategic_pct
    assert "不是理论作者发布的计量公式" in report
    assert "不等同于严格的货币流通速度" in report
    assert "行业配置建议" in report
    assert "卢麒元资本三流" in report
    assert "温铁军代价/成本转嫁链" in report
    assert report.startswith("# 宏观投资结论\n")
    assert "\n## 长期资产配置执行方案\n" in report
    assert "\n## 行业配置建议\n" in report
    assert "资本流量、资本流向、资本流速、实体传导分别计算" in report
    assert "长期资产配置执行方案" in report
    assert "四批建仓" in report
    assert "再平衡规则" in report
    InvestmentOutputGuard().ensure_safe(report)


def test_allocation_remains_non_negative_and_normalized_under_stress_snapshot() -> None:
    payload = deepcopy(BundledOfficialMacroProvider().load().to_dict())
    stressed = {
        "bank_fx_settlement": 1_000,
        "bank_fx_sales": 2_000,
        "cross_border_receipts": 1_000,
        "cross_border_payments": 2_000,
        "m1_yoy": -5,
        "m2_yoy": 2,
        "tsf_stock_yoy": 2,
        "rmb_loan_yoy": 2,
        "retail_sales_yoy": -5,
        "private_investment_yoy": -20,
        "private_industrial_profit_yoy": -10,
    }
    for metric in payload["metrics"]:
        if metric["code"] in stressed:
            metric["value"] = stressed[metric["code"]]

    analysis = analyze_macro_snapshot(MacroSnapshot.from_dict(payload))

    assert analysis.investment_view.risk_appetite_label == "防守"
    for plan in analysis.investment_view.allocation_plans:
        assert sum(target.target_pct for target in plan.targets) == 100
        assert all(0 <= target.target_pct <= 100 for target in plan.targets)
        assert plan.equity_target_pct <= plan.equity_strategic_pct


def test_macro_overlay_maps_industry_without_pretending_to_identify_unknown_stocks() -> None:
    analysis = analyze_macro_snapshot(BundledOfficialMacroProvider().load())

    manufacturing = build_macro_overlay("工业自动化", analysis)
    property_chain = build_macro_overlay("房地产开发", analysis)
    unknown = build_macro_overlay("未分类", analysis)

    assert manufacturing.stance == "overweight"
    assert 0 < manufacturing.confidence_adjustment <= 8
    assert property_chain.stance == "underweight"
    assert -12 <= property_chain.confidence_adjustment < 0
    assert unknown.stance == "unmapped"
    assert unknown.confidence_adjustment == 0


def test_macro_json_provider_round_trips_and_rejects_symlink(tmp_path) -> None:
    value = BundledOfficialMacroProvider().load().to_dict()
    path = tmp_path / "macro.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    loaded = JsonMacroProvider(path).load()

    assert loaded.version == value["version"]
    assert loaded.metric("m2_yoy").value == 8.0

    linked = tmp_path / "linked.json"
    linked.symlink_to(path)
    with pytest.raises(ValueError, match="普通文件"):
        JsonMacroProvider(linked).load()


def test_macro_plan_uses_specialized_parallel_subagents() -> None:
    plan = macro_research_plan("macro-plan-test")

    assert plan.tasks["capital_flow"].spec.depends_on == ("macro_data",)
    assert plan.tasks["cost_transfer"].spec.depends_on == ("macro_data",)
    assert plan.tasks["macro_synthesis"].spec.depends_on == (
        "freshness_gate",
        "capital_flow",
        "cost_transfer",
    )
    assert plan.tasks["freshness_gate"].spec.network_allowed is True
    assert plan.tasks["capital_flow"].spec.agent == "capital-flow-agent"
    assert plan.tasks["cost_transfer"].spec.skills == ("cost-transfer-lens",)


@pytest.mark.asyncio
async def test_macro_pipeline_persists_plan_events_and_agent_results(tmp_path) -> None:
    result = await run_macro_research(
        BundledOfficialMacroProvider(),
        workspace_root=tmp_path,
        run_id="macro-pipeline-test",
        verifier=OfficialMacroFreshnessVerifier(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="2026-08-17", request=request)
            )
        ),
        today=date(2026, 8, 21),
    )

    assert result.plan["status"] == "succeeded"
    assert result.workspace == str(tmp_path / "macro-pipeline-test")
    assert (tmp_path / "macro-pipeline-test" / ".state" / "events.jsonl").is_file()
    tasks = {task["id"]: task for task in result.plan["tasks"]}
    assert tasks["capital_flow"]["agent"] == "capital-flow-agent"
    assert tasks["cost_transfer"]["status"] == "succeeded"
    assert tasks["freshness_gate"]["result"]["status"] == "stale"
    assert tasks["guardrail"]["result"]["safe"] is True
    assert result.validity.current_decision_allowed is False
    assert "当前宏观投资结论不可用" in result.report
