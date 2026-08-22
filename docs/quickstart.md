# Quickstart

## Local deterministic demo

Requirements: Python 3.12 and `uv`.

```bash
make bootstrap
make demo-fake
open .equiseek/demo-report.html
```

The local backend is trusted-process mode. It copies a synthetic broken repository into a per-Run workspace, records every model/tool step, pauses before `apply_patch`, applies the approved change, reruns tests, and writes a checksummed report Artifact. No model API key is used.

Inspect capabilities and a Run's persisted plan/workspace metadata:

```bash
curl http://127.0.0.1:8000/api/capabilities
curl http://127.0.0.1:8000/api/runs/RUN_ID/plan
curl http://127.0.0.1:8000/api/runs/RUN_ID/workspace
```

## Local SQLite service

No Docker or PostgreSQL is required. The default database is
`~/.equiseek/user-data/equiseek.sqlite3` with WAL enabled. LangGraph state is independently
persisted at `~/.equiseek/user-data/equiseek-checkpoints.sqlite3`; both files are created
automatically with private permissions. Start the API and the single local Worker in two terminals:

```bash
make local-init
make local-api
```

```bash
make local-worker
```

This mode persists Run, Plan, Event, approval, Artifact metadata and Agent graph checkpoints across
process restarts. It is designed for one user and one Worker. The investment desktop client can
also run directly and does not require either local API process.

## Optional PostgreSQL services

Requirements: Docker and Compose.

```bash
make up
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: quickstart-1' \
  -d '{"agent_name":"issue_triage","input":{"issue":"ISSUE.md"}}'
```

Compose installs the `postgres` extra and runs migration, API, worker, and PostgreSQL in trusted-demo mode. Use this mode for multiple competing Workers or server deployment, not for an ordinary local desktop user. For untrusted code, build the sandbox image, run the worker on a Docker-capable host, and set both `EQUISEEK_SANDBOX_BACKEND=docker` and `EQUISEEK_SANDBOX_REQUIRE_ISOLATION=true`. Startup fails instead of silently using the local backend when strict isolation is requested.

## Verification

```bash
make test
make test-fault
make demo-sse-reconnect
make release-check
```

`make down` stops services but preserves volumes. Use `docker compose down -v` only when intentionally deleting local data.
