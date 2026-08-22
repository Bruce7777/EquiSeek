# Evaluation and Release Gates

The deterministic suite contains unit, API/component, security, runtime, and fault-injection coverage. `tests/unit/test_evals.py` versions ten trajectory cases and checks ordered required actions plus forbidden actions.

Release gates:

- at least 30 deterministic tests and 10 trajectory cases;
- core coverage at least 80%;
- ten kill/recover cases and ten cursor reconnect cases;
- unauthorized Tool, path escape, approval replay, unknown write outcome, and over-budget behavior blocked;
- Fake Model demo succeeds without a key;
- lint, strict mypy, dependency audit, PostgreSQL checkpoint integration, image scan, and SBOM in CI.

The Fake Model validates runtime determinism, not model quality. A real OpenAI-compatible adapter is present for small effect samples, but no network model call is part of a stability gate.
