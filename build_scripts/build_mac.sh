#!/usr/bin/env bash
# build_mac.sh — Build WFRP4e → TOW Converter for macOS
#
# Requirements:
#   pip install pyinstaller pillow
#
# Output:
#   dist/WFRP4e to TOW Converter.app   — runnable app bundle
#   dist/WFRP4e_to_TOW_Converter_mac.zip — zipped for distribution
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Cleaning previous build..."
rm -rf build dist

echo "==> Running PyInstaller..."
# Use the framework Python so Tkinter has full display access
/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python \
    -m PyInstaller wfrp_tow_converter.spec --noconfirm

echo "==> Zipping .app bundle..."
cd dist
zip -r "WFRP4e_to_TOW_Converter_mac.zip" "WFRP4e to TOW Converter.app"
cd ..

echo ""
echo "✓ Build complete."
echo "  App   : dist/WFRP4e to TOW Converter.app"
echo "  Zip   : dist/WFRP4e_to_TOW_Converter_mac.zip"
