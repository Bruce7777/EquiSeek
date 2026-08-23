from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import aegisrun.sidecar.dispatcher as dispatcher_module
from aegisrun.sidecar.dispatcher import SidecarDispatcher
from aegisrun.sidecar.local_state import LocalSettingsStore, user_data_root
from aegisrun.sidecar.protocol import RpcRequest
from aegisrun.sidecar.runs import RunRegistry


def test_fresh_desktop_defaults_to_online_public_market_data(tmp_path) -> None:
    settings = LocalSettingsStore(tmp_path / "settings.json").load()

    assert settings["schemaVersion"] == 3
    assert settings["enableNetwork"] is True
    assert settings["dataSource"] == "baostock"
    assert settings["deepSeekModel"] == "deepseek-v4-flash"
    assert settings["modelProvider"] == "deepseek-official"
    assert settings["agentPermissionMode"] == "read-only"


def test_new_installation_uses_equiseek_user_data_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("EQUISEEK_USER_DATA_ROOT", raising=False)
    monkeypatch.delenv("AEGISRUN_USER_DATA_ROOT", raising=False)

    assert user_data_root() == tmp_path / ".equiseek" / "user-data"


def test_existing_legacy_user_data_is_discovered_during_upgrade(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("EQUISEEK_USER_DATA_ROOT", raising=False)
    monkeypatch.delenv("AEGISRUN_USER_DATA_ROOT", raising=False)
    legacy = tmp_path / ".aegisrun" / "user-data"
    legacy.mkdir(parents=True)

    assert user_data_root() == legacy


def test_v1_preview_offline_default_migrates_to_simple_online_experience(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schemaVersion": 1, "enableNetwork": False, "dataSource": "demo"}),
        encoding="utf-8",
    )

    settings = LocalSettingsStore(path).load()

    assert settings["schemaVersion"] == 3
    assert settings["enableNetwork"] is True
    assert settings["dataSource"] == "baostock"


def test_explicit_v2_offline_choice_is_preserved(tmp_path) -> None:
    store = LocalSettingsStore(tmp_path / "settings.json")
    store.patch({"enableNetwork": False, "dataSource": "demo"})

    settings = store.load()

    assert settings["enableNetwork"] is False
    assert settings["dataSource"] == "demo"


def test_settings_accept_tushare_and_reject_unknown_market_source(tmp_path) -> None:
    store = LocalSettingsStore(tmp_path / "settings.json")

    assert store.patch({"dataSource": "tushare"})["dataSource"] == "tushare"
    with pytest.raises(ValueError, match="dataSource"):
        store.patch({"dataSource": "unknown"})


def test_legacy_deepseek_aliases_migrate_to_supported_v4_flash(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schemaVersion": 2, "deepSeekModel": "deepseek-chat"}),
        encoding="utf-8",
    )

    settings = LocalSettingsStore(path).load()

    assert settings["schemaVersion"] == 3
    assert settings["deepSeekModel"] == "deepseek-v4-flash"
    assert settings["modelBaseUrl"] == "https://api.deepseek.com"


def test_official_provider_migrates_or_rejects_unpublished_vision_model(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "modelProvider": "deepseek-official",
                "deepSeekModel": "deepseek-v4-flash-vision-exp",
            }
        ),
        encoding="utf-8",
    )
    store = LocalSettingsStore(path)

    assert store.load()["deepSeekModel"] == "deepseek-v4-flash"
    with pytest.raises(ValueError, match="自定义兼容端点"):
        store.patch({"deepSeekModel": "deepseek-v4-flash-vision-exp"})

    settings = store.patch(
        {
            "modelProvider": "openai-compatible",
            "modelBaseUrl": "https://vision.example.com/v1",
            "deepSeekModel": "deepseek-v4-flash-vision-exp",
        }
    )
    assert settings["deepSeekModel"] == "deepseek-v4-flash-vision-exp"


def test_sidecar_keeps_portfolio_and_default_workspace_in_its_local_data_root(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))

    dispatcher = SidecarDispatcher()

    assert dispatcher.portfolio.path == tmp_path / "portfolio.json"
    assert dispatcher._workspaces()[0]["path"] == str(tmp_path / "investment-agent-workspaces")


async def test_research_history_backfills_a_durable_pending_outcome(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    registry = RunRegistry(history_path=tmp_path / "run-history.json")

    async def execute(_report):  # type: ignore[no-untyped-def]
        return {
            "kind": "research",
            "symbol": "600050.SH",
            "source": "baostock",
            "sourceKind": "public-history",
            "adjustment": "qfq",
            "asOf": "2026-08-20",
            "advice": {
                "action": "buy",
                "action_label": "买入",
                "current_price": 10.0,
                "as_of": "2026-08-20",
            },
        }

    run = registry.start("research", execute)
    assert run.task is not None
    await run.task
    dispatcher = SidecarDispatcher(runs=registry)

    response = await dispatcher.dispatch(
        RpcRequest("history", "research.history", {"refresh": False}, "1.0")
    )

    assert response["items"][0]["result"]["outcome"]["status"] == "pending"
    restored = RunRegistry(history_path=tmp_path / "run-history.json")
    assert restored.get(run.run_id).result["outcome"]["baseline_price"] == 10.0


async def test_research_history_forwards_tushare_token_without_persisting_it(
    tmp_path, monkeypatch
) -> None:
    secret = "journal-tushare-secret-for-test"
    captured: dict[str, object] = {}
    baseline_date = date.today() - timedelta(days=2)
    latest_date = date.today() - timedelta(days=1)

    class Provider:
        def fetch_daily(self, *_args):  # type: ignore[no-untyped-def]
            if captured.get("fail"):
                raise RuntimeError(f"upstream rejected token {secret}")
            return SimpleNamespace(
                bars=(SimpleNamespace(trade_date=latest_date, close=11.0),)
            )

        def close(self) -> None:
            captured["closed"] = True

    def provider_factory(source, token):  # type: ignore[no-untyped-def]
        captured.update(source=source, token=token)
        return Provider()

    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(dispatcher_module, "market_data_provider", provider_factory)
    registry = RunRegistry(history_path=tmp_path / "run-history.json")

    async def execute(_report):  # type: ignore[no-untyped-def]
        return {
            "kind": "research",
            "symbol": "600050.SH",
            "source": "tushare",
            "sourceKind": "public-history",
            "adjustment": "qfq",
            "asOf": baseline_date.isoformat(),
            "advice": {
                "action": "buy",
                "action_label": "买入",
                "current_price": 10.0,
            },
        }

    run = registry.start("research", execute)
    assert run.task is not None
    await run.task
    dispatcher = SidecarDispatcher(runs=registry)

    response = await dispatcher.dispatch(
        RpcRequest(
            "history-tushare",
            "research.history",
            {"refresh": True, "tushareToken": secret},
            "1.0",
        )
    )

    assert captured == {"source": "tushare", "token": secret, "closed": True}
    assert response["items"][0]["result"]["outcome"]["decision_return_pct"] == 10.0
    assert secret not in json.dumps(response, ensure_ascii=False)

    captured["fail"] = True
    failed_refresh = await dispatcher.dispatch(
        RpcRequest(
            "history-tushare-error",
            "research.history",
            {"refresh": True, "tushareToken": secret},
            "1.0",
        )
    )
    serialized = json.dumps(failed_refresh, ensure_ascii=False)
    assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert secret not in (tmp_path / "run-history.json").read_text(encoding="utf-8")


async def test_sidecar_adds_and_selects_real_local_workspaces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path / "state"))
    first = tmp_path / "research-a"
    second = tmp_path / "research-b"
    first.mkdir()
    second.mkdir()
    dispatcher = SidecarDispatcher()

    added = await dispatcher.dispatch(
        RpcRequest("add", "workspace.add", {"path": str(first)}, "1.0")
    )
    assert added["activeId"].startswith("ws-")
    assert next(item for item in added["items"] if item["active"])["path"] == str(first)

    second_added = await dispatcher.dispatch(
        RpcRequest("add-2", "workspace.add", {"path": str(second)}, "1.0")
    )
    selected = await dispatcher.dispatch(
        RpcRequest("select", "workspace.select", {"workspaceId": added["activeId"]}, "1.0")
    )
    assert second_added["activeId"] != selected["activeId"]
    assert next(item for item in selected["items"] if item["active"])["path"] == str(first)


async def test_agent_treats_ui_selected_user_skill_as_explicit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    package = tmp_path / "skills" / "my-telecom-risk-check"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        """---
name: my-telecom-risk-check
description: 用户通信运营商研究规则。
version: 1.0.0
allowed-agents: [investment-lead-agent]
allowed-tools: []
network-required: false
resources: []
---

# 通信运营商检查

1. 核对经营现金流能否覆盖资本开支。
2. 核对分红是否由自由现金流覆盖。
3. 不承诺收益，不自动下单。
""",
        encoding="utf-8",
    )
    dispatcher = SidecarDispatcher()

    started = await dispatcher.dispatch(
        RpcRequest(
            "start",
            "agent.start",
            {
                "question": "使用我选择的 Skill，说明检查通信运营商时要看什么",
                "skillNames": ["my-telecom-risk-check"],
                "endDate": "2026-08-22",
            },
            "1.0",
        )
    )
    run_id = str(started["runId"])
    for _ in range(100):
        view = await dispatcher.dispatch(RpcRequest("poll", "run.get", {"runId": run_id}, "1.0"))
        if view["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.02)

    assert view["status"] == "succeeded"
    answer = view["result"]["answer"]
    assert "已按本轮选择的 Skill 读取规则" in answer
    assert "经营现金流能否覆盖资本开支" in answer
    assert "Skill 输出已被安全门阻止" not in answer


async def test_sidecar_injects_tushare_token_without_persisting_it(
    tmp_path, monkeypatch
) -> None:
    secret = "tushare-secret-for-test"
    captured: dict[str, object] = {}

    async def fail_after_capture(request, **_kwargs):
        captured["request"] = request
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(dispatcher_module, "execute_research", fail_after_capture)
    dispatcher = SidecarDispatcher()
    started = await dispatcher.dispatch(
        RpcRequest(
            "start-tushare",
            "research.start",
            {
                "symbol": "600050.SH",
                "source": "tushare",
                "tushareToken": secret,
                "endDate": "2026-08-22",
            },
            "1.0",
        )
    )

    for _ in range(100):
        view = await dispatcher.dispatch(
            RpcRequest("poll-tushare", "run.get", {"runId": started["runId"]}, "1.0")
        )
        if view["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.01)

    assert captured["request"].tushare_token == secret
    assert secret not in json.dumps(view, ensure_ascii=False)


async def test_agent_market_tools_receive_tushare_token_without_persisting_it(
    tmp_path, monkeypatch
) -> None:
    secret = "agent-tushare-secret-for-test"
    captured: dict[str, object] = {}

    async def fail_after_capture(request, **_kwargs):
        captured["request"] = request
        raise RuntimeError("synthetic agent failure")

    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(dispatcher_module, "execute_investment_agent", fail_after_capture)
    dispatcher = SidecarDispatcher()
    started = await dispatcher.dispatch(
        RpcRequest(
            "start-agent-tushare",
            "agent.start",
            {
                "question": "研究 600050.SH 的风险",
                "source": "tushare",
                "tushareToken": secret,
                "endDate": "2026-08-22",
            },
            "1.0",
        )
    )

    for _ in range(100):
        view = await dispatcher.dispatch(
            RpcRequest("poll-agent-tushare", "run.get", {"runId": started["runId"]}, "1.0")
        )
        if view["status"] not in {"queued", "running"}:
            break
        await asyncio.sleep(0.01)

    assert captured["request"].run.tushare_token == secret
    assert secret not in json.dumps(view, ensure_ascii=False)


async def test_sidecar_conversation_lifecycle_is_local_and_persistent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EQUISEEK_USER_DATA_ROOT", str(tmp_path))
    dispatcher = SidecarDispatcher()

    created = await dispatcher.dispatch(RpcRequest("create", "conversation.create", {}, "1.0"))
    thread_id = created["threadId"]
    dispatcher.conversations.append(thread_id, "user", "研究 600050.SH")

    restarted = SidecarDispatcher()
    listed = await restarted.dispatch(RpcRequest("list", "conversation.list", {}, "1.0"))
    loaded = await restarted.dispatch(
        RpcRequest("get", "conversation.get", {"threadId": thread_id}, "1.0")
    )

    assert listed["items"][0]["threadId"] == thread_id
    assert listed["items"][0]["title"] == "研究 600050.SH"
    assert loaded["turns"][0]["content"] == "研究 600050.SH"

    await restarted.dispatch(
        RpcRequest("delete", "conversation.delete", {"threadId": thread_id}, "1.0")
    )
    assert (await restarted.dispatch(RpcRequest("list-2", "conversation.list", {}, "1.0")))[
        "items"
    ] == []
