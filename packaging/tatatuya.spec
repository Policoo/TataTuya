# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller definition for the unsigned Apple Silicon application."""

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(SPEC).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "src"
PROJECT_METADATA = tomllib.loads(
    (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]
VERSION = PROJECT_METADATA["version"]

UI_ROOT = SOURCE_ROOT / "tatatuya" / "ui"
DATA_FILES = [
    (str(UI_ROOT / "styles.qss"), "tatatuya/ui"),
    (str(UI_ROOT / "icons"), "tatatuya/ui/icons"),
]

analysis = Analysis(
    [str(SOURCE_ROOT / "tatatuya" / "__main__.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=["tatatuya.infrastructure.migrations"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TataTuya",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="TataTuya",
)
application = BUNDLE(
    collection,
    name="TataTuya.app",
    bundle_identifier="ro.tatatuya.app",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "TataTuya",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": True,
    },
)
