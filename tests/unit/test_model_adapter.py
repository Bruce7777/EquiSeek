from __future__ import annotations

import json

import httpx
import pytest

from aegisrun.runtime.model_adapter import OpenAICompatibleConfig, OpenAICompatibleModel


@pytest.mark.asyncio
async def test_openai_compatible_adapter_parses_action() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        content = json.dumps({"name": "read_source", "tool_name": "read_file", "arguments": {}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    model = OpenAICompatibleModel(
        OpenAICompatibleConfig("https://model.example/v1", "secret", "model"),
        httpx.MockTransport(handler),
    )
    action = await model.next_action("tests_failed", [])
    await model.close()
    assert action.tool_name == "read_file"


@pytest.mark.asyncio
async def test_openai_compatible_adapter_sends_bearer_token() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"name":"finish"}'}}]},
        )

    model = OpenAICompatibleModel(
        OpenAICompatibleConfig("https://model.example/v1", "secret", "model"),
        httpx.MockTransport(handler),
    )
    await model.next_action("verified", [])
    await model.close()
    assert seen == ["Bearer secret"]
