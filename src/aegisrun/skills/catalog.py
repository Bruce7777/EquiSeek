from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_BYTES = 128 * 1024
MAX_RESOURCE_BYTES = 1024 * 1024
ALLOWED_FIELDS = {
    "name",
    "description",
    "version",
    "allowed-agents",
    "allowed-tools",
    "network-required",
    "resources",
    "disable-model-invocation",
    "user-invocable",
    # Common Agent Skill metadata. It is accepted for interoperability but does
    # not grant capabilities or change the local security policy.
    "author",
    "compatibility",
    "license",
    "metadata",
}


class SkillValidationError(ValueError):
    """A skill package is malformed or violates local activation policy."""


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str
    version: str
    package_root: Path
    allowed_agents: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    network_required: bool
    declared_resources: tuple[str, ...]
    manifest_sha256: str
    model_invocable: bool = True
    user_invocable: bool = True
    provider: str = "filesystem"
    rank: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "allowed_agents": list(self.allowed_agents),
            "allowed_tools": list(self.allowed_tools),
            "network_required": self.network_required,
            "declared_resources": list(self.declared_resources),
            "manifest_sha256": self.manifest_sha256,
            "model_invocable": self.model_invocable,
            "user_invocable": self.user_invocable,
            "provider": self.provider,
            "rank": self.rank,
        }


@dataclass(frozen=True, slots=True)
class SkillPackage:
    summary: SkillSummary
    instructions: str
    resources: dict[str, str]
    package_sha256: str

    def audit_dict(self) -> dict[str, Any]:
        value = self.summary.to_dict()
        value["package_sha256"] = self.package_sha256
        return value


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SkillValidationError(f"{field} must be a list of strings")
    normalized = tuple(item.strip() for item in value)
    if any(not item for item in normalized):
        raise SkillValidationError(f"{field} cannot contain empty values")
    return normalized


def _split_markdown(content: str) -> tuple[dict[str, Any], str]:
    # User-authored Skills and Git checkouts on Windows commonly use CRLF.
    # Parse a canonical newline form while keeping the original bytes for
    # mutation detection and package audit hashes.
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if not content.startswith("---\n"):
        raise SkillValidationError("SKILL.md must start with YAML frontmatter")
    end = content.find("\n---\n", 4)
    if end < 0:
        raise SkillValidationError("SKILL.md frontmatter is not closed")
    try:
        raw = yaml.safe_load(content[4:end])
    except yaml.YAMLError as error:
        raise SkillValidationError(f"invalid SKILL.md YAML: {error}") from error
    if not isinstance(raw, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a mapping")
    metadata = {str(key): value for key, value in raw.items()}
    unexpected = set(metadata) - ALLOWED_FIELDS
    if unexpected:
        raise SkillValidationError(f"unexpected SKILL.md fields: {sorted(unexpected)}")
    return metadata, content[end + 5 :].strip()


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise SkillValidationError(f"skill symlinks are not allowed: {path.name}")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SkillValidationError(f"skill file is unreadable: {path.name}") from error
    if size > limit:
        raise SkillValidationError(f"skill file exceeds {limit} bytes: {path.name}")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SkillValidationError(f"skill file is unreadable: {path.name}") from error
    if len(data) > limit:
        raise SkillValidationError(f"skill file exceeds {limit} bytes: {path.name}")
    return data


class SkillCatalog:
    """Discovers metadata first and loads full instructions only on activation."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        *,
        provider_name: str = "filesystem",
        rank: int = 100,
    ) -> None:
        if not provider_name.strip() or rank < 0:
            raise SkillValidationError("skill provider name and rank are invalid")
        resolved: list[Path] = []
        for root in roots:
            if root.is_symlink():
                raise SkillValidationError(f"skill root symlinks are not allowed: {root}")
            resolved.append(root.resolve())
        self.roots = tuple(resolved)
        self.provider_name = provider_name
        self.rank = rank
        self._summaries: dict[str, SkillSummary] = {}
        self.refresh()

    def refresh(self) -> tuple[SkillSummary, ...]:
        discovered: dict[str, SkillSummary] = {}
        for root in self.roots:
            if not root.exists():
                continue
            if not root.is_dir():
                raise SkillValidationError(f"skill root must be a real directory: {root}")
            for package_root in sorted(root.iterdir()):
                if package_root.is_symlink():
                    raise SkillValidationError(
                        f"skill package symlinks are not allowed: {package_root.name}"
                    )
                if not package_root.is_dir():
                    continue
                skill_file = package_root / "SKILL.md"
                if not skill_file.is_file():
                    continue
                content_bytes = _read_bounded(skill_file, MAX_SKILL_BYTES)
                try:
                    content = content_bytes.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise SkillValidationError("SKILL.md must be UTF-8") from error
                metadata, _ = _split_markdown(content)
                summary = self._summary(package_root, metadata, content_bytes)
                if summary.name in discovered:
                    raise SkillValidationError(f"duplicate skill name: {summary.name}")
                discovered[summary.name] = summary
        self._summaries = discovered
        return self.list()

    def _summary(
        self, package_root: Path, metadata: dict[str, Any], content_bytes: bytes
    ) -> SkillSummary:
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
            raise SkillValidationError("skill name must use lowercase hyphen-case")
        if name != package_root.name:
            raise SkillValidationError("skill directory must match its declared name")
        if not isinstance(description, str) or not description.strip():
            raise SkillValidationError("skill description must be a non-empty string")
        if len(description) > 1024 or "<" in description or ">" in description:
            raise SkillValidationError("skill description is invalid")
        version = metadata.get("version", "1.0.0")
        if not isinstance(version, str) or not version.strip():
            raise SkillValidationError("skill version must be a non-empty string")
        network_required = metadata.get("network-required", False)
        if not isinstance(network_required, bool):
            raise SkillValidationError("network-required must be a boolean")
        disable_model = metadata.get("disable-model-invocation", False)
        user_invocable = metadata.get("user-invocable", True)
        if not isinstance(disable_model, bool) or not isinstance(user_invocable, bool):
            raise SkillValidationError("skill invocation policy must use booleans")
        resources = _string_list(metadata.get("resources"), "resources")
        return SkillSummary(
            name=name,
            description=description.strip(),
            version=version.strip(),
            package_root=package_root.resolve(),
            allowed_agents=_string_list(metadata.get("allowed-agents"), "allowed-agents"),
            allowed_tools=_string_list(metadata.get("allowed-tools"), "allowed-tools"),
            network_required=network_required,
            declared_resources=resources,
            manifest_sha256=hashlib.sha256(content_bytes).hexdigest(),
            model_invocable=not disable_model,
            user_invocable=user_invocable,
            provider=self.provider_name,
            rank=self.rank,
        )

    def list(self) -> tuple[SkillSummary, ...]:
        return tuple(self._summaries[name] for name in sorted(self._summaries))

    def describe(self, name: str) -> SkillSummary:
        try:
            return self._summaries[name]
        except KeyError as error:
            raise SkillValidationError(f"unknown skill: {name}") from error

    def search(self, query: str, *, limit: int = 5) -> tuple[SkillSummary, ...]:
        if limit < 1:
            raise ValueError("skill search limit must be positive")
        tokens = tuple(token.casefold() for token in query.split() if token)
        scored: list[tuple[int, SkillSummary]] = []
        for summary in self._summaries.values():
            name = summary.name.casefold()
            text = f"{summary.name} {summary.description}".casefold()
            if tokens and not all(token in text for token in tokens):
                continue
            score = sum(2 if token in name else 1 for token in tokens)
            scored.append((score, summary))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return tuple(summary for _, summary in scored[:limit])

    def activate(
        self,
        name: str,
        *,
        agent: str,
        granted_tools: frozenset[str],
        network_allowed: bool,
    ) -> SkillPackage:
        summary = self.describe(name)
        if summary.allowed_agents and agent not in summary.allowed_agents:
            raise SkillValidationError(f"skill {name} is not allowed for agent {agent}")
        denied_tools = set(summary.allowed_tools) - granted_tools
        if denied_tools:
            raise SkillValidationError(
                f"skill {name} requests tools not granted to {agent}: {sorted(denied_tools)}"
            )
        if summary.network_required and not network_allowed:
            raise SkillValidationError(f"skill {name} requires network access")

        skill_file = summary.package_root / "SKILL.md"
        content_bytes = _read_bounded(skill_file, MAX_SKILL_BYTES)
        metadata, instructions = _split_markdown(content_bytes.decode("utf-8"))
        current = self._summary(summary.package_root, metadata, content_bytes)
        if current.manifest_sha256 != summary.manifest_sha256:
            raise SkillValidationError(f"skill changed after discovery: {name}")

        resources: dict[str, str] = {}
        digest = hashlib.sha256(content_bytes)
        total = len(content_bytes)
        for relative in summary.declared_resources:
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise SkillValidationError(f"unsafe skill resource path: {relative}")
            resource = summary.package_root / relative_path
            if not resource.is_file() or resource.is_symlink():
                raise SkillValidationError(f"skill resource is missing or unsafe: {relative}")
            resolved = resource.resolve()
            if not resolved.is_relative_to(summary.package_root):
                raise SkillValidationError(f"skill resource escapes package: {relative}")
            data = _read_bounded(resolved, MAX_RESOURCE_BYTES)
            total += len(data)
            if total > MAX_RESOURCE_BYTES:
                raise SkillValidationError("activated skill package exceeds size limit")
            try:
                resources[relative] = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SkillValidationError(f"skill resource must be UTF-8: {relative}") from error
            digest.update(relative.encode())
            digest.update(data)
        return SkillPackage(current, instructions, resources, digest.hexdigest())


def builtin_skill_catalog(*, custom_roots: tuple[Path, ...] = ()) -> SkillCatalog:
    builtins = Path(__file__).resolve().parents[1] / "builtin_skills"
    return SkillCatalog((builtins, *custom_roots))
