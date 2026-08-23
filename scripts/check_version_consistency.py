#!/usr/bin/env python3
"""Fail release checks when public product version declarations diverge."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.2.0"


def main() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8"))
    init_text = (ROOT / "src/aegisrun/__init__.py").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    declarations = {
        "Python distribution": pyproject["project"]["version"],
        "Electron application": package["version"],
        "Python module": re.search(r'__version__ = "([^"]+)"', init_text).group(1),
        "SECURITY support line": re.search(r"latest `([^`]+)` release", security).group(1),
    }
    expected = {
        "Python distribution": EXPECTED,
        "Electron application": EXPECTED,
        "Python module": EXPECTED,
        "SECURITY support line": "0.2.x",
    }
    mismatches = {
        key: {"actual": value, "expected": expected[key]}
        for key, value in declarations.items()
        if value != expected[key]
    }
    if mismatches:
        raise SystemExit(json.dumps(mismatches, indent=2, sort_keys=True))
    print(json.dumps(declarations, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
