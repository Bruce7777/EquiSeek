from __future__ import annotations

from pathlib import Path

from aegisrun.skills import SkillWorkspace, SkillWorkspacePolicy


def write_skill(root: Path, name: str, instructions: str) -> None:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: 用户自定义投资策略\n"
        "version: 2.0.0\n"
        "author: local-user\n"
        "compatibility: AegisRun\n"
        "---\n"
        f"{instructions}\n",
        encoding="utf-8",
    )


def test_user_skill_overrides_builtin_with_same_name(tmp_path: Path) -> None:
    write_skill(tmp_path, "investment-decision-engine", "使用用户自己的价值投资流程。")
    workspace = SkillWorkspace(
        SkillWorkspacePolicy(include_builtin=True, user_roots=(tmp_path,))
    )

    selected = workspace.select_for_turn(
        "请解释当前策略", defaults=("investment-decision-engine",)
    )

    assert len(selected.packages) == 1
    assert selected.packages[0].summary.provider == "user-1"
    assert "用户自己的价值投资流程" in selected.packages[0].instructions


def test_workspace_can_run_without_any_builtin_skills(tmp_path: Path) -> None:
    write_skill(tmp_path, "my-stock-filter", "按用户定义的财务和技术条件筛选。")
    workspace = SkillWorkspace(
        SkillWorkspacePolicy(include_builtin=False, user_roots=(tmp_path,))
    )

    assert [item.name for item in workspace.list()] == ["my-stock-filter"]
    selected = workspace.select_for_turn("/my-stock-filter 筛选候选池")
    assert selected.explicit is True
    assert selected.prompt == "筛选候选池"
    assert selected.packages[0].summary.provider == "user-1"


def test_windows_crlf_skill_markdown_is_supported(tmp_path: Path) -> None:
    package = tmp_path / "windows-stock-filter"
    package.mkdir()
    content = (
        "---\n"
        "name: windows-stock-filter\n"
        "description: Windows 用户自定义筛选规则\n"
        "version: 1.0.0\n"
        "---\n"
        "按现金流和估值筛选。\n"
    )
    (package / "SKILL.md").write_bytes(content.replace("\n", "\r\n").encode("utf-8"))

    workspace = SkillWorkspace(
        SkillWorkspacePolicy(include_builtin=False, user_roots=(tmp_path,))
    )
    selected = workspace.select_for_turn("/windows-stock-filter 开始筛选")

    assert selected.packages[0].instructions == "按现金流和估值筛选。"


def test_disabled_skill_is_not_listed_or_implicitly_loaded(tmp_path: Path) -> None:
    workspace = SkillWorkspace(
        SkillWorkspacePolicy(
            include_builtin=True,
            user_roots=(tmp_path,),
            disabled_skills=frozenset({"investment-decision-engine"}),
        )
    )

    selected = workspace.select_for_turn(
        "解释策略", defaults=("investment-decision-engine",)
    )

    assert "investment-decision-engine" not in {item.name for item in workspace.list()}
    assert selected.packages == ()
