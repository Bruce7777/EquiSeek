# Threat Model

## Protected assets

- workspace and host files;
- secrets and network reachability;
- approval integrity and invocation uniqueness;
- Run/Event audit records;
- CPU, memory, process, and wall-clock capacity.
- task dependency integrity, retry boundaries, and workspace quotas.

## Controls

- structured Tool arguments, JSON Schema validation, policy filtering, and execution-time authorization;
- path canonicalization rejecting absolute paths, `..`, and escaping symlinks;
- high-risk approval with version checks and replay rejection;
- invocation idempotency and explicit unknown external outcomes;
- Docker network disabled, read-only root, non-root user, all capabilities dropped, PID/CPU/memory/time limits, one workspace mount;
- validated acyclic plans, bounded concurrency, side-effect idempotency keys, and fail-closed recovery to `unknown_outcome`;
- per-task directories, safe IDs, canonical paths, atomic state writes, bounded output, scrubbed inherited environment, and per-Run quota;
- public event payload separated from an optional audit reference.

## Non-goals and residual risk

The local-process backend is not isolation and cannot enforce network denial; it refuses tasks that require strict isolation. Docker daemon access is privileged, image supply chain risk remains, and this reference executor does not promise protection against hostile public multi-tenant workloads. A malicious process could create unmanaged files until the next quota checkpoint; production deployments should also enforce filesystem quotas outside the process. Inherited secrets are removed from sandbox environments, but arbitrary secrets printed by explicitly configured task environment or code are not output-redacted; `/api/capabilities` reports that distinction. `apply_patch` is a synthetic local write; a production external Tool needs provider-specific reconciliation by `external_reference`. The development token is not a multi-tenant identity system.
