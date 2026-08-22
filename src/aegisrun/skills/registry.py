from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aegisrun.core.security import canonical_hash
from aegisrun.skills.catalog import SkillCatalog, SkillPackage, SkillSummary, SkillValidationError


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    candidates: tuple[SkillSummary, ...]
    complete: bool
    revision: str


class SkillProvider(Protocol):
    name: str
    rank: int

    def snapshot(self) -> SkillSnapshot: ...

    def load(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
    ) -> SkillPackage: ...


class FilesystemSkillProvider:
    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog
        self.name = catalog.provider_name
        self.rank = catalog.rank

    def snapshot(self) -> SkillSnapshot:
        candidates = self.catalog.refresh()
        revision = canonical_hash(
            [
                {
                    "name": item.name,
                    "description": item.description,
                    "manifest_sha256": item.manifest_sha256,
                    "model_invocable": item.model_invocable,
                    "user_invocable": item.user_invocable,
                }
                for item in candidates
            ]
        )
        return SkillSnapshot(candidates, True, revision)

    def load(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
    ) -> SkillPackage:
        return self.catalog.activate(
            name,
            agent=agent,
            granted_tools=granted_tools,
            network_allowed=network_allowed,
        )


@dataclass(slots=True)
class _ProviderEntry:
    provider: SkillProvider
    order: int


class SkillRegistry:
    """Multi-provider skill catalog with last-good complete discovery."""

    def __init__(self) -> None:
        self._providers: dict[str, _ProviderEntry] = {}
        self._next_order = 0
        self._last_good: SkillSnapshot | None = None
        self._winners: dict[str, SkillProvider] = {}

    @classmethod
    def from_catalog(cls, catalog: SkillCatalog) -> SkillRegistry:
        registry = cls()
        registry.register_provider(FilesystemSkillProvider(catalog))
        registry.snapshot()
        return registry

    def register_provider(self, provider: SkillProvider) -> Callable[[], None]:
        if not provider.name.strip() or provider.name in self._providers:
            raise SkillValidationError(f"duplicate or empty skill provider: {provider.name}")
        entry = _ProviderEntry(provider, self._next_order)
        self._next_order += 1
        self._providers[provider.name] = entry
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._providers.get(provider.name) is entry:
                del self._providers[provider.name]
                self._last_good = None
                self._winners = {}
            active = False

        return dispose

    def snapshot(self) -> SkillSnapshot:
        observations: list[tuple[_ProviderEntry, SkillSnapshot]] = []
        complete = True
        for entry in sorted(self._providers.values(), key=lambda item: item.order):
            try:
                observation = entry.provider.snapshot()
            except Exception:
                complete = False
                continue
            observations.append((entry, observation))
            complete = complete and observation.complete
        winners: dict[str, tuple[int, int, SkillSummary, SkillProvider]] = {}
        for entry, observation in observations:
            for candidate in observation.candidates:
                rank = max(candidate.rank, entry.provider.rank)
                current = winners.get(candidate.name)
                choice = (rank, -entry.order, candidate, entry.provider)
                if current is None or choice[:2] > current[:2]:
                    winners[candidate.name] = choice
        candidates = tuple(winners[name][2] for name in sorted(winners))
        revision = canonical_hash([candidate.to_dict() for candidate in candidates])
        snapshot = SkillSnapshot(candidates, complete, revision)
        if complete:
            self._last_good = snapshot
            self._winners = {name: winners[name][3] for name in winners}
            return snapshot
        if self._last_good is not None:
            return SkillSnapshot(
                self._last_good.candidates,
                False,
                self._last_good.revision,
            )
        return snapshot

    def list(self) -> tuple[SkillSummary, ...]:
        return self.snapshot().candidates

    def describe(self, name: str) -> SkillSummary:
        for candidate in self.snapshot().candidates:
            if candidate.name == name:
                return candidate
        raise SkillValidationError(f"unknown skill: {name}")

    def activate(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
        surface: str = "internal",
    ) -> SkillPackage:
        summary = self.describe(name)
        if surface == "model" and not summary.model_invocable:
            raise SkillValidationError(f"skill is not model-invocable: {name}")
        if surface == "user" and not summary.user_invocable:
            raise SkillValidationError(f"skill is not user-invocable: {name}")
        if surface not in {"internal", "model", "user"}:
            raise SkillValidationError(f"unknown skill invocation surface: {surface}")
        provider = self._winners.get(name)
        if provider is None:
            snapshot = self.snapshot()
            if not snapshot.complete:
                raise SkillValidationError("skill provider observation is incomplete")
            provider = self._winners.get(name)
        if provider is None:
            raise SkillValidationError(f"skill provider disappeared: {name}")
        package = provider.load(
            name,
            agent=agent,
            granted_tools=granted_tools,
            network_allowed=network_allowed,
        )
        if package.summary.name != name:
            raise SkillValidationError("skill provider returned a mismatched definition")
        return package
