#!/usr/bin/env bash
# build_linux.sh — Build WFRP4e → TOW Converter for Linux
#
# Requirements:
#   pip install pyinstaller pillow
#   sudo apt install python3-tk   (or equivalent for your distro)
#
# Output:
#   dist/WFRP4e_to_TOW_Converter     — single binary
#   dist/WFRP4e_to_TOW_Converter_linux.tar.gz
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "==> Cleaning previous build..."
rm -rf build dist

echo "==> Running PyInstaller..."
pyinstaller wfrp_tow_converter.spec --noconfirm

echo "==> Packaging binary..."
cd dist
tar -czf "WFRP4e_to_TOW_Converter_linux.tar.gz" "WFRP4e_to_TOW_Converter"
cd ..

echo ""
echo "✓ Build complete."
echo "  Binary : dist/WFRP4e_to_TOW_Converter"
echo "  Archive: dist/WFRP4e_to_TOW_Converter_linux.tar.gz"
