from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aegisrun.skills.catalog import SkillCatalog, SkillPackage, SkillSummary, SkillValidationError
from aegisrun.skills.registry import FilesystemSkillProvider, SkillRegistry


@dataclass(frozen=True, slots=True)
class SkillWorkspacePolicy:
    include_builtin: bool = True
    user_roots: tuple[Path, ...] = (Path(".equiseek/skills"),)
    disabled_skills: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SkillTurnSelection:
    prompt: str
    packages: tuple[SkillPackage, ...]
    explicit: bool

    @property
    def references(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "name": package.summary.name,
                "provider": package.summary.provider,
                "version": package.summary.version,
                "manifest_sha256": package.summary.manifest_sha256,
            }
            for package in self.packages
        )


class SkillWorkspace:
    """DeerFlow-style built-in/custom skill policy with user providers winning by rank."""

    def __init__(self, policy: SkillWorkspacePolicy | None = None) -> None:
        self.policy = policy or SkillWorkspacePolicy()
        self.registry = SkillRegistry()
        if self.policy.include_builtin:
            builtin_root = Path(__file__).resolve().parents[1] / "builtin_skills"
            self.registry.register_provider(
                FilesystemSkillProvider(
                    SkillCatalog((builtin_root,), provider_name="builtin", rank=100)
                )
            )
        for index, root in enumerate(self.policy.user_roots):
            self.registry.register_provider(
                FilesystemSkillProvider(
                    SkillCatalog(
                        (root,),
                        provider_name=f"user-{index + 1}",
                        rank=300 + index,
                    )
                )
            )
        self.registry.snapshot()

    def list(self) -> tuple[SkillSummary, ...]:
        return tuple(
            item
            for item in self.registry.list()
            if item.name not in self.policy.disabled_skills
        )

    def activate(
        self,
        name: str,
        *,
        agent: str = "advice-agent",
        granted_tools: frozenset[str] = frozenset(),
        network_allowed: bool = False,
        surface: str = "model",
    ) -> SkillPackage:
        if name in self.policy.disabled_skills:
            raise SkillValidationError(f"skill is disabled by workspace policy: {name}")
        return self.registry.activate(
            name,
            agent=agent,
            granted_tools=granted_tools,
            network_allowed=network_allowed,
            surface=surface,
        )

    def select_for_turn(
        self,
        prompt: str,
        *,
        defaults: tuple[str, ...] = (),
        agent: str = "advice-agent",
        granted_tools: frozenset[str] = frozenset(),
        network_allowed: bool = False,
    ) -> SkillTurnSelection:
        cleaned = prompt.strip()
        explicit = False
        selected = defaults
        if cleaned.startswith("/"):
            command, separator, remainder = cleaned.partition(" ")
            name = command[1:]
            if name:
                selected = (name,)
                cleaned = remainder.strip() if separator else ""
                explicit = True
        packages: list[SkillPackage] = []
        for name in selected:
            if name in self.policy.disabled_skills:
                continue
            try:
                package = self.activate(
                    name,
                    agent=agent,
                    granted_tools=granted_tools,
                    network_allowed=network_allowed,
                )
            except SkillValidationError:
                if explicit:
                    raise
                continue
            packages.append(package)
        if explicit and not cleaned:
            raise SkillValidationError("skill command requires a question after the skill name")
        return SkillTurnSelection(cleaned or prompt.strip(), tuple(packages), explicit)
