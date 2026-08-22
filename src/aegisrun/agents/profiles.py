from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aegisrun.core.security import canonical_hash


class AgentSpecView(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def handlers(self) -> frozenset[str]: ...

    @property
    def allowed_skills(self) -> frozenset[str]: ...

    @property
    def allowed_tools(self) -> frozenset[str]: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    @property
    def max_concurrency(self) -> int: ...

    @property
    def max_depth(self) -> int: ...

    @property
    def max_children(self) -> int: ...

    @property
    def network_allowed(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AgentProfileSnapshot:
    id: str
    generation: str
    parent_generation: str | None
    description: str
    allowed_handlers: frozenset[str]
    allowed_skills: frozenset[str]
    allowed_tools: frozenset[str]
    capabilities: frozenset[str]
    max_concurrency: int
    max_depth: int
    max_children: int
    network_allowed: bool
    digest: str

    @classmethod
    def from_spec(cls, spec: AgentSpecView) -> AgentProfileSnapshot:
        body = {
            "id": spec.name,
            "description": spec.description,
            "allowed_handlers": sorted(spec.handlers),
            "allowed_skills": sorted(spec.allowed_skills),
            "allowed_tools": sorted(spec.allowed_tools),
            "capabilities": sorted(spec.capabilities),
            "max_concurrency": spec.max_concurrency,
            "max_depth": spec.max_depth,
            "max_children": spec.max_children,
            "network_allowed": spec.network_allowed,
        }
        digest = canonical_hash(body)
        return cls(
            id=spec.name,
            generation=f"profile-{digest[:20]}",
            parent_generation=None,
            description=spec.description,
            allowed_handlers=spec.handlers,
            allowed_skills=spec.allowed_skills,
            allowed_tools=spec.allowed_tools,
            capabilities=spec.capabilities,
            max_concurrency=spec.max_concurrency,
            max_depth=spec.max_depth,
            max_children=spec.max_children,
            network_allowed=spec.network_allowed,
            digest=digest,
        )

    def narrow(
        self,
        *,
        profile_id: str,
        allowed_handlers: frozenset[str] | None = None,
        allowed_skills: frozenset[str] | None = None,
        allowed_tools: frozenset[str] | None = None,
        capabilities: frozenset[str] | None = None,
        max_concurrency: int | None = None,
        max_depth: int | None = None,
        max_children: int | None = None,
        network_allowed: bool | None = None,
    ) -> AgentProfileSnapshot:
        handlers = allowed_handlers if allowed_handlers is not None else self.allowed_handlers
        skills = allowed_skills if allowed_skills is not None else self.allowed_skills
        tools = allowed_tools if allowed_tools is not None else self.allowed_tools
        features = capabilities if capabilities is not None else self.capabilities
        concurrency = max_concurrency if max_concurrency is not None else self.max_concurrency
        depth = max_depth if max_depth is not None else self.max_depth
        children = max_children if max_children is not None else self.max_children
        network = network_allowed if network_allowed is not None else self.network_allowed
        for candidate, parent, label in (
            (handlers, self.allowed_handlers, "handlers"),
            (skills, self.allowed_skills, "skills"),
            (tools, self.allowed_tools, "tools"),
            (features, self.capabilities, "capabilities"),
        ):
            if not candidate <= parent:
                raise ValueError(f"child profile cannot expand parent {label}")
        if concurrency < 1 or concurrency > self.max_concurrency:
            raise ValueError("child profile concurrency exceeds parent")
        if depth < 0 or depth > self.max_depth:
            raise ValueError("child profile depth exceeds parent")
        if children < 1 or children > self.max_children:
            raise ValueError("child profile count exceeds parent")
        if network and not self.network_allowed:
            raise ValueError("child profile cannot enable parent-denied network")
        body = {
            "id": profile_id,
            "parent_generation": self.generation,
            "description": self.description,
            "allowed_handlers": sorted(handlers),
            "allowed_skills": sorted(skills),
            "allowed_tools": sorted(tools),
            "capabilities": sorted(features),
            "max_concurrency": concurrency,
            "max_depth": depth,
            "max_children": children,
            "network_allowed": network,
        }
        digest = canonical_hash(body)
        return AgentProfileSnapshot(
            id=profile_id,
            generation=f"profile-{digest[:20]}",
            parent_generation=self.generation,
            description=self.description,
            allowed_handlers=handlers,
            allowed_skills=skills,
            allowed_tools=tools,
            capabilities=features,
            max_concurrency=concurrency,
            max_depth=depth,
            max_children=children,
            network_allowed=network,
            digest=digest,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentProfileSnapshot:
        handlers = frozenset(str(item) for item in value["allowed_handlers"])
        skills = frozenset(str(item) for item in value["allowed_skills"])
        tools = frozenset(str(item) for item in value["allowed_tools"])
        capabilities = frozenset(str(item) for item in value["capabilities"])
        max_concurrency = int(value["max_concurrency"])
        max_depth = int(value["max_depth"])
        max_children = int(value["max_children"])
        network_allowed = bool(value["network_allowed"])
        body = {
            "id": str(value["id"]),
            "description": str(value["description"]),
            "allowed_handlers": sorted(handlers),
            "allowed_skills": sorted(skills),
            "allowed_tools": sorted(tools),
            "capabilities": sorted(capabilities),
            "max_concurrency": max_concurrency,
            "max_depth": max_depth,
            "max_children": max_children,
            "network_allowed": network_allowed,
        }
        parent_generation = value.get("parent_generation")
        if parent_generation is not None:
            body["parent_generation"] = str(parent_generation)
        digest = canonical_hash(body)
        generation = str(value["generation"])
        if str(value["digest"]) != digest or generation != f"profile-{digest[:20]}":
            raise ValueError("profile generation or digest does not match its contents")
        return cls(
            id=str(body["id"]),
            generation=generation,
            parent_generation=str(parent_generation) if parent_generation is not None else None,
            description=str(body["description"]),
            allowed_handlers=handlers,
            allowed_skills=skills,
            allowed_tools=tools,
            capabilities=capabilities,
            max_concurrency=max_concurrency,
            max_depth=max_depth,
            max_children=max_children,
            network_allowed=network_allowed,
            digest=digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "generation": self.generation,
            "parent_generation": self.parent_generation,
            "description": self.description,
            "allowed_handlers": sorted(self.allowed_handlers),
            "allowed_skills": sorted(self.allowed_skills),
            "allowed_tools": sorted(self.allowed_tools),
            "capabilities": sorted(self.capabilities),
            "max_concurrency": self.max_concurrency,
            "max_depth": self.max_depth,
            "max_children": self.max_children,
            "network_allowed": self.network_allowed,
            "digest": self.digest,
        }
