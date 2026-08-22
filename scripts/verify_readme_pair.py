#!/usr/bin/env python3
"""Verify that the English and Chinese root READMEs stay paired."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGLISH = ROOT / "README.md"
CHINESE = ROOT / "README.zh-CN.md"
RECORD = ROOT / "README.i18n.yaml"


def git_blob_hash(path: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required to verify README pairing")
    return subprocess.run(  # noqa: S603 - fixed executable and repository-owned paths
        [git, "hash-object", str(path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def headings(text: str) -> list[int]:
    return [len(match.group(1)) for match in re.finditer(r"^(#{1,6})\s+", text, re.MULTILINE)]


def code_blocks(text: str) -> list[tuple[str, str]]:
    return [
        (match.group(1).strip(), match.group(2))
        for match in re.finditer(r"^```([^\n]*)\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)
    ]


def read_record() -> dict[str, str]:
    values: dict[str, str] = {}
    if not RECORD.exists():
        return values
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def write_record() -> None:
    content = (
        "# EquiSeek bilingual README consistency record. Both languages carry equal authority.\n"
        "# After updating both files, refresh this record with:\n"
        "#   python scripts/verify_readme_pair.py --write\n"
        f"README.md: {git_blob_hash(ENGLISH)}\n"
        f"README.zh-CN.md: {git_blob_hash(CHINESE)}\n"
    )
    RECORD.write_text(content, encoding="utf-8")


def verify() -> list[str]:
    errors: list[str] = []
    english = ENGLISH.read_text(encoding="utf-8")
    chinese = CHINESE.read_text(encoding="utf-8")
    if "English | [简体中文](README.zh-CN.md)" not in english.splitlines()[:5]:
        errors.append("README.md is missing its Chinese language switcher")
    if "[English](README.md) | 简体中文" not in chinese.splitlines()[:5]:
        errors.append("README.zh-CN.md is missing its English language switcher")
    if headings(english) != headings(chinese):
        errors.append("README heading levels or order are structurally different")
    if code_blocks(english) != code_blocks(chinese):
        errors.append("README fenced code blocks are not byte-identical or in the same order")
    record = read_record()
    for path in (ENGLISH, CHINESE):
        expected = record.get(path.name)
        actual = git_blob_hash(path)
        if expected != actual:
            errors.append(
                f"{path.name} does not match README.i18n.yaml; "
                "sync both files and run --write"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the EquiSeek bilingual README pair")
    parser.add_argument(
        "--write",
        action="store_true",
        help="record the current pair as confirmed consistent",
    )
    args = parser.parse_args()
    if args.write:
        structural_errors = []
        english = ENGLISH.read_text(encoding="utf-8")
        chinese = CHINESE.read_text(encoding="utf-8")
        if headings(english) != headings(chinese):
            structural_errors.append("heading structures differ")
        if code_blocks(english) != code_blocks(chinese):
            structural_errors.append("fenced code blocks differ")
        if structural_errors:
            parser.error("; ".join(structural_errors))
        write_record()
    errors = verify()
    if errors:
        for error in errors:
            print(f"README pairing error: {error}")
        return 1
    print("README pairing: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
