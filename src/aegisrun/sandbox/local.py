from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from aegisrun.core.errors import SandboxViolationError
from aegisrun.core.security import safe_join
from aegisrun.sandbox.base import (
    SandboxCapabilities,
    SandboxEnforcement,
    SandboxPolicy,
    SandboxResult,
)

SAFE_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR", "TMPDIR")


def restricted_environment(policy: SandboxPolicy) -> dict[str, str]:
    environment = {name: value for name in SAFE_ENVIRONMENT if (value := os.getenv(name))}
    environment.update(policy.environment)
    environment["EQUISEEK_SANDBOX_NETWORK"] = "allowed" if policy.network_allowed else "denied"
    return environment


def bounded_output(stdout: bytes, stderr: bytes, limit: int) -> tuple[str, str, bool]:
    combined = len(stdout) + len(stderr)
    if combined <= limit:
        return stdout.decode(errors="replace"), stderr.decode(errors="replace"), False
    stdout_budget = min(len(stdout), limit)
    stderr_budget = max(limit - stdout_budget, 0)
    return (
        stdout[:stdout_budget].decode(errors="replace"),
        stderr[:stderr_budget].decode(errors="replace"),
        True,
    )


async def drain_bounded(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    value = bytearray()
    truncated = False
    while chunk := await stream.read(8192):
        remaining = limit - len(value)
        if remaining > 0:
            value.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(value), truncated


class LocalProcessSandbox:
    """Test/demo backend. It is not a security boundary."""

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            backend="local",
            enforcement=SandboxEnforcement.NONE,
            security_boundary=False,
            file_effects=SandboxEnforcement.NONE,
            network=SandboxEnforcement.NONE,
            process=SandboxEnforcement.NONE,
            resource_limits=SandboxEnforcement.PARTIAL,
            detail=(
                "Local processes only scrub inherited environment and bound captured output; "
                "filesystem, network, process and CPU/memory isolation are not enforced."
            ),
        )

    async def exec(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int,
        policy: SandboxPolicy | None = None,
    ) -> SandboxResult:
        active_policy = policy or SandboxPolicy()
        if active_policy.require_isolation:
            raise SandboxViolationError(
                "local sandbox is not a security boundary; configure the docker backend"
            )
        safe_join(workspace, ".", must_exist=True)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workspace,
            env=restricted_environment(active_policy),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        try:
            stdout_result, stderr_result, _ = await asyncio.wait_for(
                asyncio.gather(
                    drain_bounded(process.stdout, active_policy.max_output_bytes),
                    drain_bounded(process.stderr, active_policy.max_output_bytes),
                    process.wait(),
                ),
                timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        stdout, stdout_truncated = stdout_result
        stderr, stderr_truncated = stderr_result
        stdout_text, stderr_text, truncated = bounded_output(
            stdout, stderr, active_policy.max_output_bytes
        )
        return SandboxResult(
            exit_code=process.returncode or 0,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=int((time.monotonic() - started) * 1000),
            output_truncated=truncated or stdout_truncated or stderr_truncated,
            enforcement=SandboxEnforcement.NONE,
        )
