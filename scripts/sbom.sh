#!/usr/bin/env bash
set -euo pipefail

docker build -t equiseek:local .
docker run --rm anchore/syft:latest equiseek:local -o spdx-json > sbom.spdx.json
