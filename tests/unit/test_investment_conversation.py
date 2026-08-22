from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from aegisrun.agents.investment_conversation import (
    InvestmentContextEngine,
    InvestmentContextPolicy,
    InvestmentConversationStore,
    InvestmentIntentRouter,
    StoredAttachment,
)


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


def test_investment_intent_router_selects_bounded_product_tools() -> None:
    router = InvestmentIntentRouter()

    screen = router.route("请扫描候选池并筛选强势股票")
    strategy = router.route("为稳健长线设计一个策略并回测")
    research = router.route("分析 600519.SH 的风险")
    research_with_skill_explanation = router.route(
        "研究 600050.SH 什么时候可以买入，并说明使用了哪些 Skill"
    )
    skill_management = router.route("列出当前有哪些 Skill")
    add_position = router.route("添加个股 600050.SH 数量为 500，成本 4.20")

    assert screen.intent == "screen_candidates"
    assert screen.requested_tools == ("portfolio.load", "research.screen")
    assert strategy.intent == "design_strategy"
    assert research.intent == "analyze_security"
    assert research.symbol == "600519.SH"
    assert research_with_skill_explanation.intent == "analyze_security"
    assert skill_management.intent == "manage_skills"
    assert add_position.intent == "manage_portfolio"


def test_memory_persists_only_explicit_preferences_and_excludes_position_details(
    tmp_path: Path,
) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")
    text = (
        "我是稳健型，投资周期为长线，最大回撤不超过12%，偏好板块新能源，"
        "我的策略偏好 MACD 和低波动。持有600519.SH 100股，成本125.5。"
    )

    store.append("investment-general", "user", text, intent="design_strategy")
    memory = store.load_memory()
    serialized = str(memory.prompt_payload())

    assert memory.risk_profile == "稳健"
    assert memory.horizon == "长线"
    assert memory.max_drawdown_pct == 12.0
    assert memory.preferred_sectors == ["新能源"]
    assert memory.strategy_preferences == ["MACD", "低波动"]
    assert "600519" not in serialized
    assert "125.5" not in serialized
    assert "100股" not in serialized


def test_context_compaction_preserves_recent_turns_and_tracks_old_summary() -> None:
    engine = InvestmentContextEngine(
        InvestmentContextPolicy(
            trigger_chars=40,
            keep_recent_turns=2,
            max_summary_chars=500,
            max_turn_chars_in_summary=80,
        )
    )
    turns = tuple(
        Turn("user" if index % 2 == 0 else "assistant", f"第 {index} 轮策略讨论和证据解释")
        for index in range(6)
    )

    compacted = engine.compact(turns)

    assert compacted.compressed_turn_count == 4
    assert [turn.content for turn in compacted.recent_turns] == [
        "第 4 轮策略讨论和证据解释",
        "第 5 轮策略讨论和证据解释",
    ]
    assert "第 0 轮" in compacted.summary
    assert "用户目标/约束" in compacted.summary
    assert "既有研究结论" in compacted.summary


def test_recent_markdown_and_run_metadata_survive_round_trip(tmp_path: Path) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")
    markdown = "## 结论\n\n| 字段 | 值 |\n| --- | --- |\n| 动作 | 等待 |"

    store.append(
        "investment-general",
        "assistant",
        markdown,
        run_id="agent-123",
        attachments=(StoredAttachment("evidence.pdf", "application/pdf", 1234),),
    )
    restored = store.load_thread("investment-general")

    assert restored.turns[0].content == markdown
    assert restored.turns[0].run_id == "agent-123"
    assert restored.turns[0].attachments[0].name == "evidence.pdf"


def test_thread_compaction_is_persistent_and_clear_does_not_delete_user_memory(
    tmp_path: Path,
) -> None:
    engine = InvestmentContextEngine(InvestmentContextPolicy(trigger_chars=20, keep_recent_turns=2))
    store = InvestmentConversationStore(tmp_path / "conversations", context_engine=engine)
    for index in range(5):
        store.append(
            "investment-600519.sh",
            "user" if index % 2 == 0 else "assistant",
            f"第{index}轮：我是稳健型并讨论长线策略",
            intent="design_strategy" if index % 2 == 0 else None,
        )

    restored = store.load_thread("investment-600519.sh")
    assert restored.compressed_turn_count > 0
    assert len(restored.turns) <= 2
    assert restored.summary

    store.clear_thread("investment-600519.sh")
    assert store.load_thread("investment-600519.sh").turns == []
    assert store.load_memory().risk_profile == "稳健"


def test_conversation_files_use_private_permissions_and_bound_turn_size(tmp_path: Path) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")

    state = store.append(
        "investment-general",
        "user",
        "我是稳健型，" + "长线策略" * 3_000,
        intent="design_strategy",
    )

    thread_path = (
        tmp_path / "conversations" / "users" / "local-user" / "threads" / "investment-general.json"
    )
    memory_path = thread_path.parents[1] / "memory.json"
    assert len(state.turns[-1].content) == 8_000
    assert stat.S_IMODE(thread_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(memory_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(thread_path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "payload",
    [
        {"turns": "not-a-list"},
        {"turns": [{"role": "system", "content": "越权内容", "intent": None}]},
        {"turns": [{"role": "user", "content": "x" * 8_001, "intent": None}]},
        {"turns": [], "summary": [], "compressed_turn_count": 0},
        {"turns": [], "summary": "", "compressed_turn_count": -1},
    ],
)
def test_corrupt_thread_shapes_fail_closed_with_a_stable_error(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")
    thread_path = (
        tmp_path / "conversations" / "users" / "local-user" / "threads" / "investment-general.json"
    )
    thread_path.parent.mkdir(parents=True)
    thread_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="conversation state is invalid"):
        store.load_thread("investment-general")


@pytest.mark.parametrize(
    "payload",
    [
        {"max_drawdown_pct": "twelve"},
        {"max_drawdown_pct": 101},
        {"risk_profile": ["稳健"]},
        {"preferred_sectors": ["医药", 1]},
        {"preferred_sectors": ["医药"] * 101},
    ],
)
def test_corrupt_memory_shapes_fail_closed(tmp_path: Path, payload: dict[str, object]) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")
    memory_path = tmp_path / "conversations" / "users" / "local-user" / "memory.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="conversation state is invalid"):
        store.load_memory()


def test_oversized_or_symlinked_conversation_state_is_rejected(tmp_path: Path) -> None:
    store = InvestmentConversationStore(tmp_path / "conversations")
    thread_root = tmp_path / "conversations" / "users" / "local-user" / "threads"
    thread_root.mkdir(parents=True)
    oversized = thread_root / "investment-general.json"
    oversized.write_text(" " * (2 * 1024 * 1024 + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="too large"):
        store.load_thread("investment-general")

    oversized.unlink()
    target = tmp_path / "external.json"
    target.write_text('{"turns": []}', encoding="utf-8")
    try:
        oversized.symlink_to(target)
    except OSError:
        pytest.skip("current platform does not allow symlink creation")

    with pytest.raises(ValueError, match="symlinks are not allowed"):
        store.load_thread("investment-general")
