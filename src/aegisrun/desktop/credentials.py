from __future__ import annotations

import os
from typing import Any


class CredentialStore:
    SERVICE = "io.aegisrun.desktop"

    def __init__(self, keyring_backend: Any | None = None) -> None:
        if keyring_backend is None:
            try:
                import keyring as keyring_backend
            except ImportError:
                keyring_backend = None
        self._keyring = keyring_backend

    def __repr__(self) -> str:
        return "CredentialStore(service='io.aegisrun.desktop')"

    def _get(self, name: str, environment: str) -> str | None:
        value = os.getenv(environment)
        if value:
            return value
        if self._keyring is None:
            return None
        try:
            stored = self._keyring.get_password(self.SERVICE, name)
            return stored if isinstance(stored, str) else None
        except Exception:
            return None

    def _set(self, name: str, value: str) -> None:
        if self._keyring is None:
            raise RuntimeError("系统密钥库不可用，请通过环境变量配置密钥")
        if value:
            self._keyring.set_password(self.SERVICE, name, value)
        else:
            try:
                self._keyring.delete_password(self.SERVICE, name)
            except Exception:
                return

    def get_deepseek_api_key(self) -> str | None:
        return self._get("deepseek-api-key", "DEEPSEEK_API_KEY")

    def set_deepseek_api_key(self, value: str) -> None:
        self._set("deepseek-api-key", value.strip())

    def get_tushare_token(self) -> str | None:
        return self._get("tushare-token", "TUSHARE_TOKEN")

    def set_tushare_token(self, value: str) -> None:
        self._set("tushare-token", value.strip())

    def get_tavily_api_key(self) -> str | None:
        return self._get("tavily-api-key", "TAVILY_API_KEY")

    def set_tavily_api_key(self, value: str) -> None:
        self._set("tavily-api-key", value.strip())
