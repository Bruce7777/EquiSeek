# Deployment and Rollback

The reference deployment is a single API, worker, migration job, and PostgreSQL. Artifact/workspace data uses a named volume. Configure secrets outside source control and use an immutable image digest in a real environment.

Before release: run `make release-check`, PostgreSQL integration, container scan, and the three demos. Apply Alembic migration before API/worker. Verify `/health`, `/api/capabilities`, Run creation, plan/workspace inspection, worker claim, approval, Artifact download, and SSE cursor replay.

The default Compose file deliberately declares `sandbox_backend=local`; it is a trusted demonstration stack and not an isolation boundary. A production worker that executes untrusted code must have the sandbox image prebuilt, a controlled Docker endpoint, `EQUISEEK_SANDBOX_BACKEND=docker`, and `EQUISEEK_SANDBOX_REQUIRE_ISOLATION=true`. Keep networking denied unless both the frozen Run policy and deployment policy allow it. Size filesystem storage independently in addition to `EQUISEEK_WORKSPACE_MAX_BYTES_PER_RUN`, because the in-process quota is checked at managed checkpoints rather than being a kernel filesystem quota.

Rollback application containers to the prior image. Migration `0001` is additive and compatible with a stopped service; do not run its destructive downgrade against retained data. Restore PostgreSQL and Artifact storage together if data rollback is required, because metadata and files form one logical release boundary.
