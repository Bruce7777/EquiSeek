# Security Policy

## Supported version

Security fixes target the latest `0.2.x` release during the alpha period.

## Reporting

Do not disclose a vulnerability in a public issue. Submit it through [GitHub Security Advisories](https://github.com/Bruce7777/EquiSeek/security/advisories/new). The repository is public; reporting through GitHub requires signing in. Do not fall back to a public Issue when a report contains vulnerability details.

Include the affected revision, reproduction steps, impact, and any proposed mitigation. Maintainers will acknowledge a complete report within seven days and coordinate disclosure after a fix is available. Do not send secrets or personal financial data with a report.

Never attach real credentials or employer data. The Docker executor is a controlled local-reference sandbox. It does not claim hostile public multi-tenant isolation, and access to the Docker daemon remains privileged.
