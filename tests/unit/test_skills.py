from __future__ import annotations

from pathlib import Path

import pytest

from aegisrun.skills import SkillCatalog, SkillValidationError, builtin_skill_catalog


def write_skill(root: Path, name: str, frontmatter: str, body: str = "instructions") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n{frontmatter}\n---\n{body}",
        encoding="utf-8",
    )
    return package


def test_builtin_skill_catalog_discovers_metadata_then_activates_content() -> None:
    catalog = builtin_skill_catalog()

    names = {skill.name for skill in catalog.list()}
    assert names == {
        "a-share-market-data",
        "capital-three-flows",
        "cost-transfer-lens",
        "deepseek-summary",
        "historical-evidence",
        "html-research-report",
        "macro-investment-synthesis",
        "macro-official-data",
        "macro-official-freshness",
        "market-sector-confluence",
        "multi-timeframe-macd-wr",
        "non-advisory-guardrail",
        "investment-decision-engine",
        "investment-output-guardrail",
        "portfolio-risk-monitor",
        "technical-indicators",
    }
    assert catalog.search("技术 指标")[0].name == "technical-indicators"
    package = catalog.activate(
        "technical-indicators",
        agent="indicator-agent",
        granted_tools=frozenset(),
        network_allowed=False,
    )
    assert "模型猜测数值" in package.instructions
    assert len(package.package_sha256) == 64


def test_skill_activation_enforces_agent_tool_and_network_policy() -> None:
    catalog = builtin_skill_catalog()

    with pytest.raises(SkillValidationError, match="not allowed"):
        catalog.activate(
            "deepseek-summary",
            agent="indicator-agent",
            granted_tools=frozenset({"deepseek-chat"}),
            network_allowed=True,
        )
    with pytest.raises(SkillValidationError, match="not granted"):
        catalog.activate(
            "deepseek-summary",
            agent="language-agent",
            granted_tools=frozenset(),
            network_allowed=True,
        )
    with pytest.raises(SkillValidationError, match="requires network"):
        catalog.activate(
            "deepseek-summary",
            agent="language-agent",
            granted_tools=frozenset({"deepseek-chat"}),
            network_allowed=False,
        )


def test_skill_catalog_rejects_duplicates_and_package_symlinks(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_skill(first, "sample", "allowed-tools: []\nnetwork-required: false")
    write_skill(second, "sample", "allowed-tools: []\nnetwork-required: false")
    with pytest.raises(SkillValidationError, match="duplicate"):
        SkillCatalog((first, second))

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "linked-sample").symlink_to(first / "sample", target_is_directory=True)
    with pytest.raises(SkillValidationError, match="symlinks"):
        SkillCatalog((linked,))


def test_skill_resources_are_explicit_bounded_and_cannot_escape(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    package = write_skill(
        skills,
        "resource-skill",
        "allowed-agents: [worker]\nallowed-tools: []\nnetwork-required: false\n"
        "resources: [references/rules.md]",
    )
    reference = package / "references" / "rules.md"
    reference.parent.mkdir()
    reference.write_text("bounded evidence", encoding="utf-8")
    catalog = SkillCatalog((skills,))

    activated = catalog.activate(
        "resource-skill",
        agent="worker",
        granted_tools=frozenset(),
        network_allowed=False,
    )
    assert activated.resources == {"references/rules.md": "bounded evidence"}

    (package / "SKILL.md").write_text(
        (package / "SKILL.md")
        .read_text(encoding="utf-8")
        .replace("references/rules.md", "../secret.txt"),
        encoding="utf-8",
    )
    escaped = SkillCatalog((skills,))
    with pytest.raises(SkillValidationError, match="unsafe"):
        escaped.activate(
            "resource-skill",
            agent="worker",
            granted_tools=frozenset(),
            network_allowed=False,
        )


def test_skill_catalog_rejects_symlink_root_and_post_discovery_mutation(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    write_skill(actual, "stable", "allowed-tools: []\nnetwork-required: false")
    linked = tmp_path / "linked-root"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(SkillValidationError, match="root symlinks"):
        SkillCatalog((linked,))

    catalog = SkillCatalog((actual,))
    skill_file = actual / "stable" / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\nchanged after discovery",
        encoding="utf-8",
    )
    with pytest.raises(SkillValidationError, match="changed after discovery"):
        catalog.activate("stable", agent="worker", granted_tools=frozenset(), network_allowed=False)
