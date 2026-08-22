from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from aegisrun.harness.prompt import PromptContext, PromptRegistry, PromptSection
from aegisrun.harness.requests import ModelRequestEnvelope
from aegisrun.research.guardrails import InvestmentOutputGuard, UnsafeAdviceError

DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
DEEPSEEK_V4_PRO = "deepseek-v4-pro"
DEEPSEEK_V4_FLASH_VISION_EXP = "deepseek-v4-flash-vision-exp"
DEFAULT_DEEPSEEK_MODEL = DEEPSEEK_V4_FLASH
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_OFFICIAL_PROVIDER = "deepseek-official"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
DEEPSEEK_MODEL_OPTIONS = (
    ("DeepSeek V4 Flash · 快速经济", DEEPSEEK_V4_FLASH),
    ("DeepSeek V4 Pro · 复杂分析", DEEPSEEK_V4_PRO),
    ("V4 Flash Vision Exp · 自定义端点实验模型", DEEPSEEK_V4_FLASH_VISION_EXP),
)
DEEPSEEK_OFFICIAL_MODEL_OPTIONS = DEEPSEEK_MODEL_OPTIONS[:2]
_DEEPSEEK_MODEL_LABELS = {
    DEEPSEEK_V4_FLASH: "DeepSeek V4 Flash",
    DEEPSEEK_V4_PRO: "DeepSeek V4 Pro",
    DEEPSEEK_V4_FLASH_VISION_EXP: "DeepSeek V4 Flash Vision Exp",
}


def normalize_deepseek_model(value: object) -> str:
    model = str(value or "").strip()
    return model if model in _DEEPSEEK_MODEL_LABELS else DEFAULT_DEEPSEEK_MODEL


def deepseek_model_label(model: object) -> str:
    value = str(model or "").strip() or DEFAULT_DEEPSEEK_MODEL
    return _DEEPSEEK_MODEL_LABELS.get(value, value)


def deepseek_model_supports_vision(model: object) -> bool:
    return normalize_deepseek_model(model) == DEEPSEEK_V4_FLASH_VISION_EXP


def normalize_model_provider(value: object) -> str:
    provider = str(value or "").strip()
    return (
        provider
        if provider in {DEEPSEEK_OFFICIAL_PROVIDER, OPENAI_COMPATIBLE_PROVIDER}
        else DEEPSEEK_OFFICIAL_PROVIDER
    )


def normalize_model_base_url(value: object, provider: object) -> str:
    selected_provider = normalize_model_provider(provider)
    if selected_provider == DEEPSEEK_OFFICIAL_PROVIDER:
        return DEFAULT_DEEPSEEK_BASE_URL
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (parsed.scheme != "https" and not local_http) or not parsed.netloc:
        raise ValueError("模型 API 地址必须使用 HTTPS（本机 localhost 除外）")
    return base_url


HARNESS_IDENTITY = (
    "你是求衡（EquiSeek）的本地股票研究智能体，"
    "只能使用本次请求明确提供的事实和能力。"
)
PRODUCT_BOUNDARY = (
    "区分历史行情事实、确定性指标计算、模型推断和面向用户的结论；不得执行交易，"
    "不得承诺收益，不得编造价格、日期、公告、指标数值或数据覆盖范围。"
)
RESEARCH_PERSONA = (
    "你是由 {{model}} 提供语言能力的证券规则决策解释助手。"
    "只解释输入 JSON 中已经存在的历史数值、公式关系和本地规则引擎结论。"
)
EVIDENCE_POLICY = (
    "任何重要事实都必须能追溯到模型可见的事实快照。数据不完整、过期、模拟或来源"
    "不一致时，必须先说明限制；不得静默混用不同复权口径。"
)
INDICATOR_POLICY = (
    "技术指标由确定性本地指标引擎计算，不得由语言模型估算。解释指标时保留参数、"
    "复权方式、预热状态、缺失值策略、来源日期范围和截止交易日；信号不代表必然预测。"
)
SIGNAL_POLICY = (
    "多周期 MACD/WR 结构、背离、双峰、买入/卖出/持有/减仓结论与 5/10/20 日方向情景"
    "以及个股/大盘/板块共振门控均由本地规则引擎给出。可以清楚复述并解释这些既有结论，"
    "但不得自行改变动作、优先级、概率、价格区间、失效条件或补充输入中没有的数字。"
)
OUTPUT_POLICY = (
    "用简体中文，按投资结论、市场共振、MACD 大方向、WR 时机、风险与失效条件五段输出；第一段"
    "必须直接复述本地规则动作。允许复述输入中的方向预测与统计，但不得添加输入中没有"
    "的数字，不得承诺收益、使用必涨必跌等绝对表达，也不得声称已代用户执行交易。"
)


def research_prompt(payload: dict[str, Any], model: str) -> PromptRegistry:
    registry = PromptRegistry()
    registry.variable("model", model)
    registry.section(PromptSection("aegisrun:identity", -100, HARNESS_IDENTITY, "core"))
    registry.section(PromptSection("product:boundary", -90, PRODUCT_BOUNDARY, "compliance"))
    registry.section(PromptSection("research:persona", 0, RESEARCH_PERSONA, "research"))
    registry.section(PromptSection("research:evidence-policy", 20, EVIDENCE_POLICY, "research"))
    registry.section(PromptSection("research:indicator-policy", 30, INDICATOR_POLICY, "indicators"))
    registry.section(PromptSection("research:signal-policy", 35, SIGNAL_POLICY, "signals"))
    registry.section(PromptSection("output:research", 190, OUTPUT_POLICY, "compliance"))
    source = str(payload.get("source", "unknown"))
    as_of = str(payload.get("as_of", "unknown"))
    adjustment = str(payload.get("adjustment", "unknown"))
    registry.context(
        PromptContext(
            "market-data:coverage",
            10,
            f"本次研究数据源：{source}；截止交易日：{as_of}；复权方式：{adjustment}。",
            "marketdata",
        )
    )
    registry.context(
        PromptContext(
            "compliance:mode",
            20,
            "当前模式输出可回测的规则型研究建议，但不保证收益、不做适当性判断，也不具备交易权限。",
            "compliance",
        )
    )
    return registry


class ModelServiceError(RuntimeError):
    """Expected remote-model failure that permits deterministic local fallback."""


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = DEFAULT_DEEPSEEK_MODEL
    provider: str = DEEPSEEK_OFFICIAL_PROVIDER
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.model not in _DEEPSEEK_MODEL_LABELS:
            raise ValueError(f"不支持的 DeepSeek 模型：{self.model}")
        object.__setattr__(self, "provider", normalize_model_provider(self.provider))
        if (
            self.provider == DEEPSEEK_OFFICIAL_PROVIDER
            and self.model == DEEPSEEK_V4_FLASH_VISION_EXP
        ):
            raise ValueError(
                "DeepSeek 官方 API 当前只公布 V4 Pro 和 V4 Flash；"
                "Vision Exp 需选择实际提供该模型的自定义兼容端点"
            )
        object.__setattr__(
            self,
            "base_url",
            normalize_model_base_url(self.base_url, self.provider),
        )


class DeepSeekClient:
    def __init__(
        self, config: DeepSeekConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not config.api_key:
            raise ValueError("DeepSeek API Key 不能为空")
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )

    async def summarize(self, payload: dict[str, Any]) -> str:
        return await self.summarize_prepared(self.request_envelope(payload))

    async def summarize_prepared(self, envelope: ModelRequestEnvelope) -> str:
        try:
            response = await self._client.post(
                "chat/completions",
                json=envelope.request_body,
            )
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
        except httpx.HTTPStatusError as error:
            raise self._http_error(error.response) from error
        except httpx.HTTPError as error:
            raise ModelServiceError(
                f"DeepSeek 网络请求失败（{type(error).__name__}），请检查网络后重试"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelServiceError(
                f"DeepSeek 返回了无法解析的响应（{type(error).__name__}）"
            ) from error
        if not content:
            raise ModelServiceError("DeepSeek 返回内容为空")
        try:
            InvestmentOutputGuard().ensure_safe(content)
        except UnsafeAdviceError as error:
            # A remote wording violation must not discard the deterministic local
            # decision. The pipeline treats this as a recoverable model fallback.
            raise ModelServiceError(
                "DeepSeek 输出未通过投资输出护栏，已改用本地规则结论"
            ) from error
        return content

    async def complete_json(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1_000,
    ) -> dict[str, Any]:
        """Return a schema-neutral JSON object for bounded Agent planning.

        This method deliberately does not apply the final investment wording guard:
        intermediate planner actions are machine-readable control data. Tool schemas,
        policy checks and the final output guard are enforced by the caller.
        """

        request = {
            "model": self.config.model,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        try:
            response = await self._client.post("chat/completions", json=request)
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            value = json.loads(content)
        except httpx.HTTPStatusError as error:
            raise self._http_error(error.response) from error
        except httpx.HTTPError as error:
            raise ModelServiceError(
                f"DeepSeek 网络请求失败（{type(error).__name__}），请检查网络后重试"
            ) from error
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ModelServiceError("DeepSeek 返回了无法解析的 Agent JSON") from error
        if not isinstance(value, dict):
            raise ModelServiceError("DeepSeek Agent JSON 必须是对象")
        return {str(key): item for key, item in value.items()}

    async def verify_connection(self) -> str:
        """Verify the credential and configured model without creating a completion."""

        try:
            response = await self._client.get("models")
            response.raise_for_status()
            payload = response.json()
            models = {
                str(item["id"])
                for item in payload["data"]
                if isinstance(item, dict) and item.get("id")
            }
        except httpx.HTTPStatusError as error:
            raise self._http_error(error.response) from error
        except httpx.HTTPError as error:
            raise ModelServiceError(
                f"DeepSeek 网络请求失败（{type(error).__name__}），请检查网络后重试"
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise ModelServiceError(
                f"DeepSeek 返回了无法解析的模型列表（{type(error).__name__}）"
            ) from error
        if self.config.model not in models:
            raise ModelServiceError(f"DeepSeek 当前账户未返回模型 {self.config.model}")
        return self.config.model

    @staticmethod
    def _http_error(response: httpx.Response) -> ModelServiceError:
        detail = ""
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("code") or "")
        except (AttributeError, TypeError, ValueError):
            detail = ""
        detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏]", detail)
        detail = re.sub(r"\s+", " ", detail).strip()[:240]
        suffix = f"：{detail}" if detail else ""
        return ModelServiceError(f"DeepSeek API 返回 HTTP {response.status_code}{suffix}")

    def request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the exact credential-free model-visible request body."""

        return self.request_envelope(payload).request_body

    def request_envelope(self, payload: dict[str, Any]) -> ModelRequestEnvelope:
        prompt_registry = research_prompt(payload, self.config.model)
        prompt = prompt_registry.assemble()
        messages: list[dict[str, Any]] = []
        if prompt.runtime_context is not None:
            messages.append({"role": "user", "content": prompt.runtime_context})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        )
        request = {
            "model": self.config.model,
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": prompt.system},
                *messages,
            ],
        }
        return ModelRequestEnvelope.create(
            provider=self.config.provider,
            model=self.config.model,
            prompt=prompt,
            messages=messages,
            effective_config={
                "thinking": "disabled",
                "temperature": 0.1,
                "max_tokens": 900,
            },
            defaults={},
            request_body=request,
        )

    async def close(self) -> None:
        await self._client.aclose()
