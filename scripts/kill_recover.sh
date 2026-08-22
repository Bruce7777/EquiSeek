#!/usr/bin/env bash
set -euo pipefail

uv run pytest tests/fault_injection/test_kill_recover.py -q
