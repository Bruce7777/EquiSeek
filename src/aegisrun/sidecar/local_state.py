from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aegisrun.user_data import user_data_root as branded_user_data_root

DEFAULT_SETTINGS: dict[str, Any] = {
    "schemaVersion": 3,
    "dataSource": "baostock",
    "defaultSymbol": "600050.SH",
    "adjustment": "qfq",
    "workspaceRoot": "",
    "workspaceRoots": [],
    "userSkillRoot": "",
    "includeBuiltinSkills": True,
    "enableNetwork": True,
    "enableDeepSeek": False,
    "modelProvider": "deepseek-official",
    "modelBaseUrl": "https://api.deepseek.com",
    "deepSeekModel": "deepseek-v4-flash",
    "agentPermissionMode": "read-only",
    "theme": "light",
}


def user_data_root() -> Path:
    return branded_user_data_root()


class LocalSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "settings.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_SETTINGS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {**DEFAULT_SETTINGS, "warning": "设置文件损坏，已使用安全默认值"}
        if not isinstance(raw, dict):
            return {**DEFAULT_SETTINGS, "warning": "设置文件格式无效，已使用安全默认值"}
        # v1 was a developer-oriented preview that defaulted to offline demo data.
        # v2 is aimed at non-programmers and migrates that untouched preview default
        # to the one-click public-data experience. Explicit v2 offline choices persist.
        schema_version = raw.get("schemaVersion", 1)
        if not isinstance(schema_version, int):
            schema_version = 1
        if schema_version < 2:
            if raw.get("dataSource", "demo") == "demo" and not raw.get("enableNetwork", False):
                raw = {**raw, "dataSource": "baostock", "enableNetwork": True}
        if raw.get("deepSeekModel") in {None, "", "deepseek-chat", "deepseek-reasoner"}:
            raw = {**raw, "deepSeekModel": "deepseek-v4-flash"}
        if schema_version < 3:
            raw = {
                **raw,
                "modelProvider": "deepseek-official",
                "modelBaseUrl": "https://api.deepseek.com",
                "agentPermissionMode": "read-only",
            }
        for legacy_key in ("enableVision", "visionBaseUrl", "visionModel"):
            raw.pop(legacy_key, None)
        roots = raw.get("workspaceRoots", [])
        if not isinstance(roots, list):
            roots = []
        raw = {**raw, "workspaceRoots": [str(item) for item in roots if str(item).strip()]}
        if (
            raw.get("modelProvider", "deepseek-official") == "deepseek-official"
            and raw.get("deepSeekModel") == "deepseek-v4-flash-vision-exp"
        ):
            raw = {**raw, "deepSeekModel": "deepseek-v4-flash"}
        return {**DEFAULT_SETTINGS, **raw, "schemaVersion": 3}

    def patch(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS) - {"schemaVersion"}
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        value = self.load()
        value.pop("warning", None)
        value.update(updates)
        if (
            value.get("modelProvider") == "deepseek-official"
            and value.get("deepSeekModel") == "deepseek-v4-flash-vision-exp"
        ):
            raise ValueError(
                "DeepSeek 官方 API 当前不提供 Vision Exp；请先切换到自定义兼容端点"
            )
        value["schemaVersion"] = 3
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.path)
        return value


def effective_workspace(settings: dict[str, Any]) -> Path:
    configured = str(settings.get("workspaceRoot", "")).strip()
    return (
        Path(configured).expanduser()
        if configured
        else user_data_root() / "investment-agent-workspaces"
    )


def effective_skill_root(settings: dict[str, Any]) -> Path:
    configured = str(settings.get("userSkillRoot", "")).strip()
    return Path(configured).expanduser() if configured else user_data_root() / "skills"
