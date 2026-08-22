from __future__ import annotations

from aegisrun.config import Settings
from aegisrun.sandbox.base import SandboxProvider
from aegisrun.sandbox.docker import DockerSandbox
from aegisrun.sandbox.local import LocalProcessSandbox


def create_sandbox(settings: Settings) -> SandboxProvider:
    if settings.sandbox_backend == "docker":
        return DockerSandbox(settings.sandbox_image)
    if settings.sandbox_backend == "local":
        if settings.sandbox_require_isolation:
            raise ValueError(
                "sandbox_require_isolation=true cannot use the local backend; "
                "configure EQUISEEK_SANDBOX_BACKEND=docker"
            )
        return LocalProcessSandbox()
    raise ValueError(f"unsupported sandbox backend: {settings.sandbox_backend}")


def sandbox_capabilities(settings: Settings) -> dict[str, object]:
    strict = settings.sandbox_backend == "docker"
    provider: SandboxProvider
    if strict:
        provider = DockerSandbox(settings.sandbox_image)
    else:
        provider = LocalProcessSandbox()
    return {
        **provider.capabilities().to_dict(),
        "network_default": settings.sandbox_network_allowed,
        "network_enforced": strict,
        "read_only_root": strict,
        "resource_limits": strict,
        "inherited_environment_scrubbed": True,
        "output_secret_redaction": False,
        "output_limit_bytes": settings.sandbox_max_output_bytes,
        "memory_limit": settings.sandbox_memory_limit,
        "cpu_limit": settings.sandbox_cpu_limit,
        "pids_limit": settings.sandbox_pids_limit,
        "strict_isolation_required": settings.sandbox_require_isolation,
    }
