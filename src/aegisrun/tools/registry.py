from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from aegisrun.core.domain import PolicySnapshot
from aegisrun.core.errors import PolicyDeniedError
from aegisrun.tools.spec import ToolHandler, ToolResult, ToolSpec


@dataclass(slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> Callable[[], None]:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        registered = RegisteredTool(spec=spec, handler=handler)
        self._tools[spec.name] = registered
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._tools.get(spec.name) is registered:
                del self._tools[spec.name]
            active = False

        return dispose

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise PolicyDeniedError(f"unknown tool: {name}") from error

    def visible_specs(self, policy: PolicySnapshot) -> list[ToolSpec]:
        return [self._tools[name].spec for name in policy.allowed_tools if name in self._tools]

    def validate(
        self,
        name: str,
        arguments: dict[str, Any],
        policy: PolicySnapshot,
    ) -> RegisteredTool:
        if name not in policy.allowed_tools:
            raise PolicyDeniedError(f"tool is not allowed: {name}")
        registered = self.get(name)
        errors = sorted(
            Draft202012Validator(registered.spec.input_schema).iter_errors(arguments),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(errors[0].message)
        return registered

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        policy: PolicySnapshot,
    ) -> ToolResult:
        registered = self.validate(name, arguments, policy)
        return await registered.handler(arguments)
