#!/usr/bin/env bash
set -euo pipefail

uv run pytest tests/fault_injection/test_sse_reconnect.py -q
