# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for WFRP4e → The Old World Converter
#
# Build commands (run from repo root):
#   macOS  : pyinstaller wfrp_tow_converter.spec
#   Windows: pyinstaller wfrp_tow_converter.spec
#   Linux  : pyinstaller wfrp_tow_converter.spec
#
# Output is placed in dist/

import sys
import os
from pathlib import Path

ROOT = Path(SPECPATH)

# Platform-specific icon
if sys.platform == "darwin":
    ICON = str(ROOT / "assets" / "icon.icns")
elif sys.platform == "win32":
    ICON = str(ROOT / "assets" / "icon.ico")
else:
    ICON = str(ROOT / "assets" / "icon.png")

a = Analysis(
    [str(ROOT / "wfrp_to_tow_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: produce a .app bundle
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="WFRP4e to TOW Converter",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="WFRP4e to TOW Converter",
    )
    app = BUNDLE(
        coll,
        name="WFRP4e to TOW Converter.app",
        icon=ICON,
        bundle_identifier="com.mythictalesllc.wfrp-tow-converter",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion":            "1.0.0",
            "NSHighResolutionCapable":    True,
            "LSMinimumSystemVersion":     "11.0",
        },
    )

else:
    # Windows / Linux: single-file executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="WFRP4e_to_TOW_Converter",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,     # no terminal window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
