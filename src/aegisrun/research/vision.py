from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from aegisrun.research.deepseek import ModelServiceError

DEFAULT_VISION_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_VISION_MODEL = "deepseek-ai/deepseek-vl2"


@dataclass(frozen=True, slots=True)
class VisionConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_VISION_BASE_URL
    model: str = DEFAULT_VISION_MODEL
    timeout_seconds: float = 90.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        if parsed.scheme != "https" and not local_http:
            raise ValueError("视觉模型 API 地址必须使用 HTTPS（本机 localhost 除外）")
        if not parsed.netloc or not self.model.strip() or len(self.model) > 160:
            raise ValueError("视觉模型配置无效")


class OpenAICompatibleVisionClient:
    """Bounded image-understanding client for an OpenAI-compatible multimodal route."""

    def __init__(
        self,
        config: VisionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.api_key:
            raise ValueError("视觉模型 API Key 不能为空")
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )

    async def describe(self, data: bytes, mime_type: str, prompt: str) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        request = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": prompt[:2_000]},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 1_200,
        }
        try:
            response = await self._client.post("chat/completions", json=request)
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
        except httpx.HTTPStatusError as error:
            detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏]", error.response.text)[:200]
            raise ModelServiceError(
                f"视觉模型返回 HTTP {error.response.status_code}：{detail}"
            ) from error
        except httpx.HTTPError as error:
            raise ModelServiceError(f"视觉模型网络请求失败：{type(error).__name__}") from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelServiceError("视觉模型返回结构无法解析") from error
        if not content:
            raise ModelServiceError("视觉模型返回内容为空")
        return content[:12_000]

    async def close(self) -> None:
        await self._client.aclose()
