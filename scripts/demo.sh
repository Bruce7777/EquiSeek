#!/usr/bin/env bash
set -euo pipefail

uv run equiseek demo-fake --report .equiseek/demo-report.html
echo "Report: .equiseek/demo-report.html"
