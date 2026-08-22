from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from aegisrun.user_data import named_data_file


def _default_local_database_url() -> str:
    path = named_data_file("equiseek.sqlite3", "aegisrun.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EQUISEEK_",
        extra="ignore",
    )

    database_url: str = Field(default_factory=_default_local_database_url)
    auto_create_schema: bool = True
    checkpoint_url: str | None = None
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str | None = Field(default=None, repr=False)
    model_name: str = "gpt-4.1-mini"
    model_timeout_seconds: float = 30.0
    artifact_root: Path = Path(".equiseek/artifacts")
    workspace_root: Path = Path(".equiseek/workspaces")
    workspace_max_bytes_per_run: int = 512 * 1024 * 1024
    sandbox_backend: str = "local"
    sandbox_image: str = "equiseek-sandbox:0.1.0"
    sandbox_network_allowed: bool = False
    sandbox_max_output_bytes: int = 64_000
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_require_isolation: bool = False
    subtask_max_concurrency: int = 4
    worker_id: str = "worker-local"
    worker_poll_seconds: float = 0.5
    lease_seconds: int = 60
    log_level: str = "INFO"
    telemetry_jsonl: Path | None = None
    public_base_url: str = "http://127.0.0.1:8000"
    dev_token: str = Field(default="equiseek-local-demo", repr=False)

    @property
    def effective_checkpoint_url(self) -> str | None:
        if self.checkpoint_url:
            return self.checkpoint_url
        database = make_url(self.database_url)
        if database.get_backend_name() != "sqlite" or not database.database:
            return None
        if database.database == ":memory:":
            return None
        database_path = Path(database.database).expanduser()
        legacy = database_path.with_name("aegisrun-checkpoints.sqlite3")
        current = database_path.with_name("equiseek-checkpoints.sqlite3")
        return str(legacy if legacy.exists() and not current.exists() else current)

    def prepare_directories(self) -> None:
        if self.workspace_max_bytes_per_run < 1:
            raise ValueError("workspace_max_bytes_per_run must be positive")
        if self.sandbox_max_output_bytes < 1:
            raise ValueError("sandbox_max_output_bytes must be positive")
        if self.sandbox_cpu_limit <= 0 or self.sandbox_pids_limit < 1:
            raise ValueError("sandbox resource limits must be positive")
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            database_path = make_url(self.database_url).database
            if database_path and database_path != ":memory:":
                parent = Path(database_path).expanduser().parent
                parent.mkdir(parents=True, exist_ok=True)
                parent.chmod(0o700)
        checkpoint_url = self.effective_checkpoint_url
        if checkpoint_url and "://" not in checkpoint_url and checkpoint_url != ":memory:":
            checkpoint_parent = Path(checkpoint_url).expanduser().parent
            checkpoint_parent.mkdir(parents=True, exist_ok=True)
            checkpoint_parent.chmod(0o700)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings
