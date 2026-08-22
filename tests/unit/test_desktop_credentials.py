from __future__ import annotations

from aegisrun.desktop.credentials import CredentialStore


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_credentials_prefer_environment_and_never_expose_secret(monkeypatch: object) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")  # type: ignore[attr-defined]
    store = CredentialStore(keyring_backend=MemoryKeyring())

    assert store.get_deepseek_api_key() == "env-secret"
    assert "env-secret" not in repr(store)


def test_credentials_use_operating_system_keyring(monkeypatch: object) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # type: ignore[attr-defined]
    backend = MemoryKeyring()
    store = CredentialStore(keyring_backend=backend)
    store.set_deepseek_api_key("keyring-secret")

    assert store.get_deepseek_api_key() == "keyring-secret"
    store.set_deepseek_api_key("")
    assert store.get_deepseek_api_key() is None


def test_optional_web_search_key_uses_environment_or_keyring(monkeypatch: object) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)  # type: ignore[attr-defined]
    backend = MemoryKeyring()
    store = CredentialStore(keyring_backend=backend)
    store.set_tavily_api_key("search-secret")
    assert store.get_tavily_api_key() == "search-secret"

    monkeypatch.setenv("TAVILY_API_KEY", "environment-search-secret")  # type: ignore[attr-defined]
    assert store.get_tavily_api_key() == "environment-search-secret"
