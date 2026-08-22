from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aegisrun.core.security import canonical_hash

VARIABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
VARIABLE_TOKEN = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
RUNTIME_CONTEXT_PREAMBLE = (
    "Current runtime context. This snapshot supersedes earlier runtime-context snapshots."
)
RUNTIME_CONTEXT_CLEARED = (
    "Current runtime context: none. Earlier runtime-context snapshots no longer apply."
)


class PromptAssemblyError(ValueError):
    """A prompt composition is ambiguous, malformed, or incomplete."""


PromptTextProvider = str | Callable[[Mapping[str, str | None]], str]
VariableProvider = str | None | Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class PromptSection:
    name: str
    order: float
    text: PromptTextProvider
    source: str
    complete: bool = False


@dataclass(frozen=True, slots=True)
class PromptContext:
    name: str
    order: float
    text: PromptTextProvider
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedPromptSection:
    name: str
    order: float
    text: str
    source: str
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "order": self.order,
            "text": self.text,
            "source": self.source,
            "complete": self.complete,
            "sha256": canonical_hash(self.text),
        }


@dataclass(frozen=True, slots=True)
class ResolvedPromptContext:
    name: str
    order: float
    text: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "order": self.order,
            "text": self.text,
            "source": self.source,
            "sha256": canonical_hash(self.text),
        }


@dataclass(frozen=True, slots=True)
class ToolSchemaSnapshot:
    name: str
    source: str
    schema_json: str
    sha256: str

    @classmethod
    def create(cls, schema: Mapping[str, Any], *, source: str) -> ToolSchemaSnapshot:
        try:
            encoded = json.dumps(
                dict(schema),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            snapshot = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise PromptAssemblyError("tool schema must be lossless JSON") from error
        if not isinstance(snapshot, dict):
            raise PromptAssemblyError("tool schema must be an object")
        name = snapshot.get("name")
        if not isinstance(name, str) or not name.strip():
            raise PromptAssemblyError("tool schema requires a non-empty name")
        return cls(name, source, encoded, canonical_hash(snapshot))

    def to_dict(self) -> dict[str, Any]:
        schema = json.loads(self.schema_json)
        return {"name": self.name, "source": self.source, "sha256": self.sha256, "schema": schema}

    def request_schema(self) -> dict[str, Any]:
        value = json.loads(self.schema_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor enforces this
            raise PromptAssemblyError("tool schema snapshot is corrupt")
        return value


@dataclass(frozen=True, slots=True)
class PromptAssembly:
    sections: tuple[ResolvedPromptSection, ...]
    contexts: tuple[ResolvedPromptContext, ...]
    tools: tuple[ToolSchemaSnapshot, ...]
    variables: tuple[tuple[str, str | None], ...]
    system: str | None
    runtime_context: str | None
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": [section.to_dict() for section in self.sections],
            "contexts": [context.to_dict() for context in self.contexts],
            "tools": [tool.to_dict() for tool in self.tools],
            "variables": dict(self.variables),
            "system": self.system,
            "runtime_context": self.runtime_context,
            "sha256": self.sha256,
        }


class _Registration:
    def __init__(self, dispose: Callable[[], None]) -> None:
        self._dispose = dispose
        self._active = True

    def dispose(self) -> None:
        if self._active:
            self._active = False
            self._dispose()

    def __enter__(self) -> _Registration:
        return self

    def __exit__(self, *_: object) -> None:
        self.dispose()


class PromptRegistry:
    """Layered, reversible prompt composition for one immutable agent generation."""

    def __init__(self, parent: PromptRegistry | None = None) -> None:
        self.parent = parent
        self._sections: dict[str, PromptSection] = {}
        self._contexts: dict[str, PromptContext] = {}
        self._tools: dict[str, ToolSchemaSnapshot] = {}
        self._variables: dict[str, VariableProvider] = {}
        self._runtime_context_suppressors = 0
        self._closed = False

    def child(self) -> PromptRegistry:
        self._ensure_open()
        return PromptRegistry(self)

    def _ensure_open(self) -> None:
        if self._closed:
            raise PromptAssemblyError("prompt registry is closed")

    def _register(self, values: dict[str, Any], name: str, value: Any) -> _Registration:
        self._ensure_open()
        if not name.strip() or name in values:
            raise PromptAssemblyError(f'duplicate or empty prompt contribution: "{name}"')
        values[name] = value

        def dispose() -> None:
            if values.get(name) is value:
                del values[name]

        return _Registration(dispose)

    def section(self, section: PromptSection) -> _Registration:
        if not isinstance(section.order, int | float) or not math.isfinite(section.order):
            raise PromptAssemblyError(f'prompt section "{section.name}" order must be finite')
        return self._register(self._sections, section.name, section)

    def context(self, context: PromptContext) -> _Registration:
        if not isinstance(context.order, int | float) or not math.isfinite(context.order):
            raise PromptAssemblyError(f'prompt context "{context.name}" order must be finite')
        return self._register(self._contexts, context.name, context)

    def variable(self, name: str, provider: VariableProvider) -> _Registration:
        if not VARIABLE_NAME.fullmatch(name):
            raise PromptAssemblyError(f'invalid prompt variable name: "{name}"')
        return self._register(self._variables, name, provider)

    def tool(self, schema: Mapping[str, Any], *, source: str) -> _Registration:
        snapshot = ToolSchemaSnapshot.create(schema, source=source)
        return self._register(self._tools, snapshot.name, snapshot)

    def suppress_runtime_context(self) -> _Registration:
        self._ensure_open()
        self._runtime_context_suppressors += 1

        def dispose() -> None:
            self._runtime_context_suppressors = max(0, self._runtime_context_suppressors - 1)

        return _Registration(dispose)

    def _layers(self) -> tuple[PromptRegistry, ...]:
        layers: list[PromptRegistry] = []
        current: PromptRegistry | None = self
        while current is not None:
            current._ensure_open()
            layers.append(current)
            current = current.parent
        return tuple(reversed(layers))

    @staticmethod
    def _resolve_text(provider: PromptTextProvider, variables: Mapping[str, str | None]) -> str:
        text = provider(variables) if callable(provider) else provider
        if not isinstance(text, str):
            raise PromptAssemblyError("prompt text provider must return a string")
        return _interpolate(text, variables)

    def assemble(
        self, *, variable_values: Mapping[str, str | None] | None = None
    ) -> PromptAssembly:
        layers = self._layers()
        section_map: dict[str, PromptSection] = {}
        context_map: dict[str, PromptContext] = {}
        tool_map: dict[str, ToolSchemaSnapshot] = {}
        variable_map: dict[str, VariableProvider] = {}
        for layer in layers:
            section_map.update(layer._sections)
            context_map.update(layer._contexts)
            tool_map.update(layer._tools)
            variable_map.update(layer._variables)
        overrides = dict(variable_values or {})
        unknown = set(overrides) - set(variable_map)
        if unknown:
            raise PromptAssemblyError(f"unknown prompt variable overrides: {sorted(unknown)}")
        resolved_variables: dict[str, str | None] = {}
        for name, provider in variable_map.items():
            value = provider() if callable(provider) else provider
            if value is not None and not isinstance(value, str):
                raise PromptAssemblyError(f'prompt variable "{name}" must resolve to a string')
            resolved_variables[name] = overrides.get(name, value)

        sections = tuple(
            ResolvedPromptSection(
                section.name,
                section.order,
                self._resolve_text(section.text, resolved_variables),
                section.source,
                section.complete,
            )
            for section in sorted(section_map.values(), key=lambda item: (item.order, item.name))
        )
        complete = tuple(section for section in sections if section.complete)
        if len(complete) > 1:
            raise PromptAssemblyError("multiple complete system prompt sections are active")
        effective_sections = complete or tuple(section for section in sections if section.text)
        system = "\n\n".join(section.text for section in effective_sections) or None

        suppressed = any(layer._runtime_context_suppressors for layer in layers)
        resolved_contexts: list[ResolvedPromptContext] = []
        if not suppressed:
            for context in sorted(context_map.values(), key=lambda item: (item.order, item.name)):
                text = self._resolve_text(context.text, resolved_variables)
                if text:
                    resolved_contexts.append(
                        ResolvedPromptContext(
                            context.name,
                            context.order,
                            text,
                            context.source,
                        )
                    )
        contexts = tuple(resolved_contexts)
        context_body = "\n\n".join(context.text for context in contexts)
        runtime_context = f"{RUNTIME_CONTEXT_PREAMBLE}\n\n{context_body}" if context_body else None
        tools = tuple(tool_map[name] for name in sorted(tool_map))
        variables = tuple(sorted(resolved_variables.items()))
        material = {
            "sections": [section.to_dict() for section in effective_sections],
            "contexts": [context.to_dict() for context in contexts],
            "tools": [tool.to_dict() for tool in tools],
            "variables": dict(variables),
            "system": system,
            "runtime_context": runtime_context,
        }
        return PromptAssembly(
            effective_sections,
            contexts,
            tools,
            variables,
            system,
            runtime_context,
            canonical_hash(material),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._sections.clear()
        self._contexts.clear()
        self._tools.clear()
        self._variables.clear()
        self._runtime_context_suppressors = 0
        self._closed = True


def _interpolate(text: str, variables: Mapping[str, str | None]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise PromptAssemblyError(f'unknown prompt variable "{name}"')
        value = variables[name]
        if value is None:
            raise PromptAssemblyError(f'prompt variable "{name}" is undefined')
        return value

    rendered = VARIABLE_TOKEN.sub(replace, text)
    if "{{" in rendered or "}}" in rendered:
        raise PromptAssemblyError("malformed prompt variable expression")
    return rendered
