from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from aegisrun.runtime.fake_model import FakeAction


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0
    max_retries: int = 2


class OpenAICompatibleModel:
    """Small, provider-neutral adapter; the deterministic demo never requires it."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )

    async def next_action(self, phase: str, visible_tools: list[dict[str, Any]]) -> FakeAction:
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose one authorized next action. Return JSON with name, "
                        "tool_name and arguments. Never invent unavailable tools."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"phase": phase, "tools": visible_tools}),
                },
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.client.post("chat/completions", json=payload)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                return FakeAction(
                    name=str(data["name"]),
                    tool_name=data.get("tool_name"),
                    arguments=data.get("arguments"),
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                if attempt == self.config.max_retries:
                    raise
        raise RuntimeError("model request failed") from last_error

    async def close(self) -> None:
        await self.client.aclose()
