from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SAFE_NAME = re.compile(r"^[a-z0-9]+(?:[_.-][a-z0-9]+)*$")


class CapabilityError(ValueError):
    """A capability is absent, incompatible, or outside the active scope."""


class TrustLevel(StrEnum):
    BUILTIN = "builtin"
    SIGNED_USER = "signed-user"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    kind: str
    name: str
    version: str
    features: frozenset[str] = frozenset()
    trust: TrustLevel = TrustLevel.BUILTIN

    def __post_init__(self) -> None:
        if not SAFE_NAME.fullmatch(self.kind) or not SAFE_NAME.fullmatch(self.name):
            raise CapabilityError("capability kind and name must be safe identifiers")
        if not self.version.strip():
            raise CapabilityError("capability version is required")
        if any(not SAFE_NAME.fullmatch(feature) for feature in self.features):
            raise CapabilityError("capability features must be safe identifiers")


@dataclass(slots=True)
class _Entry:
    descriptor: CapabilityDescriptor
    provider: Any


class CapabilityRegistration:
    def __init__(self, registry: CapabilityRegistry, key: tuple[str, str], entry: _Entry) -> None:
        self._registry = registry
        self._key = key
        self._entry = entry
        self._active = True

    def dispose(self) -> None:
        if self._active and self._registry._entries.get(self._key) is self._entry:
            del self._registry._entries[self._key]
        self._active = False

    def __enter__(self) -> CapabilityRegistration:
        return self

    def __exit__(self, *_: object) -> None:
        self.dispose()


class CapabilityRegistry:
    """Layered provider registry with a monotonic child-scope ceiling."""

    def __init__(
        self,
        parent: CapabilityRegistry | None = None,
        *,
        allowed: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.parent = parent
        self.allowed = dict(allowed) if allowed is not None else None
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._registrations: list[CapabilityRegistration] = []
        self._closed = False

    def _is_allowed(self, kind: str, name: str) -> bool:
        if self.allowed is None:
            return True
        return name in self.allowed.get(kind, frozenset())

    def register(self, descriptor: CapabilityDescriptor, provider: Any) -> CapabilityRegistration:
        if self._closed:
            raise CapabilityError("capability registry is closed")
        if not self._is_allowed(descriptor.kind, descriptor.name):
            raise CapabilityError(
                f"capability exceeds scope ceiling: {descriptor.kind}/{descriptor.name}"
            )
        key = (descriptor.kind, descriptor.name)
        if key in self._entries:
            raise CapabilityError(f"duplicate capability: {descriptor.kind}/{descriptor.name}")
        entry = _Entry(descriptor, provider)
        self._entries[key] = entry
        registration = CapabilityRegistration(self, key, entry)
        self._registrations.append(registration)
        return registration

    def resolve(
        self,
        kind: str,
        name: str,
        *,
        required_features: frozenset[str] = frozenset(),
    ) -> Any:
        if self._closed:
            raise CapabilityError("capability registry is closed")
        if not self._is_allowed(kind, name):
            raise CapabilityError(f"capability is outside this scope: {kind}/{name}")
        entry = self._entries.get((kind, name))
        if entry is None and self.parent is not None:
            provider = self.parent.resolve(kind, name, required_features=required_features)
            return provider
        if entry is None:
            raise CapabilityError(f"unknown capability: {kind}/{name}")
        missing = required_features - entry.descriptor.features
        if missing:
            raise CapabilityError(f"capability {kind}/{name} lacks features: {sorted(missing)}")
        return entry.provider

    def descriptors(self, kind: str | None = None) -> tuple[CapabilityDescriptor, ...]:
        merged: dict[tuple[str, str], CapabilityDescriptor] = {}
        if self.parent is not None:
            for descriptor in self.parent.descriptors(kind):
                if self._is_allowed(descriptor.kind, descriptor.name):
                    merged[(descriptor.kind, descriptor.name)] = descriptor
        for key, entry in self._entries.items():
            if kind is None or key[0] == kind:
                merged[key] = entry.descriptor
        return tuple(merged[key] for key in sorted(merged))

    def child(self, allowed: Mapping[str, frozenset[str]]) -> CapabilityRegistry:
        if self._closed:
            raise CapabilityError("capability registry is closed")
        if self.allowed is None:
            ceiling = dict(allowed)
        else:
            ceiling = {
                kind: names & self.allowed.get(kind, frozenset()) for kind, names in allowed.items()
            }
        return CapabilityRegistry(self, allowed=ceiling)

    def close(self) -> None:
        if self._closed:
            return
        for registration in reversed(self._registrations):
            registration.dispose()
        self._closed = True
