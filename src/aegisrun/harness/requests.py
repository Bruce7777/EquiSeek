from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aegisrun.core.security import canonical_hash
from aegisrun.harness.prompt import PromptAssembly

SECRET_FIELD_NAMES = frozenset(
    {
        "authorization",
        "api_key",
        "api-key",
        "access_token",
        "access-token",
        "password",
        "secret",
        "tushare_token",
    }
)


def _json_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be lossless JSON") from error
    if not isinstance(result, dict):  # pragma: no cover - dict(value) guarantees an object
        raise ValueError(f"{label} must be an object")
    return result


def _json_messages(messages: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    value = _json_object({"messages": [dict(message) for message in messages]}, "messages")
    raw = value["messages"]
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("messages must be a list of objects")
    return tuple(dict(item) for item in raw)


def _reject_credential_fields(value: Any, path: str = "request") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in SECRET_FIELD_NAMES:
                raise ValueError(f"credential field must not enter model envelope: {path}.{key}")
            _reject_credential_fields(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_credential_fields(item, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ModelRequestEnvelope:
    provider: str
    model: str
    prompt: PromptAssembly
    messages: tuple[dict[str, Any], ...]
    effective_config: dict[str, Any]
    defaults: dict[str, Any]
    request_body: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        model: str,
        prompt: PromptAssembly,
        messages: Sequence[Mapping[str, Any]],
        effective_config: Mapping[str, Any],
        defaults: Mapping[str, Any],
        request_body: Mapping[str, Any],
    ) -> ModelRequestEnvelope:
        if not provider.strip() or not model.strip():
            raise ValueError("model provider and model are required")
        message_snapshot = _json_messages(messages)
        request_snapshot = _json_object(request_body, "model request body")
        _reject_credential_fields(message_snapshot, "messages")
        _reject_credential_fields(request_snapshot)
        return cls(
            provider,
            model,
            prompt,
            message_snapshot,
            _json_object(effective_config, "effective model config"),
            _json_object(defaults, "model defaults"),
            request_snapshot,
        )

    def header_payload(
        self,
        request_id: str,
        *,
        credential_ref: str,
        surface_event_seqs: Sequence[int],
        reason: str = "initial",
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "reason": reason,
            "provider": self.provider,
            "model": self.model,
            "system": self.prompt.system,
            "tools": [tool.request_schema() for tool in self.prompt.tools],
            "effective_config": dict(self.effective_config),
            "defaults": dict(self.defaults),
            "prompt": self.prompt.to_dict(),
            "prompt_sha256": self.prompt.sha256,
            "messages_sha256": canonical_hash(list(self.messages)),
            "request_sha256": canonical_hash(self.request_body),
            "surface_event_seqs": list(surface_event_seqs),
            "credential_ref": credential_ref,
        }

    def model_request_payload(
        self,
        request_id: str,
        *,
        credential_ref: str,
        header_seq: int,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "provider": self.provider,
            "model": self.model,
            "request": dict(self.request_body),
            "request_sha256": canonical_hash(self.request_body),
            "header_seq": header_seq,
            "credential_ref": credential_ref,
        }
