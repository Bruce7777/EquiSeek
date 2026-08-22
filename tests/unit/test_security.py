from __future__ import annotations

from pathlib import Path

import pytest

from aegisrun.core.errors import PolicyDeniedError, SandboxViolationError
from aegisrun.core.security import authorize_relative_path, canonical_hash, safe_join


def test_canonical_hash_is_key_order_independent() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_canonical_hash_changes_with_value() -> None:
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_safe_join_accepts_relative_path(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "nested/file.txt") == tmp_path / "nested/file.txt"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "nested/../../secret"])
def test_safe_join_rejects_escape(tmp_path: Path, path: str) -> None:
    with pytest.raises(SandboxViolationError):
        safe_join(tmp_path, path)


def test_safe_join_rejects_escaping_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-aegisrun.txt"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(SandboxViolationError):
        safe_join(tmp_path, "link", must_exist=True)


def test_path_policy_accepts_nested_prefix() -> None:
    authorize_relative_path("src/package/file.py", ("src",))


@pytest.mark.parametrize("path", ["README.md", "tests/test_app.py", "../secret"])
def test_path_policy_rejects_non_matching_prefix(path: str) -> None:
    with pytest.raises(PolicyDeniedError):
        authorize_relative_path(path, ("src",))
