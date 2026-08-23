#!/usr/bin/env python3
"""Build the license and SBOM resources embedded in desktop distributions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = PROJECT_ROOT / "apps" / "desktop"
DEFAULT_OUTPUT = PROJECT_ROOT / "build" / "release-compliance"
LICENSE_NAMES = re.compile(r"^(license|licence|copying|notice|copyright)(\.|$)", re.I)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]+", "_", value)[:160]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_license_files(package_root: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    if not package_root.is_dir():
        return copied
    for candidate in sorted(package_root.iterdir()):
        if not candidate.is_file() or not LICENSE_NAMES.match(candidate.name):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / candidate.name
        shutil.copy2(candidate, target)
        copied.append(target.name)
    return copied


def collect_python_licenses(destination: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for distribution in sorted(
        importlib.metadata.distributions(),
        key=lambda item: (item.metadata.get("Name") or "").lower(),
    ):
        name = distribution.metadata.get("Name") or "unknown-python-package"
        version = distribution.version
        package_destination = destination / f"{safe_name(name)}-{safe_name(version)}"
        copied: list[str] = []
        for relative in distribution.files or ():
            relative_path = Path(str(relative))
            if not LICENSE_NAMES.match(relative_path.name):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file() or source.stat().st_size > 5 * 1024 * 1024:
                continue
            package_destination.mkdir(parents=True, exist_ok=True)
            target = package_destination / safe_name("__".join(relative_path.parts))
            shutil.copy2(source, target)
            copied.append(target.name)
        declared = distribution.metadata.get("License-Expression")
        if not declared or declared == "UNKNOWN":
            legacy_license = distribution.metadata.get("License")
            declared = legacy_license if legacy_license and legacy_license != "UNKNOWN" else None
        if not declared:
            classifier_map = {
                "License :: OSI Approved :: Apache Software License": "Apache-2.0",
                "License :: OSI Approved :: ISC License (ISCL)": "ISC",
                "License :: OSI Approved :: MIT License": "MIT",
                "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
                "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
            }
            declared = " AND ".join(
                classifier_map[item]
                for item in distribution.metadata.get_all("Classifier", [])
                if item in classifier_map
            ) or "UNKNOWN"
        records.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": version,
                "license": declared,
                "licenseDirectory": f"python/{package_destination.name}",
                "licenseFiles": [
                    f"python/{package_destination.name}/{file_name}"
                    for file_name in sorted(set(copied))
                ],
            }
        )
    return records


def collect_node_licenses(destination: Path) -> list[dict[str, Any]]:
    lock = json.loads((DESKTOP_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for relative, locked in sorted(lock.get("packages", {}).items()):
        if not relative or "node_modules/" not in relative:
            continue
        package_root = DESKTOP_ROOT / relative
        if not package_root.is_dir():
            continue
        package_json = package_root / "package.json"
        metadata: dict[str, Any] = {}
        if package_json.is_file():
            try:
                metadata = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        name = str(metadata.get("name") or relative.rsplit("node_modules/", 1)[-1])
        version = str(metadata.get("version") or locked.get("version") or "unknown")
        copied = copy_license_files(
            package_root,
            destination / f"{safe_name(name)}-{safe_name(version)}",
        )
        declared = metadata.get("license", locked.get("license", "UNKNOWN"))
        if isinstance(declared, dict):
            declared = declared.get("type", "UNKNOWN")
        records.append(
            {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "license": str(declared or "UNKNOWN"),
                "licenseDirectory": f"npm/{safe_name(name)}-{safe_name(version)}",
                "licenseFiles": [
                    f"npm/{safe_name(name)}-{safe_name(version)}/{file_name}"
                    for file_name in copied
                ],
                "developmentOnly": bool(locked.get("dev", False)),
            }
        )
    return records


def attach_spdx_texts(records: list[dict[str, Any]], licenses_root: Path) -> None:
    full_list_path = DESKTOP_ROOT / "node_modules" / "spdx-license-list" / "spdx-full.json"
    full_list = json.loads(full_list_path.read_text(encoding="utf-8"))
    aliases = {
        "BSD License": "BSD-2-Clause",
        "MIT License": "MIT",
        "Apache License, Version 2.0": "Apache-2.0",
    }
    spdx_destination = licenses_root / "SPDX"
    for record in records:
        declaration = aliases.get(str(record["license"]), str(record["license"]))
        if record["license"] == "BSD License":
            record["licenseEvidence"] = (
                "PyPI classifier is ambiguous; BSD-2-Clause mapping is retained "
                "for final rights review."
            )
        declaration_tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", declaration))
        identifiers = sorted(declaration_tokens.intersection(full_list))
        for identifier in identifiers:
            target = spdx_destination / f"{safe_name(identifier)}.txt"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    full_list[identifier]["licenseText"].rstrip() + "\n",
                    encoding="utf-8",
                )
            reference = f"SPDX/{target.name}"
            if reference not in record["licenseFiles"]:
                record["licenseFiles"].append(reference)
        record["license"] = aliases.get(str(record["license"]), str(record["license"]))


def attach_metadata_declarations(
    records: list[dict[str, Any]], licenses_root: Path
) -> None:
    """Retain a distributable declaration when a package ships no license file."""
    for record in records:
        if record["licenseFiles"]:
            continue
        relative = Path(record["licenseDirectory"]) / "DECLARED-LICENSE.txt"
        target = licenses_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"Package: {record['name']} {record['version']}\n"
            f"Ecosystem: {record['ecosystem']}\n"
            f"License declaration from package metadata: {record['license']}\n",
            encoding="utf-8",
        )
        record["licenseFiles"].append(relative.as_posix())
        record["licenseEvidence"] = (
            "The distributed package contains no standalone license file; this "
            "record preserves its package-metadata declaration."
        )


def run_sbom_tools(output: Path) -> None:
    uvx = shutil.which("uvx")
    npx = shutil.which("npx")
    if not uvx or not npx:
        raise RuntimeError("uvx and npx are required to generate release SBOMs")
    subprocess.run(  # noqa: S603 - executable and arguments are fixed release tooling
        [
            uvx,
            "--from",
            "cyclonedx-bom==7.3.1",
            "cyclonedx-py",
            "environment",
            sys.executable,
            "--output-format",
            "JSON",
            "--output-file",
            str(output / "sbom-python.cdx.json"),
            "--validate",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    subprocess.run(  # noqa: S603 - executable and arguments are fixed release tooling
        [
            npx,
            "--no-install",
            "cyclonedx-npm",
            "--gather-license-texts",
            "--output-reproducible",
            "--output-format",
            "JSON",
            "--output-file",
            str(output / "sbom-electron.cdx.json"),
            "--validate",
            str(DESKTOP_ROOT / "package.json"),
        ],
        cwd=DESKTOP_ROOT,
        check=True,
    )


def build(output: Path) -> None:
    if output.resolve() != DEFAULT_OUTPUT.resolve():
        raise ValueError(f"output must be {DEFAULT_OUTPUT}")
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(PROJECT_ROOT / name, output / name)

    licenses_root = output / "licenses"
    records = collect_python_licenses(licenses_root / "python")
    records.extend(collect_node_licenses(licenses_root / "npm"))
    attach_spdx_texts(records, licenses_root)
    attach_metadata_declarations(records, licenses_root)
    (output / "THIRD_PARTY_LICENSE_INDEX.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    missing_files = [item for item in records if not item["licenseFiles"]]
    (output / "README.txt").write_text(
        "EquiSeek desktop compliance bundle\n"
        "\n"
        "LICENSE and NOTICE cover EquiSeek. THIRD_PARTY_NOTICES.md is the curated notice.\n"
        "licenses/ contains discovered license, copying, notice, and copyright texts.\n"
        "THIRD_PARTY_LICENSE_INDEX.json maps locked packages to declarations and files.\n"
        "sbom-*.cdx.json are CycloneDX 1.6 software bills of materials.\n"
        f"Packages indexed: {len(records)}; packages without a standalone license file: "
        f"{len(missing_files)}.\n",
        encoding="utf-8",
    )
    run_sbom_tools(output)
    artifacts = {
        path.name: sha256(path)
        for path in sorted(output.iterdir())
        if path.is_file()
    }
    manifest = {
        "product": "EquiSeek",
        "version": "0.2.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "indexedPackages": len(records),
        "packagesWithoutStandaloneLicenseFile": len(missing_files),
        "artifactsSha256": artifacts,
    }
    (output / "COMPLIANCE-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
