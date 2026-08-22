from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from aegisrun.config import Settings
from aegisrun.core.errors import SandboxViolationError
from aegisrun.sandbox.base import SandboxEnforcement, SandboxPolicy
from aegisrun.sandbox.docker import DockerSandbox
from aegisrun.sandbox.factory import create_sandbox, sandbox_capabilities
from aegisrun.sandbox.local import LocalProcessSandbox


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_sandbox_uses_argv_without_shell(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    result = await LocalProcessSandbox().exec(
        tmp_path,
        ["python", "-c", "import sys; print(sys.argv[1])", f";touch {marker}"],
        5,
    )
    assert result.exit_code == 0
    assert not marker.exists()
    assert result.enforcement is SandboxEnforcement.NONE


@pytest.mark.asyncio
@pytest.mark.security
async def test_sandbox_timeout_terminates_process(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError):
        await LocalProcessSandbox().exec(
            tmp_path, ["python", "-c", "import time; time.sleep(2)"], 0.01
        )


@pytest.mark.asyncio
@pytest.mark.security
async def test_missing_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, SandboxViolationError)):
        await LocalProcessSandbox().exec(tmp_path / "missing", ["python", "-V"], 1)


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_sandbox_scrubs_secrets_and_limits_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    result = await LocalProcessSandbox().exec(
        tmp_path,
        [
            "python",
            "-c",
            "import os; print(os.getenv('DEEPSEEK_API_KEY')); print('x' * 1000)",
        ],
        5,
        SandboxPolicy(max_output_bytes=80),
    )

    assert "must-not-leak" not in result.stdout
    assert result.output_truncated is True
    assert len(result.stdout.encode()) <= 80


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_backend_refuses_strict_isolation(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolationError, match="security boundary"):
        await LocalProcessSandbox().exec(
            tmp_path,
            ["python", "-V"],
            5,
            SandboxPolicy(require_isolation=True),
        )


def test_sandbox_factory_fails_closed_for_impossible_strict_configuration() -> None:
    settings = Settings(sandbox_backend="local", sandbox_require_isolation=True)

    with pytest.raises(ValueError, match="cannot use the local backend"):
        create_sandbox(settings)

    capabilities = sandbox_capabilities(settings)
    assert capabilities["security_boundary"] is False
    assert capabilities["enforcement"] == "none"
    assert capabilities["file_effects"] == "none"
    assert capabilities["resource_limits_enforcement"] == "partial"
    assert capabilities["resource_limits"] is False
    assert capabilities["network_enforced"] is False


@pytest.mark.asyncio
@pytest.mark.security
async def test_docker_sandbox_applies_isolation_and_resource_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class Container:
        def wait(self, timeout: int) -> dict[str, int]:
            captured["timeout"] = timeout
            return {"StatusCode": 0}

        def logs(self, *, stdout: bool, stderr: bool) -> bytes:
            return b"ok" if stdout and not stderr else b""

        def remove(self, *, force: bool) -> None:
            captured["removed"] = force

        def kill(self) -> None:
            captured["killed"] = True

    class Containers:
        def run(self, image: object, argv: list[str], **kwargs: object) -> Container:
            captured.update({"image": image, "argv": argv, **kwargs})
            return Container()

    class Images:
        @staticmethod
        def get(name: str) -> str:
            return name

    class Client:
        images = Images()
        containers = Containers()

        @staticmethod
        def close() -> None:
            captured["closed"] = True

    monkeypatch.setattr("aegisrun.sandbox.docker.docker.from_env", Client)
    result = await DockerSandbox("sandbox:test").exec(
        tmp_path,
        ["python", "-V"],
        7,
        SandboxPolicy(
            network_allowed=False,
            read_only_workspace=True,
            memory_limit="128m",
            cpu_limit=0.5,
            pids_limit=32,
        ),
    )

    assert result.exit_code == 0
    assert result.enforcement is SandboxEnforcement.FULL
    assert DockerSandbox("sandbox:test").capabilities().to_dict() == {
        "backend": "docker",
        "enforcement": "full",
        "security_boundary": True,
        "file_effects": "full",
        "network": "full",
        "process": "full",
        "resource_limits_enforcement": "full",
        "detail": (
            "Docker applies a read-only root, explicit workspace mount, network mode, "
            "dropped capabilities and cgroup resource limits per execution."
        ),
    }
    assert captured["network_disabled"] is True
    assert captured["read_only"] is True
    assert captured["cap_drop"] == ["ALL"]
    assert captured["pids_limit"] == 32
    assert captured["mem_limit"] == "128m"
    assert captured["nano_cpus"] == 500_000_000
    assert captured["user"] == "65532:65532"
    log_config = cast(Any, captured["log_config"])
    assert log_config.config["max-file"] == "1"
    assert captured["removed"] is True
