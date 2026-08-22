from __future__ import annotations

import os
from pathlib import Path


def default_research_workspace_root() -> Path:
    configured = os.getenv("EQUISEEK_RESEARCH_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".equiseek" / "research-workspaces"
