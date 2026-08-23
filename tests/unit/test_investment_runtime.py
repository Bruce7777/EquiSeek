from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from aegisrun.agents.investment_runtime import (
    InvestmentAgentAction,
    InvestmentAgentRunRequest,
    InvestmentAgentRuntime,
)
from aegisrun.macro.freshness import OfficialMacroFreshnessVerifier
from aegisrun.macro.providers import BundledOfficialMacroProvider
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.portfolio.models import PortfolioBook
from aegisrun.portfolio.repository import PortfolioRepository
from aegisrun.skills import SkillWorkspace, SkillWorkspacePolicy


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def persisted_text(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())


def path_is_file(path: str) -> bool:
    return Path(path).is_file()


def macro_verifier() -> OfficialMacroFreshnessVerifier:
    return OfficialMacroFreshnessVerifier(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="2026-08-17", request=request)
        )
    )


def request(question: str, *, intent: str = "general_research") -> InvestmentAgentRunRequest:
    return InvestmentAgentRunRequest(
        question=question,
        intent=intent,  # type: ignore[arg-type]
        thread_id="investment-general",
        portfolio=PortfolioBook(),
        source="demo",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 20),
        adjustment=AdjustmentMode.QFQ,
    )


@pytest.mark.asyncio
async def test_local_runtime_reads_macro_and_persists_workspace_artifact(tmp_path: Path) -> None:
    progress: list[dict[str, object]] = []
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=())),
        macro_verifier=macro_verifier(),
    )

    result = await runtime.run(
        request("宏观分析的数据来源是什么，是否实时？"),
        on_progress=progress.append,
    )

    assert result.status == "succeeded"
    assert "macro.snapshot" in result.tool_calls
    assert "2026-06-30" in result.answer
    assert "当前结论不可用" in result.answer
    assert "stats.gov.cn" in result.answer
    assert all(path_is_file(artifact.path) for artifact in result.artifacts)
    state = read_json(Path(result.workspace) / ".state" / "investment-run.json")
    assert state["status"] == "succeeded"
    assert state["trace"] == "investment-agent-trace.md"
    assert {artifact.name for artifact in result.artifacts} == {
        "investment-agent-report.html",
        "investment-agent-report.md",
        "investment-agent-trace.md",
    }
    assert state["html_report"] == "investment-agent-report.html"
    html_report = next(item for item in result.artifacts if item.media_type == "text/html")
    assert html_report.name == "investment-agent-report.html"
    html_text = persisted_text(Path(result.workspace))
    assert "Content-Security-Policy" in html_text
    assert "<script" not in html_text
    assert "html-research-report" in html_text
    assert "本轮 Skill" not in result.answer
    assert "执行方式" not in result.answer
    assert "查看依据" not in result.answer
    assert any(step.stage == "skill" for step in result.trace)
    assert progress[0]["kind"] == "run-started"
    assert progress[-1]["kind"] == "run-ended"


@pytest.mark.asyncio
async def test_online_agent_macro_tool_loads_live_provider_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    live_snapshot = replace(
        BundledOfficialMacroProvider().load(),
        version="cn-macro-official-live-test",
        as_of=date(2026, 8, 22),
    )

    class Provider:
        def load(self):  # type: ignore[no-untyped-def]
            return live_snapshot

    def provider_factory(*, live: bool = False):  # type: ignore[no-untyped-def]
        calls.append(live)
        return Provider()

    monkeypatch.setattr(
        "aegisrun.agents.investment_runtime.default_macro_provider", provider_factory
    )
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=())),
        macro_verifier=macro_verifier(),
    )

    result = await runtime.run(
        replace(request("宏观分析的数据来源是什么，是否实时？"), source="baostock")
    )

    assert calls == [True]
    assert "macro.snapshot" in result.tool_calls
    persisted = persisted_text(Path(result.workspace))
    assert "cn-macro-official-live-test" in persisted
    assert '"realtime":true' in persisted


@pytest.mark.asyncio
async def test_offline_web_search_fails_explicitly_without_fake_live_data(
    tmp_path: Path,
) -> None:
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
    )

    result = await runtime.run(request("联网搜索今天最新公告"))

    assert result.status == "succeeded"
    assert result.tool_calls == ("web.search",)
    assert "当前处于离线演示模式" in result.answer
    assert "不会把模型记忆冒充实时搜索结果" in result.answer


@pytest.mark.asyncio
async def test_default_online_mode_can_search_public_web_without_an_api_key(
    tmp_path: Path,
) -> None:
    page = """<html><body><a class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fannouncement">
    交易所公开公告</a><a class="result__snippet">公告发布日期与摘要</a></body></html>"""

    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
        web_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        ),
    )
    run_request = replace(request("联网搜索今天最新公告"), source="baostock")

    result = await runtime.run(run_request)

    assert result.status == "succeeded"
    assert result.tool_calls == ("web.search",)
    assert "交易所公开公告" in result.answer
    assert "https://example.com/announcement" in result.answer
    assert "searched_at_runtime" in persisted_text(Path(result.workspace))


class ScriptedModel:
    def __init__(self) -> None:
        self.calls = 0

    async def choose_investment_action(self, **_: object) -> InvestmentAgentAction:
        self.calls += 1
        if self.calls == 1:
            return InvestmentAgentAction("tool", "macro.snapshot", reason="先读取有截止日期的事实")
        return InvestmentAgentAction(
            "final",
            content=(
                "## 结论\n\n已读取本地宏观历史快照；它不是实时数据。"
                "这是历史研究，不保证收益，也不会自动下单。"
            ),
        )


@pytest.mark.asyncio
async def test_model_selects_only_registered_tool_and_runtime_records_it(tmp_path: Path) -> None:
    model = ScriptedModel()
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=())),
        model=model,
        macro_verifier=macro_verifier(),
    )

    result = await runtime.run(request("分析宏观风险"))

    assert model.calls == 2
    assert result.answer_mode == "deepseek"
    assert result.tool_calls == ("macro.snapshot",)
    events = (Path(result.workspace) / ".state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type":"tool/call"' in events
    assert '"event_type":"tool/result"' in events


class ToolVisibilityModel:
    def __init__(self) -> None:
        self.tool_names: set[str] = set()

    async def choose_investment_action(self, **kwargs: object) -> InvestmentAgentAction:
        tools = kwargs["tools"]
        assert isinstance(tools, list)
        self.tool_names = {str(item["name"]) for item in tools}  # type: ignore[index]
        return InvestmentAgentAction("final", content="工具边界已检查，不自动下单。")


@pytest.mark.asyncio
async def test_read_only_agent_exposes_shell_and_reader_but_not_file_mutations(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    model = ToolVisibilityModel()
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
        model=model,
    )

    await runtime.run(replace(request("检查工作区"), working_directory=str(selected)))

    assert {"list_files", "read", "bash"} <= model.tool_names
    assert "write" not in model.tool_names
    assert "edit" not in model.tool_names


class WorkspaceHarnessModel:
    def __init__(self) -> None:
        self.calls = 0
        self.persisted_value = ""

    async def choose_investment_action(self, **kwargs: object) -> InvestmentAgentAction:
        self.calls += 1
        if self.calls == 5:
            observations = kwargs["observations"]
            assert isinstance(observations, list)
            self.persisted_value = str(observations[-1]["data"]["output"])  # type: ignore[index]
        actions = (
            InvestmentAgentAction("tool", "list_files", {"path": "."}),
            InvestmentAgentAction(
                "tool", "write", {"file_path": "agent-note.md", "content": "# research\n"}
            ),
            InvestmentAgentAction(
                "tool",
                "bash",
                {"command": "export EQUISEEK_PERSIST=ready; mkdir -p reports; cd reports"},
            ),
            InvestmentAgentAction(
                "tool", "bash", {"command": 'printf "%s" "$EQUISEEK_PERSIST"'}
            ),
            InvestmentAgentAction(
                "final", content="已在同一个求衡投研助手中使用文件编辑器和持久 Shell。"
            ),
        )
        return actions[self.calls - 1]


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="the enforced shell uses macOS Seatbelt")
async def test_full_agent_uses_workspace_editor_and_persistent_shell_tools(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    model = WorkspaceHarnessModel()
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
        model=model,
    )
    run_request = replace(
        request("整理本地研究文件并继续投资分析"),
        working_directory=str(selected),
        workspace_permission="workspace-write",
    )

    result = await runtime.run(run_request)

    assert result.status == "succeeded"
    assert result.tool_calls == ("list_files", "write", "bash", "bash")
    assert (selected / "agent-note.md").read_text(encoding="utf-8") == "# research\n"
    assert (selected / "reports").is_dir()
    assert model.persisted_value == "ready"


@pytest.mark.asyncio
async def test_explicit_skill_selection_does_not_silently_add_another_entry_skill(
    tmp_path: Path,
) -> None:
    skills = SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=()))
    selection = skills.select_for_turn("/investment-decision-engine 宏观历史快照的数据来源是什么？")
    runtime = InvestmentAgentRuntime(tmp_path / "runs", skills, macro_verifier=macro_verifier())
    run_request = replace(
        request("宏观历史快照的数据来源是什么？"),
        active_skills=selection.packages,
        skill_selection_mode="explicit",
    )

    result = await runtime.run(run_request)

    assert "skills.activate" not in result.tool_calls
    assert [item["name"] for item in result.active_skills] == ["investment-decision-engine"]
    assert "macro.snapshot" in result.tool_calls


@pytest.mark.asyncio
async def test_explicit_user_skill_instructions_are_used_in_local_answer(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    package_root = skill_root / "my-telecom-check"
    package_root.mkdir(parents=True)
    (package_root / "SKILL.md").write_text(
        """---
name: my-telecom-check
description: 用户通信运营商研究规则。
version: 1.0.0
allowed-agents:
  - investment-lead-agent
allowed-tools: []
network-required: false
resources: []
---

# 通信运营商检查

1. 核对经营现金流能否覆盖资本开支。
2. 核对分红是否由自由现金流覆盖。
3. 不覆盖平台技术门控，不承诺收益，不自动下单。
""",
        encoding="utf-8",
    )
    skills = SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=(skill_root,)))
    package = skills.activate(
        "my-telecom-check",
        agent="investment-lead-agent",
        granted_tools=frozenset(),
        network_allowed=False,
    )
    runtime = InvestmentAgentRuntime(tmp_path / "runs", skills)
    run_request = replace(
        request("使用我选择的 Skill，说明检查通信运营商时要看什么", intent="manage_skills"),
        active_skills=(package,),
        skill_selection_mode="explicit",
    )

    result = await runtime.run(run_request)

    assert result.tool_calls == ("skills.list",)
    assert "已按本轮选择的 Skill 读取规则" in result.answer
    assert "经营现金流能否覆盖资本开支" in result.answer
    assert "分红是否由自由现金流覆盖" in result.answer
    assert "当前可用 Skill" not in result.answer


@pytest.mark.asyncio
async def test_runtime_can_execute_real_demo_market_research_tool(tmp_path: Path) -> None:
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=True, user_roots=())),
    )
    run_request = request("分析 600519.SH", intent="analyze_security")
    run_request = replace(run_request, symbol="600519.SH")

    result = await runtime.run(run_request)

    assert "market.analyze" in result.tool_calls
    assert "600519.SH" in result.answer
    assert "当前决策" in result.answer
    assert "关键指标" in result.answer
    assert "月 / 周 / 日多周期" in result.answer
    assert "五步决策门控" in result.answer
    assert "规则情景区间" in result.answer
    assert any(step.stage == "research-task" for step in result.trace)
    assert any(step.stage == "decision-gate" for step in result.trace)
    assert any("multi-timeframe-macd-wr" in step.skill_names for step in result.trace)
    assert any(step.evidence_path for step in result.trace)
    trace_artifact = next(
        artifact for artifact in result.artifacts if artifact.name == "investment-agent-trace.md"
    )
    assert path_is_file(trace_artifact.path)
    trace_text = persisted_text(Path(result.workspace))
    assert "规则门控" in trace_text
    assert "证据文件" in trace_text


@pytest.mark.asyncio
async def test_agent_can_add_list_update_and_remove_local_position(tmp_path: Path) -> None:
    repository = PortfolioRepository(tmp_path / "portfolio.json")
    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
        portfolio_manager=repository,
    )

    added = await runtime.run(
        replace(
            request("添加持仓 600050.SH 10手，成本 4.20", intent="manage_portfolio"),
            symbol="600050.SH",
        )
    )
    listed = await runtime.run(request("查询我的本地持仓", intent="manage_portfolio"))
    updated = await runtime.run(
        replace(
            request("更新持仓 600050.SH 成本 4.35", intent="manage_portfolio"),
            symbol="600050.SH",
        )
    )
    removed = await runtime.run(
        replace(
            request("删除持仓 600050.SH", intent="manage_portfolio"),
            symbol="600050.SH",
        )
    )

    assert repository.load().positions == ()
    assert "1000" in added.answer
    assert "本地持仓" in listed.answer and "600050.SH" in listed.answer
    assert "4.35" in updated.answer
    assert "已删除本地持仓记录" in removed.answer


@pytest.mark.asyncio
async def test_configured_web_search_returns_sources_without_persisting_key(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["api_key"] == "private-search-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "交易所公告",
                        "url": "https://example.com/announcement",
                        "content": "公开公告摘要",
                    }
                ]
            },
        )

    runtime = InvestmentAgentRuntime(
        tmp_path / "runs",
        SkillWorkspace(SkillWorkspacePolicy(include_builtin=False, user_roots=())),
        web_transport=httpx.MockTransport(handler),
    )
    base = request("联网搜索今天最新公告")
    run_request = replace(base, web_search_api_key="private-search-key")

    result = await runtime.run(run_request)

    assert "https://example.com/announcement" in result.answer
    persisted = persisted_text(Path(result.workspace))
    assert "private-search-key" not in persisted
