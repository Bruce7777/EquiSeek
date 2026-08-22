from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aegisrun.core.errors import PolicyDeniedError, SandboxViolationError


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def safe_join(root: Path, relative_path: str, *, must_exist: bool = False) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SandboxViolationError(f"path is outside workspace: {relative_path}")
    root_resolved = root.resolve()
    target = (root_resolved / candidate).resolve(strict=must_exist)
    if target != root_resolved and root_resolved not in target.parents:
        raise SandboxViolationError(f"path escapes workspace: {relative_path}")
    return target


def authorize_relative_path(relative_path: str, prefixes: tuple[str, ...]) -> None:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PolicyDeniedError(f"path is not authorized: {relative_path}")
    for prefix in prefixes:
        allowed = Path(prefix)
        if prefix == "." or candidate == allowed or allowed in candidate.parents:
            return
    raise PolicyDeniedError(f"path is not authorized: {relative_path}")
