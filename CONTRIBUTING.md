# Contributing

Thank you for improving EquiSeek. Open an issue before a large design change. Keep each pull request focused and include a deterministic regression test for behavior changes.

```bash
make bootstrap
make release-check
```

Do not submit proprietary code, production data, credentials, copied prompts, or confidential business flows. New Tool implementations must document risk, side effects, input schema, timeout, required capabilities, idempotency behavior, and unknown-outcome handling.

Commits use imperative summaries. Security reports follow [SECURITY.md](SECURITY.md), not public issues.
