from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Any

import docker

from aegisrun.core.security import safe_join
from aegisrun.sandbox.base import (
    SandboxCapabilities,
    SandboxEnforcement,
    SandboxPolicy,
    SandboxResult,
)
from aegisrun.sandbox.local import bounded_output


class DockerSandbox:
    def __init__(self, image: str) -> None:
        self.image = image

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            backend="docker",
            enforcement=SandboxEnforcement.FULL,
            security_boundary=True,
            file_effects=SandboxEnforcement.FULL,
            network=SandboxEnforcement.FULL,
            process=SandboxEnforcement.FULL,
            resource_limits=SandboxEnforcement.FULL,
            detail=(
                "Docker applies a read-only root, explicit workspace mount, network mode, "
                "dropped capabilities and cgroup resource limits per execution."
            ),
        )

    async def exec(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int,
        policy: SandboxPolicy | None = None,
    ) -> SandboxResult:
        root = safe_join(workspace, ".", must_exist=True)
        return await asyncio.to_thread(
            self._exec_sync, root, argv, timeout_seconds, policy or SandboxPolicy()
        )

    def _exec_sync(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int,
        policy: SandboxPolicy,
    ) -> SandboxResult:
        client = docker.from_env()
        started = time.monotonic()
        image = client.images.get(self.image)
        log_limit_kib = max(4, math.ceil(policy.max_output_bytes / 1024))
        container: Any = client.containers.run(
            image,
            argv,
            detach=True,
            network_disabled=not policy.network_allowed,
            read_only=True,
            cap_drop=["ALL"],
            pids_limit=policy.pids_limit,
            mem_limit=policy.memory_limit,
            nano_cpus=int(policy.cpu_limit * 1_000_000_000),
            user="65532:65532",
            working_dir="/workspace",
            volumes={
                str(workspace): {
                    "bind": "/workspace",
                    "mode": "ro" if policy.read_only_workspace else "rw",
                }
            },
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},  # noqa: S108
            security_opt=["no-new-privileges:true"],
            environment=policy.environment,
            log_config=docker.types.LogConfig(
                type=docker.types.LogConfig.types.JSON,
                config={"max-size": f"{log_limit_kib}k", "max-file": "1"},
            ),
        )
        try:
            result = container.wait(timeout=timeout_seconds)
            logs = container.logs(stdout=True, stderr=False)
            errors = container.logs(stdout=False, stderr=True)
            stdout, stderr, truncated = bounded_output(logs, errors, policy.max_output_bytes)
            return SandboxResult(
                exit_code=int(result.get("StatusCode", 1)),
                stdout=stdout,
                stderr=stderr,
                duration_ms=int((time.monotonic() - started) * 1000),
                output_truncated=truncated,
                enforcement=SandboxEnforcement.FULL,
            )
        except Exception:
            try:
                container.kill()
            except docker.errors.APIError:
                pass
            raise
        finally:
            try:
                container.remove(force=True)
            except docker.errors.APIError:
                pass
            client.close()
