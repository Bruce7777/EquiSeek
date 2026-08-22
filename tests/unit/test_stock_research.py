from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from aegisrun.marketdata.indicators import calculate_indicators
from aegisrun.marketdata.models import AdjustmentMode, MarketDataSet
from aegisrun.marketdata.providers import DemoMarketDataProvider
from aegisrun.portfolio.models import Position
from aegisrun.research.deepseek import (
    DEEPSEEK_V4_FLASH,
    DEEPSEEK_V4_FLASH_VISION_EXP,
    DEEPSEEK_V4_PRO,
    OPENAI_COMPATIBLE_PROVIDER,
    DeepSeekClient,
    DeepSeekConfig,
    ModelServiceError,
    deepseek_model_supports_vision,
)
from aegisrun.research.guardrails import AdviceGuard, InvestmentOutputGuard, UnsafeAdviceError
from aegisrun.research.objective import build_objective_summary, build_research_snapshot
from aegisrun.research.paths import default_research_workspace_root
from aegisrun.research.pipeline import research_plan
from aegisrun.research.service import run_research
from aegisrun.workspace.manager import WorkspaceManager


class SafeSummaryModel:
    async def summarize(self, payload: dict[str, object]) -> str:
        assert payload["symbol"] == "600519.SH"
        return "仅陈述输入中的历史指标关系；不构成投资建议。"


class UnsafeSummaryModel:
    async def summarize(self, payload: dict[str, object]) -> str:
        del payload
        raise RuntimeError("unexpected application defect")


class UnavailableSummaryModel:
    async def summarize(self, payload: dict[str, object]) -> str:
        del payload
        raise ModelServiceError("service unavailable")


def snapshot() -> tuple[MarketDataSet, object]:
    data = DemoMarketDataProvider().fetch_daily(
        "600519.SH", date(2025, 1, 1), date(2026, 1, 1), AdjustmentMode.QFQ
    )
    return data, calculate_indicators(data.bars)


def test_objective_summary_is_traceable_and_non_predictive() -> None:
    data, indicators = snapshot()
    research = build_research_snapshot(data, indicators)
    summary = build_objective_summary(research)

    assert "synthetic-demo" in summary
    assert research.as_of.isoformat() in summary
    assert summary.startswith("## 数据快照\n")
    assert "\n## 历史指标事实\n" in summary
    assert "\n## 数据与方法提示\n" in summary
    assert "不预测未来价格" in summary
    AdviceGuard().ensure_safe(summary, allow_disclaimer=True)


def test_snapshot_exposes_cache_lineage_without_local_cache_path() -> None:
    data, indicators = snapshot()
    cached = MarketDataSet(
        symbol=data.symbol,
        source="baostock",
        adjustment=data.adjustment,
        bars=data.bars,
        fetched_at=data.fetched_at,
        warnings=("公开历史数据",),
        cache_status="partial",
        cache_hit_bars=180,
        cache_added_bars=5,
        network_rows=6,
        fetch_ranges=("2026-01-01..2026-01-08",),
        cache_path="/private/local/market-data.sqlite3",
    )

    research = build_research_snapshot(cached, indicators)
    prompt = research.to_prompt_payload()
    summary = build_objective_summary(research)

    assert prompt["source"] == "baostock"
    assert prompt["adjustment"] == "qfq"
    assert prompt["cache_status"] == "partial"
    assert prompt["cache_hit_bars"] == 180
    assert "cache_path" not in prompt
    assert "/private/local" not in str(prompt)
    assert "增量补齐，命中 180 根、新增 5 根" in summary


def test_deepseek_config_does_not_reveal_api_key() -> None:
    config = DeepSeekConfig(api_key="secret-key")

    assert "secret-key" not in repr(config)


def test_deepseek_config_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="不支持的 DeepSeek 模型"):
        DeepSeekConfig(api_key="secret-key", model="deepseek-unknown")


def test_deepseek_v4_catalog_has_pro_flash_and_multimodal_flash() -> None:
    assert deepseek_model_supports_vision(DEEPSEEK_V4_FLASH_VISION_EXP) is True
    assert deepseek_model_supports_vision(DEEPSEEK_V4_FLASH) is False
    assert deepseek_model_supports_vision(DEEPSEEK_V4_PRO) is False


def test_official_provider_rejects_unpublished_vision_model() -> None:
    with pytest.raises(ValueError, match="官方 API 当前只公布"):
        DeepSeekConfig(api_key="official-secret", model=DEEPSEEK_V4_FLASH_VISION_EXP)

    custom = DeepSeekConfig(
        api_key="custom-secret",
        model=DEEPSEEK_V4_FLASH_VISION_EXP,
        provider=OPENAI_COMPATIBLE_PROVIDER,
        base_url="https://vision.example.com/v1",
    )
    assert custom.model == DEEPSEEK_V4_FLASH_VISION_EXP


def test_custom_compatible_provider_keeps_https_endpoint_and_official_forces_default() -> None:
    custom = DeepSeekConfig(
        api_key="custom-secret",
        model=DEEPSEEK_V4_FLASH,
        provider=OPENAI_COMPATIBLE_PROVIDER,
        base_url="https://models.example.com/v1/",
    )
    official = DeepSeekConfig(
        api_key="official-secret",
        model=DEEPSEEK_V4_FLASH,
        base_url="https://untrusted.example.com/v1",
    )

    assert custom.base_url == "https://models.example.com/v1"
    assert official.base_url == "https://api.deepseek.com"


def test_default_research_workspace_is_user_writable_and_not_cwd(
    tmp_path, monkeypatch: object
) -> None:
    configured = tmp_path / "desktop-workspaces"
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "EQUISEEK_RESEARCH_WORKSPACE_ROOT", str(configured)
    )
    monkeypatch.chdir("/")  # type: ignore[attr-defined]

    assert default_research_workspace_root() == configured


@pytest.mark.parametrize(
    "text",
    [
        "建议买入这只股票",
        "目标价为120元",
        "可以使用三成仓位",
        "设置止损位在90元",
        "未来上涨概率很高",
        "这是一只稳赚的股票",
    ],
)
def test_advice_guard_blocks_recommendations(text: str) -> None:
    with pytest.raises(UnsafeAdviceError):
        AdviceGuard().ensure_safe(text)


@pytest.mark.asyncio
async def test_deepseek_v4_flash_uses_official_chat_api() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "仅陈述历史指标，不预测未来价格。"}}]},
        )

    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret-key"), transport=httpx.MockTransport(handler)
    )
    result = await client.summarize({"as_of": "2026-01-01", "facts": []})
    await client.close()

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer secret-key"
    assert '"model":"deepseek-v4-flash"' in str(captured["body"])
    assert result == "仅陈述历史指标，不预测未来价格。"


@pytest.mark.asyncio
async def test_deepseek_guard_violation_degrades_as_expected_model_failure() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "这只股票必涨并且保证收益。"}}]},
        )

    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret-key"), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ModelServiceError, match="已改用本地规则结论"):
            await client.summarize({"as_of": "2026-01-01", "facts": []})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deepseek_connection_verifies_configured_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]},
        )

    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret-key"), transport=httpx.MockTransport(handler)
    )
    try:
        model = await client.verify_connection()
    finally:
        await client.close()

    assert model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_deepseek_pro_is_sent_as_selected_model() -> None:
    client = DeepSeekClient(DeepSeekConfig(api_key="secret-key", model=DEEPSEEK_V4_PRO))
    try:
        payload = client.request_payload({"as_of": "2026-08-18", "facts": []})
    finally:
        await client.close()

    assert payload["model"] == DEEPSEEK_V4_PRO


def test_research_plan_names_the_selected_deepseek_model() -> None:
    plan = research_plan("selected-model-plan", use_model=True, model_name=DEEPSEEK_V4_PRO)

    assert plan.tasks["model"].spec.title == "DeepSeek V4 Pro 语言整理"


@pytest.mark.asyncio
async def test_deepseek_http_error_is_actionable_and_redacts_credentials() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication failed for sk-sensitive-value"}},
        )

    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret-key"), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ModelServiceError, match="HTTP 401") as captured:
            await client.verify_connection()
    finally:
        await client.close()

    message = str(captured.value)
    assert "Authentication failed" in message
    assert "sk-sensitive-value" not in message


@pytest.mark.asyncio
async def test_research_pipeline_persists_plan_and_isolated_subtask_outputs(tmp_path) -> None:
    progress: list[dict[str, object]] = []
    end = date(2026, 8, 11)

    result = await run_research(
        DemoMarketDataProvider(),
        "600519.SH",
        date(2025, 1, 1),
        end,
        AdjustmentMode.QFQ,
        workspace_root=tmp_path,
        run_id="research-test",
        on_progress=progress.append,
    )

    assert result.plan is not None
    assert result.plan["status"] == "succeeded"
    assert result.workspace == str(tmp_path / "research-test")
    assert progress[0]["status"] == "running"
    assert progress[-1]["status"] == "succeeded"
    assert progress[-1]["context"]["agent_runtime"]["mode"] == "local"
    assert progress[-1]["context"]["agent_runtime"]["event_sourced"] is True
    manager = WorkspaceManager(tmp_path)
    restored = manager.plan_store("research-test").load()
    assert restored.status.value == "succeeded"
    assert manager.describe("research-test")["task_ids"] == [
        "advice",
        "benchmark_market",
        "facts",
        "guardrail",
        "indicators",
        "market_data",
        "timing",
    ]
    for task_id in manager.describe("research-test")["task_ids"]:
        output = manager.create_task("research-test", task_id).output / "result.json"
        assert output.is_file()
    delegations = manager.paths("research-test").state / "delegations.json"
    assert delegations.is_file()
    assert {task["agent"] for task in result.plan["tasks"]} == {
        "market-data-agent",
        "indicator-agent",
        "market-context-agent",
        "timing-agent",
        "advice-agent",
        "evidence-agent",
        "compliance-agent",
    }
    event_path = manager.paths("research-test").state / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    event_types = [event["event_type"] for event in events]
    assert {
        "session/header",
        "plan/created",
        "skill/catalog",
        "market-data/fetched",
        "indicator/computed",
        "market-context/benchmark",
        "strategy/multi-timeframe",
        "investment/advice",
        "research/facts",
        "policy/decision",
        "plan/status",
    }.issubset(event_types)
    assert event_types.count("subagent/started") == 7
    assert event_types.count("subagent/ended") == 7
    assert result.investment_advice.action_label in {
        "买入",
        "加仓",
        "持有",
        "减仓",
        "卖出",
        "等待",
        "回避",
    }
    assert len(result.investment_advice.forecasts) == 3
    InvestmentOutputGuard().ensure_safe(result.deterministic_summary)
    assert result.plan["context"]["agent_runtime"]["event_count"] == len(events)


@pytest.mark.asyncio
async def test_real_deepseek_adapter_records_exact_request_without_secret(tmp_path) -> None:
    secret = "canary-deepseek-secret"
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "仅陈述历史指标，不预测未来价格。"}}]},
        )

    client = DeepSeekClient(DeepSeekConfig(api_key=secret), transport=httpx.MockTransport(handler))
    try:
        result = await run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            date(2025, 1, 1),
            date(2026, 1, 1),
            AdjustmentMode.QFQ,
            client,
            workspace_root=tmp_path,
            run_id="deepseek-event-audit",
        )
    finally:
        await client.close()

    assert result.model_summary == "仅陈述历史指标，不预测未来价格。"
    event_path = tmp_path / "deepseek-event-audit" / ".state" / "events.jsonl"
    event_text = event_path.read_text(encoding="utf-8")
    assert secret not in event_text
    events = [json.loads(line) for line in event_text.splitlines()]
    model_request = next(event for event in events if event["event_type"] == "model/request")
    request_header = next(event for event in events if event["event_type"] == "request/header")
    model_response = next(event for event in events if event["event_type"] == "model/response")
    payload = model_request["payload"]
    assert payload["credential_ref"] == "deepseek-api-key"
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["request"] == captured["body"]
    assert payload["header_seq"] == request_header["seq"]
    assert request_header["payload"]["request_id"] == payload["request_id"]
    assert request_header["payload"]["provider"] == "deepseek-official"
    assert request_header["payload"]["prompt_sha256"]
    assert request_header["payload"]["messages_sha256"]
    assert request_header["payload"]["request_sha256"] == payload["request_sha256"]
    assert request_header["payload"]["surface_event_seqs"]
    assert "EquiSeek" in request_header["payload"]["system"]
    assert "Authorization" not in json.dumps(payload, ensure_ascii=False)
    assert captured["authorization"] == f"Bearer {secret}"
    assert model_response["payload"]["content"] == result.model_summary


@pytest.mark.asyncio
async def test_research_progress_observer_cannot_break_analysis(tmp_path) -> None:
    def broken_observer(_: dict[str, object]) -> None:
        raise RuntimeError("observer failed")

    result = await run_research(
        DemoMarketDataProvider(),
        "000001.SZ",
        date(2025, 1, 1),
        date(2026, 1, 1),
        AdjustmentMode.BFQ,
        workspace_root=tmp_path,
        run_id="observer-failure",
        on_progress=broken_observer,
    )

    assert result.plan is not None
    assert result.plan["status"] == "succeeded"


@pytest.mark.asyncio
async def test_position_analysis_uses_portfolio_agent_without_persisting_private_fields(
    tmp_path,
) -> None:
    result = await run_research(
        DemoMarketDataProvider(),
        "600519.SH",
        date(2019, 1, 1),
        date(2026, 8, 11),
        AdjustmentMode.QFQ,
        workspace_root=tmp_path,
        run_id="position-research",
        position=Position("600519.SH", 12_345.67, 9_876.5432, notes="private-note"),
    )

    assert result.holding_assessment is not None
    assert result.holding_assessment.recommended_action_label
    assert result.plan is not None
    portfolio_task = next(task for task in result.plan["tasks"] if task["id"] == "portfolio")
    assert portfolio_task["agent"] == "portfolio-agent"
    assert portfolio_task["skills"] == ["portfolio-risk-monitor"]
    event_text = (tmp_path / "position-research" / ".state" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "private-note" not in event_text
    assert "quantity" not in event_text
    assert "cost_price" not in event_text


@pytest.mark.asyncio
async def test_research_pipeline_with_model_persists_only_bounded_metadata(tmp_path) -> None:
    result = await run_research(
        DemoMarketDataProvider(),
        "600519.SH",
        date(2025, 1, 1),
        date(2026, 1, 1),
        AdjustmentMode.QFQ,
        SafeSummaryModel(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        run_id="model-research",
    )

    assert result.model_summary
    assert result.plan is not None
    model_task = next(task for task in result.plan["tasks"] if task["id"] == "model")
    assert model_task["result"]["accepted"] is True
    assert "summary" not in model_task["result"]
    assert len(model_task["result"]["summary_sha256"]) == 64


@pytest.mark.asyncio
async def test_unexpected_model_defect_fails_plan_instead_of_being_hidden(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="application defect"):
        await run_research(
            DemoMarketDataProvider(),
            "600519.SH",
            date(2025, 1, 1),
            date(2026, 1, 1),
            AdjustmentMode.QFQ,
            UnsafeSummaryModel(),  # type: ignore[arg-type]
            workspace_root=tmp_path,
            run_id="model-defect",
        )

    restored = WorkspaceManager(tmp_path).plan_store("model-defect").load()
    assert restored.status.value == "failed"
    assert restored.tasks["model"].status.value == "failed"
    assert restored.tasks["guardrail"].status.value == "skipped"
    events = [
        json.loads(line)
        for line in (tmp_path / "model-defect" / ".state" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    model_events = [
        event["event_type"] for event in events if event["event_type"].startswith("model/")
    ]
    assert model_events == ["model/request", "model/failure"]
    failure = next(event for event in events if event["event_type"] == "model/failure")
    assert failure["payload"]["error_type"] == "RuntimeError"
    assert failure["payload"]["expected"] is False


@pytest.mark.asyncio
async def test_expected_model_service_failure_safely_degrades(tmp_path) -> None:
    result = await run_research(
        DemoMarketDataProvider(),
        "600519.SH",
        date(2025, 1, 1),
        date(2026, 1, 1),
        AdjustmentMode.QFQ,
        UnavailableSummaryModel(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        run_id="model-unavailable",
    )

    assert result.model_warning == "DeepSeek 语言整理未采用：service unavailable"
    assert result.plan is not None
    assert result.plan["status"] == "succeeded"
