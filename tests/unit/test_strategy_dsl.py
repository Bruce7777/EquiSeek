from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegisrun.portfolio.analysis import CandidateInput, rank_strategy_candidates
from aegisrun.portfolio.strategy_dsl import (
    CandidateStrategy,
    StrategyValidationError,
    candidate_strategy_from_skill,
)
from aegisrun.research.signals import (
    Direction,
    MultiTimeframeAnalysis,
    TimingAction,
    TimingDecision,
)
from aegisrun.skills import SkillValidationError, SkillWorkspace, SkillWorkspacePolicy


def analysis(score: int, action: TimingAction = TimingAction.ENTRY_WATCH) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        version="test",
        direction=Direction.BULLISH.value,
        direction_label=Direction.BULLISH.label,
        direction_score=5,
        regime="trend",
        macd={},
        wr={},
        risk_flags=(),
        timing=TimingDecision(action.value, action.label, 80, ("测试触发",)),
        candidate_score=score,
    )


def payload() -> dict[str, object]:
    return {
        "schema_version": "aegisrun-candidate-strategy/v1",
        "name": "测试策略",
        "filters": {
            "min_confidence": 60,
            "min_candidate_score": 50,
            "allowed_actions": ["buy"],
            "allowed_directions": ["bullish"],
            "require_buy_gate_open": False,
            "include_industries": [],
            "exclude_industries": ["房地产"],
            "exclude_symbols": ["000002.SZ"],
        },
        "ranking": {
            "mode": "weighted",
            "confidence_weight": 1,
            "candidate_score_weight": 1,
            "market_adjustment_weight": 0,
            "macro_adjustment_weight": 0,
            "preferred_industries": ["医药"],
            "preferred_industry_bonus": 10,
        },
        "max_results": 2,
    }


def test_declarative_strategy_filters_and_reorders_candidates() -> None:
    strategy = CandidateStrategy.from_dict(payload())
    ranked = rank_strategy_candidates(
        [
            CandidateInput("600000.SH", "浦发银行", analysis(72), industry="银行"),
            CandidateInput("600276.SH", "恒瑞医药", analysis(68), industry="创新医药"),
            CandidateInput("000002.SZ", "万科A", analysis(90), industry="房地产"),
            CandidateInput(
                "000001.SZ", "平安银行", analysis(80, TimingAction.WAIT), industry="银行"
            ),
        ],
        strategy,
    )

    assert [item.symbol for item in ranked] == ["600276.SH", "600000.SH"]
    assert ranked[0].strategy_score == 78.0
    assert ranked[0].strategy_name == "测试策略"


def write_skill(root: Path, strategy_payload: dict[str, object]) -> None:
    package = root / "my-filter"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\n"
        "name: my-filter\n"
        "description: 自定义候选筛选\n"
        "resources:\n"
        "  - strategy.json\n"
        "---\n"
        "按声明式策略筛选。\n",
        encoding="utf-8",
    )
    (package / "strategy.json").write_text(
        json.dumps(strategy_payload, ensure_ascii=False), encoding="utf-8"
    )


def test_user_skill_strategy_resource_is_loaded_and_auditable(tmp_path: Path) -> None:
    write_skill(tmp_path, payload())
    workspace = SkillWorkspace(
        SkillWorkspacePolicy(include_builtin=False, user_roots=(tmp_path,))
    )
    package = workspace.select_for_turn("/my-filter 筛选候选池").packages[0]

    strategy = candidate_strategy_from_skill(package)

    assert strategy.name == "测试策略"
    assert strategy.audit_dict()["filters"]["exclude_symbols"] == ["000002.SZ"]
    assert package.summary.provider == "user-1"


def test_builtin_investment_strategy_is_declared_and_parseable() -> None:
    workspace = SkillWorkspace(SkillWorkspacePolicy(user_roots=()))
    package = workspace.activate("investment-decision-engine")

    strategy = candidate_strategy_from_skill(package)

    assert "strategy.json" in package.summary.declared_resources
    assert strategy.name == "内置 MACD/WR 候选排序"
    assert strategy.max_results == 50


def test_builtin_strategy_preserves_legacy_order_and_platform_exit_filter() -> None:
    workspace = SkillWorkspace(SkillWorkspacePolicy(user_roots=()))
    strategy = candidate_strategy_from_skill(workspace.activate("investment-decision-engine"))
    items = [
        CandidateInput("000001.SZ", "平安银行", analysis(65, TimingAction.WAIT)),
        CandidateInput("600519.SH", "贵州茅台", analysis(88)),
        CandidateInput("000002.SZ", "万科A", analysis(99, TimingAction.EXIT_WATCH)),
    ]

    legacy = rank_strategy_candidates(items)
    configured = rank_strategy_candidates(items, strategy)

    assert [item.symbol for item in configured] == [item.symbol for item in legacy]
    assert "000002.SZ" not in {item.symbol for item in configured}


def test_strategy_applies_market_gate_industry_scope_and_result_limit() -> None:
    value = payload()
    filters = dict(value["filters"])  # type: ignore[arg-type]
    filters.update(
        {
            "allowed_actions": ["buy", "wait"],
            "require_buy_gate_open": True,
            "include_industries": ["医药"],
            "exclude_industries": ["中药"],
            "exclude_symbols": [],
        }
    )
    value["filters"] = filters
    value["max_results"] = 1
    strategy = CandidateStrategy.from_dict(value)

    assert strategy.allows(
        symbol="600276.SH",
        industry="创新医药",
        action="buy",
        direction="bullish",
        confidence=70,
        candidate_score=70,
        buy_gate_open=True,
    )
    assert not strategy.allows(
        symbol="600276.SH",
        industry="创新医药",
        action="buy",
        direction="bullish",
        confidence=70,
        candidate_score=70,
        buy_gate_open=False,
    )
    assert not strategy.allows(
        symbol="600285.SH",
        industry="中药医药",
        action="buy",
        direction="bullish",
        confidence=70,
        candidate_score=70,
        buy_gate_open=True,
    )
    filters["require_buy_gate_open"] = False
    value["filters"] = filters
    ranking_strategy = CandidateStrategy.from_dict(value)
    ranked = rank_strategy_candidates(
        [
            CandidateInput("600276.SH", "恒瑞医药", analysis(75), industry="创新医药"),
            CandidateInput("688180.SH", "君实生物", analysis(70), industry="生物医药"),
        ],
        ranking_strategy,
    )
    assert len(ranked) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"code": "print('unsafe')"}, "unknown strategy fields"),
        ({"max_results": 51}, "max_results"),
    ],
)
def test_strategy_rejects_unknown_executable_fields_and_out_of_range_values(
    change: dict[str, object], message: str
) -> None:
    value = payload()
    value.update(change)

    with pytest.raises(StrategyValidationError, match=message):
        CandidateStrategy.from_dict(value)


def test_strategy_rejects_unsafe_actions() -> None:
    value = payload()
    filters = dict(value["filters"])  # type: ignore[arg-type]
    filters["allowed_actions"] = ["buy", "sell"]
    value["filters"] = filters

    with pytest.raises(StrategyValidationError, match="allowed_actions"):
        CandidateStrategy.from_dict(value)


@pytest.mark.parametrize(
    ("section", "field", "invalid", "message"),
    [
        ("filters", "min_confidence", True, "min_confidence"),
        ("filters", "allowed_directions", ["up"], "allowed_directions"),
        ("filters", "exclude_symbols", ["not-a-symbol"], "invalid excluded symbol"),
        ("filters", "unexpected", 1, "unknown strategy fields"),
        ("ranking", "mode", "model-generated", "ranking.mode"),
        ("ranking", "preferred_industry_bonus", 31, "preferred_industry_bonus"),
        ("ranking", "market_adjustment_weight", float("nan"), "market_adjustment_weight"),
    ],
)
def test_strategy_rejects_nested_unknown_types_and_bounds(
    section: str, field: str, invalid: object, message: str
) -> None:
    value = payload()
    nested = dict(value[section])  # type: ignore[arg-type]
    nested[field] = invalid
    value[section] = nested

    with pytest.raises(StrategyValidationError, match=message):
        CandidateStrategy.from_dict(value)


def test_screening_skill_must_declare_strategy_resource(tmp_path: Path) -> None:
    package = tmp_path / "instructions-only"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\n"
        "name: instructions-only\n"
        "description: 只有说明的 Skill\n"
        "---\n"
        "说明。\n",
        encoding="utf-8",
    )
    workspace = SkillWorkspace(
        SkillWorkspacePolicy(include_builtin=False, user_roots=(tmp_path,))
    )
    selected = workspace.activate("instructions-only")

    with pytest.raises(SkillValidationError, match="does not declare strategy.json"):
        candidate_strategy_from_skill(selected)
