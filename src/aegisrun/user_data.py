from __future__ import annotations

import os
from pathlib import Path


def user_data_root() -> Path:
    """Use the EquiSeek data root and discover data from earlier releases."""
    configured = os.getenv("EQUISEEK_USER_DATA_ROOT") or os.getenv("AEGISRUN_USER_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()
    current = Path.home() / ".equiseek" / "user-data"
    legacy = Path.home() / ".aegisrun" / "user-data"
    return legacy if legacy.exists() and not current.exists() else current


def named_data_file(current_name: str, legacy_name: str | None = None) -> Path:
    root = user_data_root()
    current = root / current_name
    if current.exists() or not legacy_name:
        return current
    legacy = root / legacy_name
    return legacy if legacy.exists() else current
