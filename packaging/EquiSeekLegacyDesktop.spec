# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import sys

from PyInstaller.utils.hooks import collect_all


datas, binaries, hiddenimports = collect_all("baostock")
datas += [
    ("../LICENSE", "."),
    ("../THIRD_PARTY_NOTICES.md", "."),
    ("../src/aegisrun/builtin_skills", "aegisrun/builtin_skills"),
]

analysis = Analysis(
    ["desktop_entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "aiosqlite",
        "alembic",
        "asyncpg",
        "docker",
        "fastapi",
        "jsonschema",
        "langgraph",
        "opentelemetry",
        "psycopg",
        "pytest",
        "rich",
        "sqlalchemy",
        "typer",
        "uvicorn",
    ],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EquiSeekLegacy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
application_files = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EquiSeekLegacy",
)

if sys.platform == "darwin":
    application = BUNDLE(
        application_files,
        name="EquiSeek Legacy.app",
        icon=None,
        version="0.2.0",
        bundle_identifier="ai.equiseek.legacy",
        info_plist={
            "CFBundleDisplayName": "EquiSeek Legacy",
            "CFBundleVersion": "0.2.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
