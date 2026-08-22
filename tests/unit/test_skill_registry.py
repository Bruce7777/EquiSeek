from __future__ import annotations

from pathlib import Path

import pytest

from aegisrun.skills import (
    SkillPackage,
    SkillRegistry,
    SkillSnapshot,
    SkillSummary,
    SkillValidationError,
)


class Provider:
    name = "test-provider"
    rank = 200

    def __init__(self, summary: SkillSummary) -> None:
        self.summary = summary
        self.complete = True

    def snapshot(self) -> SkillSnapshot:
        return SkillSnapshot((self.summary,), self.complete, self.summary.manifest_sha256)

    def load(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
    ) -> SkillPackage:
        return SkillPackage(self.summary, "instructions", {}, "package-hash")


def summary(name: str, *, model: bool = True, user: bool = True) -> SkillSummary:
    return SkillSummary(
        name=name,
        description="bounded skill",
        version="1",
        package_root=Path("/virtual") / name,
        allowed_agents=(),
        allowed_tools=(),
        network_required=False,
        declared_resources=(),
        manifest_sha256=f"hash-{name}",
        model_invocable=model,
        user_invocable=user,
        provider="test-provider",
        rank=200,
    )


def test_registry_preserves_last_good_catalog_on_incomplete_observation() -> None:
    provider = Provider(summary("stable"))
    registry = SkillRegistry()
    registry.register_provider(provider)
    first = registry.snapshot()
    provider.summary = summary("transient")
    provider.complete = False
    second = registry.snapshot()

    assert first.complete is True
    assert second.complete is False
    assert [item.name for item in second.candidates] == ["stable"]


def test_registry_enforces_model_and_user_invocation_surfaces() -> None:
    registry = SkillRegistry()
    registry.register_provider(Provider(summary("human-only", model=False)))
    registry.snapshot()

    with pytest.raises(SkillValidationError, match="model-invocable"):
        registry.activate(
            "human-only",
            agent="worker",
            granted_tools=frozenset(),
            network_allowed=False,
            surface="model",
        )
    package = registry.activate(
        "human-only",
        agent="worker",
        granted_tools=frozenset(),
        network_allowed=False,
        surface="user",
    )
    assert package.instructions == "instructions"
