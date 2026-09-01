from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegisrun.harness.loop_control import AgentLoopHarness
from aegisrun.tools import RiskLevel, ToolSpec


def spec(name: str, description: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1.0.0",
        description=description,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=RiskLevel.LOW,
        side_effect=False,
    )


def harness(tmp_path: Path) -> AgentLoopHarness:
    specs = (
        spec("tools.search", "发现工具"),
        spec("plan.update", "更新计划"),
        spec("market.bars", "读取历史行情"),
        spec("macro.snapshot", "读取宏观快照"),
        spec("web.search", "搜索公开网页"),
    )
    return AgentLoopHarness(
        goal="分析历史行情",
        intent="general_research",
        tool_specs=specs,
        allowed_tool_names=tuple(item.name for item in specs),
        state_directory=tmp_path,
        observation_budget_chars=2_000,
        observation_item_chars=500,
    )


def test_deferred_tools_are_discovered_and_promoted_by_capability(tmp_path: Path) -> None:
    control = harness(tmp_path)

    assert "market.bars" in control.promoted_tool_names
    assert "macro.snapshot" in control.deferred_tool_names

    matches = control.discover("需要宏观分析")

    assert [item["name"] for item in matches] == ["macro.snapshot"]
    assert control.is_promoted("macro.snapshot")
    assert control.discover("需要宏观分析") == []


def test_deferred_catalog_supports_exact_selection_and_stable_hash(tmp_path: Path) -> None:
    control = harness(tmp_path)

    discovery = control.model_context()["tool_discovery"]
    deferred_names = {item["name"] for item in discovery["deferred_catalog"]}
    matches = control.discover("select:web.search,macro.snapshot")

    assert {"macro.snapshot", "web.search"} <= deferred_names
    assert [item["name"] for item in matches] == ["macro.snapshot", "web.search"]
    assert len(discovery["catalog_hash"]) == 64

    specs = tuple(
        reversed(
            (
                spec("tools.search", "发现工具"),
                spec("plan.update", "更新计划"),
                spec("market.bars", "读取历史行情"),
                spec("macro.snapshot", "读取宏观快照"),
                spec("web.search", "搜索公开网页"),
            )
        )
    )
    reordered = AgentLoopHarness(
        goal="分析历史行情",
        intent="general_research",
        tool_specs=specs,
        allowed_tool_names=tuple(item.name for item in specs),
        state_directory=tmp_path / "reordered",
        observation_budget_chars=2_000,
        observation_item_chars=500,
    )
    changed_specs = (*specs[:-1], spec(specs[-1].name, "发生变化的描述"))
    changed = AgentLoopHarness(
        goal="分析历史行情",
        intent="general_research",
        tool_specs=changed_specs,
        allowed_tool_names=tuple(item.name for item in changed_specs),
        state_directory=tmp_path / "changed",
        observation_budget_chars=2_000,
        observation_item_chars=500,
    )

    assert reordered.catalog_hash == control.catalog_hash
    assert changed.catalog_hash != control.catalog_hash


def test_plan_snapshot_allows_only_one_active_item(tmp_path: Path) -> None:
    control = harness(tmp_path)

    with pytest.raises(ValueError, match="at most one"):
        control.replace_plan(
            [
                {"id": "one", "title": "第一项", "status": "in_progress"},
                {"id": "two", "title": "第二项", "status": "in_progress"},
            ]
        )

    allowed = control.before_tool("market.bars", {"symbol": "600000.SH"})
    control.after_tool("market.bars", ok=True, detail="读取完成")
    items = control.plan_snapshot()["items"]

    assert allowed.allowed
    assert items[0]["status"] == "completed"
    assert items[1]["status"] == "in_progress"


def test_model_owned_plan_is_not_advanced_by_tool_success(tmp_path: Path) -> None:
    control = harness(tmp_path)
    control.replace_plan(
        [
            {
                "id": "model-evidence",
                "title": "由模型判断证据是否充分",
                "status": "in_progress",
                "tool": "market.bars",
            },
            {"id": "model-synthesis", "title": "形成结论", "status": "pending"},
        ]
    )

    control.before_tool("market.bars", {"symbol": "600000.SH"})
    control.after_tool("market.bars", ok=True, detail="工具成功但任务未必完成")
    snapshot = control.plan_snapshot()

    assert snapshot["owner"] == "model"
    assert [item["status"] for item in snapshot["items"]] == ["in_progress", "pending"]


def test_loop_guard_only_reminds_on_consecutive_exact_repeats(tmp_path: Path) -> None:
    control = harness(tmp_path)

    decisions = [control.before_tool("market.bars", {"symbol": "600000.SH"}) for _ in range(8)]

    assert all(decision.allowed for decision in decisions)
    assert [index + 1 for index, decision in enumerate(decisions) if decision.code] == [3, 5, 8]
    assert all(decisions[index - 1].code == "REPEATED_TOOL_CALL_REMINDER" for index in (3, 5, 8))
    assert "工具：market.bars" in decisions[4].reason
    assert '参数：{"symbol":"600000.SH"}' in decisions[4].reason

    different = control.before_tool("market.bars", {"symbol": "000001.SZ"})
    original_again = control.before_tool("market.bars", {"symbol": "600000.SH"})

    assert different.allowed and not different.code
    assert original_again.allowed and not original_again.code


def test_large_observation_is_externalized_and_model_projection_is_bounded(
    tmp_path: Path,
) -> None:
    control = harness(tmp_path)
    observations = [
        {
            "tool": "market.bars",
            "ok": True,
            "summary": "读取大量历史行情",
            "data": {"bars": [{"close": index, "note": "x" * 80} for index in range(80)]},
        }
    ]

    projected = control.project_observations(observations)

    assert "externalized" in projected[0]
    relative = projected[0]["externalized"]["path"].removeprefix(".state/")
    saved = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    assert len(saved["data"]["bars"]) == 80
    assert len(json.dumps(projected, ensure_ascii=False)) < 2_000
    assert control.snapshot()["observation_budget"]["externalized_results"] == 1


def test_total_observation_budget_holds_with_multiple_large_metadata_fields(
    tmp_path: Path,
) -> None:
    control = harness(tmp_path)
    observations = [
        {
            "tool": "market.bars",
            "ok": False,
            "summary": "摘要" * 1_000,
            "error": "错误" * 1_000,
            "artifact_id": "artifact-" * 500,
            "harness_notice": "提醒" * 1_000,
            "data": {"payload": "行情" * 2_000},
        }
    ]

    projected = control.project_observations(observations)
    encoded = json.dumps(
        projected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert len(encoded) <= control.observation_budget_chars
    assert projected[0]["externalized"]["path"].startswith(".state/tool-results/")


def test_externalized_result_can_be_read_in_verified_pages(tmp_path: Path) -> None:
    control = harness(tmp_path)
    projected = control.project_observations(
        [
            {
                "tool": "market.bars",
                "ok": True,
                "summary": "读取大量历史行情",
                "data": {"payload": "求衡" * 1_000},
            }
        ]
    )
    reference = projected[0]["externalized"]

    first = control.read_externalized_result(reference["path"], limit=200)
    second = control.read_externalized_result(
        reference["path"], offset=first["next_offset"], limit=5_000
    )

    assert first["sha256"] == reference["sha256"]
    assert first["next_offset"] == 200
    assert first["eof"] is False
    assert second["eof"] is True
    assert first["content"] + second["content"] == (
        tmp_path / reference["path"].removeprefix(".state/")
    ).read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not an externalized result reference"):
        control.read_externalized_result(".state/tool-results/../events.jsonl")
    with pytest.raises(ValueError, match="does not exist"):
        control.read_externalized_result(".state/tool-results/missing.json")
